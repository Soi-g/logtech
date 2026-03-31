"""
AWS Observability Agent - Tool 및 Sub-agent 정의

obs-agent 스타일로 전면 재작성:
- 파라미터화된 툴 (fetch_amp_metric, fetch_logs, fetch_traces)
- infrastructure_agent 추가 (EC2/RDS/CloudWatch/CloudTrail/ELB/ASG)
- sub-agent 프롬프트 obs-agent 스타일 적용

환경변수:
    AMP_ENDPOINT        - AMP workspace endpoint
    OPENSEARCH_ENDPOINT - OpenSearch domain endpoint
    OPENSEARCH_USER     - OpenSearch 사용자
    OPENSEARCH_PASSWORD - OpenSearch 비밀번호
    AWS_REGION_NAME     - AWS 리전 (기본: ap-northeast-2)
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import boto3
from strands import Agent, tool
from strands.models import BedrockModel


try:
    from cw_logger import cw_log
except Exception:
    def cw_log(msg): pass


def _log(msg: str):
    """print + cw_log 동시 출력 (Lambda/AgentCore 양쪽 로그 그룹에서 확인 가능)"""
    print(msg)
    cw_log(msg)


# ============================================================
# Raw tool output 캡처 — Collector 할루시네이션 방지용
# Collector가 tool 반환값을 요약하는 과정에서 수치를 왜곡하는 문제를
# 해결하기 위해 원본 반환값을 별도로 저장하여 Writer에 직접 전달함
# ============================================================
import threading as _threading
_raw_outputs: dict = {}
_raw_outputs_lock = _threading.Lock()


def clear_raw_outputs():
    """Collector 호출 전 이전 결과 초기화"""
    with _raw_outputs_lock:
        _raw_outputs.clear()


def get_raw_outputs() -> dict:
    """Collector 호출 후 tool 원본 반환값 조회"""
    with _raw_outputs_lock:
        return dict(_raw_outputs)


# ============================================================
# 환경변수
# ============================================================
AMP_ENDPOINT = os.environ.get("AMP_ENDPOINT", "")
print(f"[agents_aws] loaded, AMP_ENDPOINT={AMP_ENDPOINT[:40] if AMP_ENDPOINT else 'NOT SET'}")
OS_ENDPOINT  = os.environ.get("OPENSEARCH_ENDPOINT", "")
OS_USER      = os.environ.get("OPENSEARCH_USER", "admin")
OS_PASSWORD  = os.environ.get("OPENSEARCH_PASSWORD", "")
AWS_REGION   = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")

# Athena 설정
ATHENA_DB          = os.environ.get("ATHENA_DATABASE", "log_platform_dev_observability")
ATHENA_OUTPUT      = os.environ.get("ATHENA_OUTPUT_BUCKET", "")  # s3://bucket/athena-results/
LOGS_BUCKET        = os.environ.get("LOGS_BUCKET", "log-platform-dev-logs-backup-347751175815")
TRACES_BUCKET      = os.environ.get("TRACES_BUCKET", "log-platform-dev-traces-backup-347751175815")


# ============================================================
# AMP / OpenSearch 스키마 (AI 컨텍스트용)
# ============================================================
AMP_SCHEMA = """
## AMP(Prometheus) 메트릭 스키마

### 라벨 규칙
- job 라벨 = service.name/service.namespace 조합 (예: job="todolist/springboot", job="todolist/flask", job="todolist/thymeleaf")
- Flask job 이름: "todolist/flask" (todoui-flask 아님)
- job="springboot" 단독은 존재하지 않음. 반드시 "todolist/springboot" 등 전체값 사용.
- deployment_environment: "dev" = VM/Docker, "prod" = EC2/AWS
- source: 데이터 수집 경로 구분 — "app"(OTLP/앱 SDK), "container"(docker_stats), "host"(hostmetrics), "sys"(syslog)
  - http_server_request_duration_seconds_count 등 앱 메트릭은 source="app" 고정
  - 파생 메트릭(job:http_4xx_error_ratio:rate5m 등)에도 source 라벨 보존됨
- 반드시 job 라벨로 서비스를 필터할 것. service_name 라벨도 존재하나 job 우선 사용.

### 파생 메트릭 (Recording Rules — 우선 사용)
http:  job:http_4xx_error_ratio:rate5m       (4xx 에러율, job+deployment_environment+source 라벨)
       job:http_5xx_error_ratio:rate5m       (5xx 에러율, job+deployment_environment+source 라벨)
app:   app_http_server_requests_5m           (총 요청 수)
       app_http_server_5xx_errors_5m         (5xx 에러 건수)
       app_http_server_4xx_errors_5m         (4xx 에러 건수)
       app_http_server_5xx_error_ratio_5m    (5xx 에러율)
       app_http_server_4xx_error_ratio_5m    (4xx 에러율)
       app_http_server_latency_p95_5m        (P95 응답시간)
  ※ 모든 app_* 파생 메트릭은 source, deployment_environment, service_namespace, service_name 라벨 포함
infra: app_container_cpu_utilization_avg_5m, app_container_memory_utilization_avg_5m
jvm:   app_jvm_cpu_utilization_avg_5m, app_jvm_memory_used_avg_5m,
       app_jvm_gc_count_5m, app_jvm_gc_duration_p95_5m

### 4xx vs 5xx 해석 기준
- 4xx 에러율 상승 + 5xx 정상 → 클라이언트 잘못된 요청 (False Positive 가능성)
- 5xx 에러율 상승 → 서버/인프라 장애 가능성, 추가 조사 필요
- 두 지표 모두 조회해서 비교할 것

### http_route 라벨 — 4xx/5xx 원인 경로 드릴다운
- http_server_request_duration_seconds_count에는 http_route, http_response_status_code 라벨 존재
- 4xx/5xx 알람 발생 시 반드시 route별 분석으로 어떤 경로에서 에러가 나는지 특정할 것
- 드릴다운 쿼리 (job 값은 실제 서비스에 맞게 변경):
  topk(5, sum by (http_route, http_response_status_code)(
    rate(http_server_request_duration_seconds_count{job="...", http_response_status_code=~"4.."}[5m])
  ))
- 해석 기준:
  - 특정 route에만 4xx 집중 → 해당 경로 문제 (없는 경로 요청, 인증 오류 등)
  - 모든 route에 고르게 4xx → 공통 미들웨어/인증/설정 문제
  - 404가 대부분 → 존재하지 않는 경로 요청 (클라이언트 버그, 잘못된 URL)
  - 401/403 → 인증/권한 문제
  - 400 → 잘못된 요청 형식

### Raw 메트릭 (파생 없을 때)
http:      http_server_request_duration_seconds_count{http_route, http_response_status_code 라벨 포함}
           http_server_request_duration_seconds_bucket
jvm:       jvm_memory_used_bytes, jvm_thread_count, jvm_gc_duration_seconds_bucket
container: container_cpu_utilization_ratio, container_memory_usage_total_bytes
db:        db_client_connections_usage, db_client_connections_max

### 단위
app_container_cpu_utilization_avg_5m, app_jvm_cpu_utilization_avg_5m: 퍼센트
app_jvm_memory_used_avg_5m: bytes
"""

LOGS_SCHEMA = """
## OpenSearch 로그/트레이스 스키마

### 인덱스
- logs-app : OTel로 수집된 앱 로그
- logs-host: 호스트/syslog (service.name="host-logs" 고정)
- traces-app: OTel 분산 트레이스

### 환경
- "dev"  : VM/Docker 로컬 환경
- "prod" : EC2/AWS 고객 환경

### logs-app 주요 필드
- @timestamp: 시간 (epoch_millis 또는 ISO)
- body: 로그 메시지 본문
- severity.text: INFO / WARN / ERROR / SEVERE
  ※ Java(Spring Boot)는 ERROR 대신 SEVERE 사용 → fetch_logs가 자동으로 둘 다 조회
- traceId, spanId: traces-app과 연계 키
- resource.service.name, resource.deployment.environment

### traces-app 주요 필드
- startTime: ISO 문자열 (시간 필터 기준, @timestamp 아님)
- name: span 이름, kind: Client/Server/Internal
- status.code: "Unset"(정상), "Ok"(명시적 성공), "Error"(에러)
- traceId, spanId, parentSpanId
"""


# ============================================================
# AMP / OpenSearch 헬퍼
# ============================================================
def _amp_base_url() -> str:
    base = AMP_ENDPOINT.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    return base


def _amp_signed_request(url: str) -> dict:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()
    aws_req = AWSRequest(method="GET", url=url)
    SigV4Auth(creds, "aps", AWS_REGION).add_auth(aws_req)
    req = urllib.request.Request(url, headers=dict(aws_req.headers))
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def query_opensearch(index: str, query: dict) -> dict:
    try:
        creds = base64.b64encode(f"{OS_USER}:{OS_PASSWORD}".encode()).decode()
        url = f"https://{OS_ENDPOINT}/{index}/_search"
        payload = json.dumps(query).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Basic {creds}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        _log(f"[query_opensearch 에러] {e}")
        return {"error": str(e), "hits": {"hits": []}}


# ============================================================
# boto3 클라이언트 팩토리
# ============================================================
def _cw():    return boto3.client("cloudwatch",  region_name=AWS_REGION)
def _logs():  return boto3.client("logs",         region_name=AWS_REGION)
def _rds():   return boto3.client("rds",          region_name=AWS_REGION)
def _ec2():   return boto3.client("ec2",          region_name=AWS_REGION)
def _elb():   return boto3.client("elbv2",        region_name=AWS_REGION)
def _asg():   return boto3.client("autoscaling",  region_name=AWS_REGION)
def _trail(): return boto3.client("cloudtrail",   region_name=AWS_REGION)


# ============================================================
# AMP / OpenSearch 파라미터화 툴 (obs-agent 스타일)
# ============================================================

@tool
def fetch_amp_metric(promql: str, last_minutes: int = 0) -> str:
    """AMP(Amazon Managed Prometheus)에서 PromQL 쿼리로 메트릭 조회.
    last_minutes=0이면 instant query(현재값), >0이면 range query(시계열).
    서비스 필터는 job 라벨 사용. 예: app_http_server_error_ratio_5m{job="todolist/springboot",deployment_environment="dev"}
    job 라벨은 반드시 전체값 사용 (job="todolist/springboot", job="todolist/thymeleaf", job="todolist/todoui-flask")
    """ + AMP_SCHEMA
    try:
        q = urllib.parse.quote(promql, safe="")
        if last_minutes > 0:
            end = int(time.time())
            start = end - last_minutes * 60
            url = f"{_amp_base_url()}/query_range?query={q}&start={start}&end={end}&step=60"
        else:
            url = f"{_amp_base_url()}/query?query={q}"
        out = _amp_signed_request(url)
        # 데이터 신선도 정보 추가 (range query인 경우만)
        if last_minutes > 0:
            results = out.get("data", {}).get("result", [])
            now_ts = int(time.time())
            last_ts = None
            for series in results:
                values = series.get("values", [])
                if values:
                    candidate = values[-1][0]
                    if last_ts is None or candidate > last_ts:
                        last_ts = candidate
            if last_ts is not None:
                out["_meta"] = {
                    "current_unix_time": now_ts,
                    "last_data_timestamp": last_ts,
                    "last_data_age_seconds": now_ts - last_ts,
                }
            else:
                # 데이터가 전혀 없음 — 서비스 다운 또는 스크래핑 중단
                out["_meta"] = {
                    "current_unix_time": now_ts,
                    "last_data_timestamp": None,
                    "last_data_age_seconds": None,
                    "no_data": True,
                }
        preview = json.dumps(out.get("data", {}).get("result", [])[:2], ensure_ascii=False)[:300]
        _log(f"[TOOL] fetch_amp_metric promql={promql!r} last_minutes={last_minutes} status={out.get('status','ok')} preview={preview}")
        return json.dumps(out, ensure_ascii=False)[:4000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_logs(
    service: str,
    environment: str,
    index: str = "logs-app",
    severity: str = "",
    filter_text: str = "",
    last_minutes: int = 10,
    size: int = 20,
) -> str:
    """OpenSearch에서 로그 조회. index: logs-app(앱) 또는 logs-host(호스트).
    severity: ERROR(Java의 SEVERE 포함)/WARN/INFO.
    service = resource.service.name 값 (예: "springboot", "flask", "thymeleaf").
    environment = resource.deployment.environment 값 (예: "dev", "prod").
    """ + LOGS_SCHEMA
    try:
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - last_minutes * 60 * 1000
        must_filters: list = [
            {"range": {"@timestamp": {"gte": from_ms, "lte": now_ms, "format": "epoch_millis"}}},
            {"term": {"resource.service.name.keyword": service}},
            {"term": {"resource.deployment.environment.keyword": environment}},
        ]
        if severity:
            sev_upper = severity.upper()
            if sev_upper == "ERROR":
                must_filters.append({"bool": {"should": [
                    {"term": {"severity.text.keyword": "ERROR"}},
                    {"term": {"severity.text.keyword": "SEVERE"}},
                ], "minimum_should_match": 1}})
            else:
                must_filters.append({"term": {"severity.text.keyword": sev_upper}})
        if filter_text:
            must_filters.append({"match": {"body": filter_text}})
        target_index = index if index in ("logs-app", "logs-host") else "logs-app"
        resp = query_opensearch(target_index, {
            "size": size,
            "sort": [{"@timestamp": {"order": "desc"}}],
            "query": {"bool": {"filter": must_filters}},
        })
        if "error" in resp:
            err_msg = f"OpenSearch 조회 실패: {resp['error']}"
            _log(f"[TOOL] fetch_logs index={target_index} service={service} env={environment} -> ERROR: {resp['error']}")
            return json.dumps({"status": "error", "message": err_msg})
        hits = resp.get("hits", {}).get("hits", [])
        latest_log_ts = hits[0]["_source"].get("@timestamp") if hits else None
        now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # 최근 로그 신선도 경고: 마지막 로그가 5분 이상 이전이면 서비스 다운 의심
        log_age_warning = None
        if latest_log_ts:
            try:
                import datetime as _dt
                last_dt = _dt.datetime.strptime(latest_log_ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_dt.timezone.utc)
                age_sec = int((time.time() - last_dt.timestamp()))
                if age_sec > 300:
                    log_age_warning = f"경고: 마지막 로그가 {age_sec//60}분 {age_sec%60}초 전입니다. 서비스가 그 이후 로그를 생성하지 않음 = 서비스 다운 가능성 높음"
            except Exception:
                pass
        out = {
            "status": "success",
            "index": target_index,
            "count": len(hits),
            "current_time_utc": now_utc,
            "latest_log_timestamp": latest_log_ts,
            "log_age_warning": log_age_warning,
            "samples": [{
                "timestamp": h["_source"].get("@timestamp"),
                "severity": (h["_source"].get("severity") or {}).get("text"),
                "body": h["_source"].get("body"),
                "traceId": h["_source"].get("traceId"),
            } for h in hits],
        }
        sample = hits[0]["_source"].get("body", "")[:100] if hits else ""
        _log(f"[TOOL] fetch_logs index={target_index} service={service} env={environment} severity={severity or '(all)'} last_minutes={last_minutes} -> count={len(hits)}" + (f" sample={sample!r}" if sample else ""))
        return json.dumps(out, ensure_ascii=False)[:4000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_traces(
    service: str,
    environment: str,
    status_filter: str = "",
    last_minutes: int = 10,
) -> str:
    """OpenSearch traces-app에서 trace span 조회.
    status_filter=Error로 에러 span만 필터. 시간 필터는 startTime(ISO) 기준, @timestamp 사용 금지.
    service = resource.service.name 값 (예: "springboot").
    """ + LOGS_SCHEMA
    try:
        now_ms = int(time.time() * 1000)
        from_ms = now_ms - last_minutes * 60 * 1000
        from_iso = datetime.fromtimestamp(from_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        to_iso   = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        must_filters: list = [
            {"range": {"startTime": {"gte": from_iso, "lte": to_iso, "format": "strict_date_optional_time"}}},
            {"term": {"resource.service.name.keyword": service}},
            {"term": {"resource.deployment.environment.keyword": environment}},
        ]
        if status_filter:
            must_filters.append({"term": {"status.code.keyword": status_filter}})
        resp = query_opensearch("traces-app", {
            "size": 30,
            "sort": [{"startTime": {"order": "desc"}}],
            "query": {"bool": {"filter": must_filters}},
        })
        if "error" in resp:
            _log(f"[TOOL] fetch_traces service={service} env={environment} -> ERROR: {resp['error']}")
            return json.dumps({"status": "error", "message": f"OpenSearch 조회 실패: {resp['error']}"})
        hits = resp.get("hits", {}).get("hits", [])
        total_val = resp.get("hits", {}).get("total")
        total_count = total_val.get("value", len(hits)) if isinstance(total_val, dict) else len(hits)
        out = {
            "status": "success",
            "trace_count": total_count,
            "recent_spans": [{
                "startTime": h["_source"].get("startTime"),
                "traceId": h["_source"].get("traceId"),
                "name": h["_source"].get("name"),
                "kind": h["_source"].get("kind"),
                "status": h["_source"].get("status", {}),
            } for h in hits[:15]],
        }
        _log(f"[TOOL] fetch_traces service={service} env={environment} "
              f"status_filter={status_filter!r} last_minutes={last_minutes} -> spans={len(hits)} total={total_count}")
        return json.dumps(out, ensure_ascii=False)[:4000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ============================================================
# Infrastructure 툴 (obs-agent sub_agents.py 기반)
# ============================================================

@tool
def fetch_cloudwatch_metric(
    namespace: str,
    metric_name: str,
    dimensions: str,
    stat: str = "Average",
    last_minutes: int = 10,
) -> str:
    """AWS CloudWatch에서 메트릭 조회. RDS, EC2 등 AWS 관리형 서비스 메트릭에 사용.
    namespace: "AWS/RDS" | "AWS/EC2" | "AWS/ApplicationELB" 등
    metric_name 예시 (RDS): DatabaseConnections, CPUUtilization, FreeableMemory, ReadLatency, WriteLatency
    metric_name 예시 (EC2): CPUUtilization, StatusCheckFailed
    dimensions: JSON 문자열
      RDS: '[{"Name":"DBInstanceIdentifier","Value":"log-platform-dev-customer-postgres"}]'
      EC2: '[{"Name":"InstanceId","Value":"i-0123456789abcdef0"}]'
    stat: Average | Maximum | Minimum | Sum
    """
    try:
        dims = json.loads(dimensions)
        end_time = time.time()
        start_time = end_time - last_minutes * 60
        resp = _cw().get_metric_statistics(
            Namespace=namespace, MetricName=metric_name, Dimensions=dims,
            StartTime=start_time, EndTime=end_time, Period=60, Statistics=[stat],
        )
        datapoints = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
        result = {
            "status": "success",
            "namespace": namespace, "metric_name": metric_name, "stat": stat,
            "datapoints": [{"timestamp": str(d["Timestamp"]), "value": d.get(stat, d.get("Average", 0))} for d in datapoints[-10:]],
            "summary": {
                "count": len(datapoints),
                "latest": datapoints[-1].get(stat, datapoints[-1].get("Average")) if datapoints else None,
                "max": max((d.get(stat, d.get("Average", 0)) for d in datapoints), default=None),
            },
        }
        _log(f"[TOOL] fetch_cloudwatch_metric {namespace}/{metric_name} -> {len(datapoints)} points latest={result['summary']['latest']}")
        return json.dumps(result, ensure_ascii=False, default=str)[:3000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_cloudwatch_alarms(state: str = "ALARM", name_prefix: str = "") -> str:
    """현재 발생 중인 CloudWatch 알람 조회.
    state: "ALARM" | "OK" | "INSUFFICIENT_DATA" (기본값: "ALARM")
    name_prefix: 알람 이름 필터 (예: "log-platform-dev")
    """
    try:
        kwargs: Dict[str, Any] = {"StateValue": state}
        if name_prefix:
            kwargs["AlarmNamePrefix"] = name_prefix
        resp = _cw().describe_alarms(**kwargs)
        alarms = resp.get("MetricAlarms", [])
        result = {
            "status": "success", "state_filter": state, "count": len(alarms),
            "alarms": [{
                "name": a["AlarmName"], "state": a["StateValue"],
                "reason": a.get("StateReason", ""),
                "metric": f"{a.get('Namespace', '')}/{a.get('MetricName', '')}",
                "threshold": a.get("Threshold"),
                "state_updated": str(a.get("StateUpdatedTimestamp", "")),
            } for a in alarms[:20]],
        }
        _log(f"[TOOL] fetch_cloudwatch_alarms state={state} -> {len(alarms)} alarms")
        return json.dumps(result, ensure_ascii=False, default=str)[:3000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_cloudwatch_logs(
    log_group: str,
    filter_pattern: str = "",
    last_minutes: int = 30,
    limit: int = 20,
) -> str:
    """CloudWatch Logs에서 로그 이벤트 조회. RDS 에러 로그, Lambda 로그 등에 사용.
    log_group: CloudWatch 로그 그룹 이름
      RDS 예시: "/aws/rds/instance/log-platform-dev-customer-postgres/error"
      Lambda 예시: "/aws/lambda/log-platform-dev-observability-agent"
    filter_pattern: CloudWatch Logs 필터 패턴 (예: "ERROR", "Exception", "")
    """
    try:
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - last_minutes * 60 * 1000
        if filter_pattern:
            resp = _logs().filter_log_events(
                logGroupName=log_group, startTime=start_ms, endTime=end_ms,
                limit=limit, filterPattern=filter_pattern,
            )
            events = resp.get("events", [])
        else:
            streams = _logs().describe_log_streams(
                logGroupName=log_group, orderBy="LastEventTime", descending=True, limit=3,
            ).get("logStreams", [])
            events = []
            for stream in streams:
                e_resp = _logs().get_log_events(
                    logGroupName=log_group, logStreamName=stream["logStreamName"],
                    startTime=start_ms, endTime=end_ms, limit=limit // 3,
                )
                events.extend(e_resp.get("events", []))
        result = {
            "status": "success", "log_group": log_group, "count": len(events),
            "events": [{
                "timestamp": str(datetime.fromtimestamp(e["timestamp"] / 1000, tz=timezone.utc)),
                "message": e["message"][:200],
            } for e in sorted(events, key=lambda x: x["timestamp"], reverse=True)[:limit]],
        }
        _log(f"[TOOL] fetch_cloudwatch_logs group={log_group} pattern={filter_pattern!r} -> {len(events)} events")
        return json.dumps(result, ensure_ascii=False, default=str)[:4000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_rds_status(db_instance_identifier: str = "") -> str:
    """RDS 인스턴스 현재 상태 조회.
    db_instance_identifier가 비어있으면 모든 RDS 인스턴스 목록 반환.
    반환: DBInstanceStatus, Engine, InstanceClass, Endpoint, PendingModifiedValues
    """
    try:
        kwargs: Dict[str, Any] = {}
        if db_instance_identifier:
            kwargs["DBInstanceIdentifier"] = db_instance_identifier
        instances = _rds().describe_db_instances(**kwargs).get("DBInstances", [])
        result = {
            "status": "success",
            "instances": [{
                "identifier": i["DBInstanceIdentifier"],
                "status": i["DBInstanceStatus"],
                "engine": f"{i['Engine']} {i['EngineVersion']}",
                "instance_class": i["DBInstanceClass"],
                "multi_az": i["MultiAZ"],
                "endpoint": f"{i['Endpoint']['Address']}:{i['Endpoint']['Port']}" if i.get("Endpoint") else None,
                "pending_changes": i.get("PendingModifiedValues", {}),
            } for i in instances],
        }
        _log(f"[TOOL] fetch_rds_status db={db_instance_identifier or 'all'} -> {len(instances)} instances")
        return json.dumps(result, ensure_ascii=False, default=str)[:3000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_rds_events(db_instance_identifier: str = "", last_minutes: int = 60) -> str:
    """RDS 이벤트 이력 조회. 재시작, failover, 파라미터 변경, 백업 등 확인."""
    try:
        end_time = datetime.now(tz=timezone.utc)
        start_time = end_time - timedelta(minutes=last_minutes)
        kwargs: Dict[str, Any] = {"StartTime": start_time, "EndTime": end_time, "SourceType": "db-instance"}
        if db_instance_identifier:
            kwargs["SourceIdentifier"] = db_instance_identifier
        events = _rds().describe_events(**kwargs).get("Events", [])
        result = {
            "status": "success", "count": len(events),
            "events": [{
                "time": str(e.get("Date", "")),
                "source": e.get("SourceIdentifier", ""),
                "message": e.get("Message", ""),
            } for e in sorted(events, key=lambda x: x.get("Date", ""), reverse=True)[:20]],
        }
        _log(f"[TOOL] fetch_rds_events db={db_instance_identifier or 'all'} last={last_minutes}m -> {len(events)} events")
        return json.dumps(result, ensure_ascii=False, default=str)[:3000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_ec2_status(instance_ids: str = "", name_filter: str = "") -> str:
    """EC2 인스턴스 현재 상태 조회. 보안그룹 + health check 포함.
    instance_ids: 쉼표 구분 ID (예: "i-0123,i-0456")
    name_filter: Name 태그 패턴 (예: "*customer-backend*")
    """
    try:
        ec2 = _ec2()
        ids = [i.strip() for i in instance_ids.split(",") if i.strip()] if instance_ids else []
        filters = [{"Name": "tag:Name", "Values": [name_filter]}] if name_filter and not ids else []
        kwargs: Dict[str, Any] = {}
        if ids: kwargs["InstanceIds"] = ids
        if filters: kwargs["Filters"] = filters
        resp = ec2.describe_instances(**kwargs)

        instance_ids_list = [
            i["InstanceId"]
            for r in resp.get("Reservations", [])
            for i in r.get("Instances", [])
        ]
        health_map: Dict[str, str] = {}
        if instance_ids_list:
            for s in ec2.describe_instance_status(InstanceIds=instance_ids_list, IncludeAllInstances=True).get("InstanceStatuses", []):
                health_map[s["InstanceId"]] = f"system={s['SystemStatus']['Status']} instance={s['InstanceStatus']['Status']}"

        instances = []
        for r in resp.get("Reservations", []):
            for i in r.get("Instances", []):
                name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "")
                instances.append({
                    "instance_id": i["InstanceId"], "name": name,
                    "state": i["State"]["Name"],
                    "health_check": health_map.get(i["InstanceId"], "unknown"),
                    "instance_type": i["InstanceType"],
                    "private_ip": i.get("PrivateIpAddress"),
                    "public_ip": i.get("PublicIpAddress"),
                    "launch_time": str(i.get("LaunchTime", "")),
                    "security_groups": [{"id": sg["GroupId"], "name": sg["GroupName"]} for sg in i.get("SecurityGroups", [])],
                })
        result = {"status": "success", "instances": instances}
        _log(f"[TOOL] fetch_ec2_status filter={instance_ids or name_filter or 'all'} -> {len(instances)} instances")
        return json.dumps(result, ensure_ascii=False, default=str)[:3000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_cloudtrail_events(
    resource_name: str = "",
    event_names: str = "",
    last_minutes: int = 60,
) -> str:
    """CloudTrail에서 최근 API 변경 이력 조회. 누가 언제 무엇을 변경했는지 추적.
    resource_name: 리소스 이름 (예: "log-platform-dev-customer-postgres")
    event_names: 쉼표 구분 API 이름 (예: "ModifyDBInstance,RebootDBInstance")
      SG 변경: "AuthorizeSecurityGroupIngress,RevokeSecurityGroupIngress"
      EC2 변경: "StopInstances,StartInstances,RebootInstances"
    """
    try:
        end_time = datetime.now(tz=timezone.utc)
        start_time = end_time - timedelta(minutes=last_minutes)
        all_events: List[Dict] = []
        if event_names:
            for name in [n.strip() for n in event_names.split(",")]:
                resp = _trail().lookup_events(
                    LookupAttributes=[{"AttributeKey": "EventName", "AttributeValue": name}],
                    StartTime=start_time, EndTime=end_time, MaxResults=10,
                )
                all_events.extend(resp.get("Events", []))
        elif resource_name:
            resp = _trail().lookup_events(
                LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": resource_name}],
                StartTime=start_time, EndTime=end_time, MaxResults=20,
            )
            all_events = resp.get("Events", [])
        else:
            resp = _trail().lookup_events(StartTime=start_time, EndTime=end_time, MaxResults=20)
            all_events = resp.get("Events", [])
        result = {
            "status": "success", "count": len(all_events),
            "events": [{
                "time": str(e.get("EventTime", "")),
                "event_name": e.get("EventName", ""),
                "username": e.get("Username", ""),
                "resources": [r.get("ResourceName", "") for r in e.get("Resources", [])],
            } for e in sorted(all_events, key=lambda x: x.get("EventTime", ""), reverse=True)[:20]],
        }
        _log(f"[TOOL] fetch_cloudtrail_events resource={resource_name!r} events={event_names!r} -> {len(all_events)} events")
        return json.dumps(result, ensure_ascii=False, default=str)[:3000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_elb_health(load_balancer_name: str = "", target_group_name: str = "") -> str:
    """ALB/NLB target health 조회. 뒷단 EC2 인스턴스 health check 상태 확인.
    load_balancer_name: LB 이름 일부 (예: "log-platform-dev")
    target_group_name: target group 이름 일부 (예: "springboot")
    """
    try:
        elb = _elb()
        lb_kwargs: Dict[str, Any] = {}
        if load_balancer_name:
            lb_kwargs["Names"] = [load_balancer_name]
        lbs = elb.describe_load_balancers(**lb_kwargs).get("LoadBalancers", [])
        result_data: List[Dict] = []
        for lb in lbs[:5]:
            for tg in elb.describe_target_groups(LoadBalancerArn=lb["LoadBalancerArn"]).get("TargetGroups", []):
                if target_group_name and target_group_name.lower() not in tg["TargetGroupName"].lower():
                    continue
                health = elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
                result_data.append({
                    "lb_name": lb["LoadBalancerName"], "lb_state": lb["State"]["Code"],
                    "tg_name": tg["TargetGroupName"],
                    "targets": [{
                        "id": t["Target"]["Id"],
                        "health": t["TargetHealth"]["State"],
                        "reason": t["TargetHealth"].get("Reason", ""),
                    } for t in health.get("TargetHealthDescriptions", [])],
                })
        _log(f"[TOOL] fetch_elb_health lb={load_balancer_name!r} -> {len(result_data)} target groups")
        return json.dumps({"status": "success", "load_balancers": result_data}, ensure_ascii=False, default=str)[:3000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def fetch_autoscaling_activity(asg_name: str = "", last_minutes: int = 60) -> str:
    """Auto Scaling 그룹의 스케일링 활동 이력 조회."""
    try:
        end_time = datetime.now(tz=timezone.utc)
        start_time = end_time - timedelta(minutes=last_minutes)
        kwargs: Dict[str, Any] = {}
        if asg_name:
            kwargs["AutoScalingGroupName"] = asg_name
        asgs = _asg().describe_auto_scaling_groups(**kwargs).get("AutoScalingGroups", [])
        all_activities: List[Dict] = []
        for asg in asgs[:5]:
            acts = _asg().describe_scaling_activities(AutoScalingGroupName=asg["AutoScalingGroupName"], MaxRecords=20)
            for a in acts.get("Activities", []):
                if a.get("StartTime") and a["StartTime"] >= start_time:
                    all_activities.append({
                        "asg_name": asg["AutoScalingGroupName"],
                        "desired": asg["DesiredCapacity"], "current": len(asg["Instances"]),
                        "time": str(a.get("StartTime", "")),
                        "cause": a.get("Cause", ""), "status": a.get("StatusCode", ""),
                    })
        _log(f"[TOOL] fetch_autoscaling_activity asg={asg_name or 'all'} -> {len(all_activities)} activities")
        return json.dumps({
            "status": "success", "asg_count": len(asgs),
            "activities": sorted(all_activities, key=lambda x: x["time"], reverse=True)[:20],
        }, ensure_ascii=False, default=str)[:3000]
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ============================================================
# Sub-agent 프롬프트 (obs-agent 스타일)
# ============================================================

METRICS_AGENT_PROMPT = """You are a Metrics Specialist. Retrieve and summarize metric data.

Data sources:
1. AMP (OTel metrics) — fetch_amp_metric
   - App/container/host/JVM metrics from OTel collector
   - Environment label: deployment_environment="dev" (VM/Docker) or deployment_environment="prod" (EC2/AWS)
   - Job label = service name (전체값 사용): job="todolist/springboot", job="todolist/thymeleaf", job="todolist/flask"
   - job="springboot" 단독은 AMP에 존재하지 않음. 반드시 "todolist/springboot" 등 전체값 사용.
   - 파생 메트릭 우선 사용, 없으면 raw 메트릭 시도
   - 파생 메트릭 목록 (우선순위 순):
     app_http_server_5xx_error_ratio_5m, app_http_server_4xx_error_ratio_5m
     app_http_server_5xx_errors_5m, app_http_server_4xx_errors_5m
     app_http_server_requests_5m, app_http_server_latency_p95_5m
     app_jvm_cpu_utilization_avg_5m, app_jvm_memory_used_avg_5m
     app_container_cpu_utilization_avg_5m, app_container_memory_utilization_avg_5m

2. CloudWatch — fetch_cloudwatch_metric
   - AWS 관리형 서비스 메트릭 (RDS, EC2)
   - RDS: namespace="AWS/RDS", dimension DBInstanceIdentifier (예: "log-platform-dev-customer-postgres")
   - EC2: namespace="AWS/EC2", dimension InstanceId

3. CloudWatch Alarms — fetch_cloudwatch_alarms
   - 현재 ALARM 상태인 알람 목록 확인 시 사용

Decision rules:
- OTel 앱/JVM/컨테이너 메트릭 → fetch_amp_metric (파생 메트릭 먼저)
- RDS/EC2 AWS-level 메트릭 → fetch_cloudwatch_metric
- AMP 빈 결과 + prod 환경 → CloudWatch도 시도
- "현재 알람" 또는 "어떤 알람 발생 중" → fetch_cloudwatch_alarms
- "에러율" 단독 요청 시 → 4xx + 5xx 둘 다 조회 (app_http_server_4xx_error_ratio_5m + app_http_server_5xx_error_ratio_5m)

LIMIT: 최대 2회 tool 호출. 2회 안에 핵심 수치를 확보하고 결과를 반환하라.
Always return actual numeric values with units. (max 800 tokens)"""


LOGS_AGENT_PROMPT = """You are a Logs & Traces Specialist. Retrieve and summarize log and trace data.

Tools:
1. fetch_logs (OpenSearch logs-app, logs-host)
   - OTel로 수집된 앱 로그 및 호스트 syslog
   - Java(Spring Boot) uses SEVERE instead of ERROR → 이미 자동으로 둘 다 조회됨
   - Environment: "dev"=VM, "prod"=EC2
   - logs-host: service="host-logs" (고정)

2. fetch_traces (OpenSearch traces-app)
   - OTel로 수집된 분산 트레이스
   - status_filter="Error"로 에러 span만 필터
   - traceId로 logs와 연계 가능

3. fetch_cloudwatch_logs (CloudWatch Logs)
   - OTel에 없는 AWS 관리형 서비스 로그 (RDS 에러 로그, Lambda 로그 등)
   - RDS 에러: "/aws/rds/instance/{db-identifier}/error"
   - Lambda 로그: "/aws/lambda/{function-name}"

Tool selection rules:
- query에 "트레이스", "traces", "span", "분산 추적" 중 하나라도 포함 → fetch_traces 사용
- query에 에러 트레이스 확인 요청 → fetch_traces(status_filter="Error") 사용
- 위 조건 해당 없으면 → fetch_logs 사용 (fetch_traces 호출 금지)
- RDS 내부 에러 로그 확인 시 → fetch_cloudwatch_logs 사용

Other rules:
- 로그 0건이면 last_minutes 늘려서 재시도 1회만 허용 (10 → 30)
- logs-host의 인프라/OS 로그는 무시하고 앱 로그만 보고할 것

LIMIT: 최대 3회 tool 호출. 3회 안에 핵심 로그/트레이스를 확보하고 결과를 반환하라.
Return concise summary. (max 800 tokens)"""


INFRASTRUCTURE_AGENT_PROMPT = """You are an Infrastructure Specialist. Check current state and history of AWS resources.

Tools:
- fetch_ec2_status: EC2 상태 + 보안그룹 + health check
- fetch_rds_status: RDS 현재 상태 (running/failed, endpoint, pending changes)
- fetch_rds_events: RDS 이벤트 이력 (재시작, failover, 파라미터 변경)
- fetch_cloudwatch_alarms: 현재 발생 중인 CloudWatch 알람
- fetch_cloudtrail_events: API 변경 이력 (누가 언제 무엇을 변경했는지)
- fetch_elb_health: ALB/NLB target health check 상태
- fetch_autoscaling_activity: Auto Scaling 스케일링 이벤트 이력
- fetch_cloudwatch_metric: CloudWatch 메트릭 (RDS CPU/connections 등)

Customer stack (prod environment) resource naming:
- RDS identifier: "log-platform-dev-customer-postgres"
- EC2 frontend Name tag: "*customer-frontend*"
- EC2 backend Name tag: "*customer-backend*"

Investigation approach:
1. 현재 상태 → fetch_ec2_status, fetch_rds_status
2. 최근 이벤트/변경 → fetch_rds_events, fetch_cloudtrail_events
3. 알람 상태 → fetch_cloudwatch_alarms
4. LB 상태 → fetch_elb_health (ALB 있을 때)

CloudTrail 활용 예시:
- SG 변경: event_names="AuthorizeSecurityGroupIngress,RevokeSecurityGroupIngress"
- RDS 변경: event_names="ModifyDBInstance,RebootDBInstance"
- EC2 재시작: event_names="StopInstances,StartInstances,RebootInstances"

LIMIT: 최대 3회 tool 호출. 요청된 리소스만 확인하고 결과를 반환하라. 광범위 스캔 금지.
Conclude HEALTHY or UNHEALTHY with specific evidence. (max 1000 tokens)"""


# ============================================================
# Strands 모델
# ============================================================
_model = BedrockModel(
    model_id=os.environ.get("ANALYSIS_MODEL_ID", "apac.anthropic.claude-sonnet-4-20250514-v1:0"),
    region_name=AWS_REGION,
    streaming=False,
)


# ============================================================
# Sub-agent 인스턴스 (모듈 레벨 싱글턴)
# ============================================================
_metrics_agent_instance = Agent(
    name="metrics_agent",
    model=_model,
    system_prompt=METRICS_AGENT_PROMPT,
    tools=[fetch_amp_metric, fetch_cloudwatch_metric, fetch_cloudwatch_alarms],
    callback_handler=None,
)

_logs_agent_instance = Agent(
    name="logs_agent",
    model=_model,
    system_prompt=LOGS_AGENT_PROMPT,
    tools=[fetch_logs, fetch_traces, fetch_cloudwatch_logs],
    callback_handler=None,
)

_infrastructure_agent_instance = Agent(
    name="infrastructure_agent",
    model=_model,
    system_prompt=INFRASTRUCTURE_AGENT_PROMPT,
    tools=[
        fetch_ec2_status, fetch_rds_status, fetch_rds_events,
        fetch_cloudwatch_alarms, fetch_cloudtrail_events,
        fetch_elb_health, fetch_autoscaling_activity, fetch_cloudwatch_metric,
    ],
    callback_handler=None,
)


# ============================================================
# Collector용 @tool 래퍼 (Collector Agent가 호출하는 Sub-agents)
# ============================================================

@tool
def metrics_agent(query: str) -> str:
    """AMP(OTel) 또는 CloudWatch에서 메트릭을 조회하는 전문 에이전트.

    사용 시나리오:
    - OTel 앱/JVM/컨테이너 메트릭: "springboot dev 환경 최근 10분 HTTP 에러율과 latency 확인"
    - RDS CloudWatch 메트릭: "prod RDS 최근 10분 커넥션 수와 CPU 확인"
    - 현재 알람: "현재 ALARM 상태인 CloudWatch 알람 목록 확인"

    query에 포함할 내용: 서비스명, 환경(dev/prod), 시간 범위, 원하는 메트릭 종류
    """
    _log(f"[SUB-AGENT] metrics_agent query={query!r}")
    try:
        result = _metrics_agent_instance(query)
        text = str(result)
        # 원본 반환값 저장 — Writer에 직접 전달하여 Collector 요약 왜곡 방지
        with _raw_outputs_lock:
            _raw_outputs["metrics_agent"] = text[:3000]
        _log(f"[TOOL_RAW] metrics_agent result={text[:2000]}")
        return text[:4000]
    except Exception as e:
        err = json.dumps({"status": "error", "message": str(e)})
        with _raw_outputs_lock:
            _raw_outputs["metrics_agent"] = err
        return err


@tool
def logs_agent(query: str) -> str:
    """OpenSearch(OTel) 또는 CloudWatch Logs에서 로그와 트레이스를 조회하는 전문 에이전트.

    사용 시나리오:
    - 앱 에러 로그: "springboot dev 환경 최근 10분 ERROR 로그 확인"
    - 트레이스: "springboot dev 최근 10분 에러 트레이스 확인"
    - RDS 에러 로그: "prod RDS 에러 로그 최근 30분 확인"
    - Lambda 로그: "log-platform-dev-observability-agent Lambda 로그 확인"

    query에 포함할 내용: 서비스명, 환경(dev/prod), 시간 범위, 로그 레벨/키워드
    """
    _log(f"[SUB-AGENT] logs_agent query={query!r}")
    try:
        result = _logs_agent_instance(query)
        text = str(result)
        with _raw_outputs_lock:
            _raw_outputs["logs_agent"] = text[:3000]
        _log(f"[TOOL_RAW] logs_agent result={text[:2000]}")
        return text[:4000]
    except Exception as e:
        err = json.dumps({"status": "error", "message": str(e)})
        with _raw_outputs_lock:
            _raw_outputs["logs_agent"] = err
        return err


@tool
def infrastructure_agent(query: str) -> str:
    """AWS 인프라 현재 상태와 변경 이력을 조회하는 전문 에이전트.

    사용 시나리오:
    - EC2/RDS 상태: "prod EC2 frontend/backend 실행 상태 확인"
    - RDS 이벤트: "prod RDS 최근 1시간 이벤트 이력 확인 (재시작, failover 등)"
    - 변경 이력: "prod RDS 최근 수정/변경 내역 CloudTrail로 확인"
    - 알람: "현재 발생 중인 CloudWatch 알람 확인"
    - ELB: "prod ALB target health check 상태 확인"

    query에 포함할 내용: 리소스 종류, 환경(prod), 확인할 상태 또는 증상
    """
    _log(f"[SUB-AGENT] infrastructure_agent query={query!r}")
    try:
        result = _infrastructure_agent_instance(query)
        text = str(result)
        with _raw_outputs_lock:
            _raw_outputs["infrastructure_agent"] = text[:3000]
        _log(f"[TOOL_RAW] infrastructure_agent result={text[:2000]}")
        return text[:4000]
    except Exception as e:
        err = json.dumps({"status": "error", "message": str(e)})
        with _raw_outputs_lock:
            _raw_outputs["infrastructure_agent"] = err
        return err


# ============================================================
# 챗봇 전용 툴
# ============================================================

@tool
def search_incident_history(alert_name: str, query: str = "") -> str:
    """과거 인시던트 이력을 AgentCore Memory에서 검색합니다.

    사용 시나리오:
    - "ServiceDown 알람 최근에 몇 번 났어?"
    - "springboot 장애 원인이 주로 뭐야?"
    - "이런 에러 전에도 있었나?"

    Args:
        alert_name: 알람 이름 (예: "ServiceDown", "HighHttpErrorRate")
        query: 추가 검색 키워드 (선택)
    """
    try:
        from agentcore_memory import AgentCoreMemoryClient
        client = AgentCoreMemoryClient()

        result = {}

        # 통계
        stats = client.get_stats(alert_name)
        if stats:
            result["stats"] = stats

        # 유사 인시던트
        similar = client.search_similar_incidents(
            alert_name=alert_name,
            error_message=query,
            limit=5
        )
        if similar:
            result["similar_incidents"] = similar

        if not result:
            return json.dumps({"message": f"{alert_name} 관련 이력 없음"})

        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def get_ongoing_alarms() -> str:
    """현재 발화 중인 알람 목록을 DynamoDB에서 조회합니다.

    사용 시나리오:
    - "지금 발화 중인 알람 있어?"
    - "현재 어떤 알람이 켜져 있어?"
    """
    try:
        TABLE_NAME = os.environ.get("DYNAMODB_INCIDENT_TABLE", "")
        if not TABLE_NAME:
            return json.dumps({"message": "DYNAMODB_INCIDENT_TABLE 환경변수 미설정"})

        dynamodb = boto3.client("dynamodb", region_name=AWS_REGION)
        resp = dynamodb.scan(TableName=TABLE_NAME)
        items = resp.get("Items", [])

        if not items:
            return json.dumps({"message": "현재 발화 중인 알람 없음"})

        alarms = []
        for item in items:
            alarms.append({
                "alert_name": item.get("alert_name", {}).get("S", ""),
                "severity": item.get("severity", {}).get("S", ""),
                "started_at": item.get("started_at", {}).get("S", ""),
                "root_cause": item.get("root_cause", {}).get("S", "분석 중"),
            })

        return json.dumps({"ongoing_alarms": alarms, "count": len(alarms)},
                          ensure_ascii=False, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def get_active_services() -> str:
    """현재 AMP에 메트릭을 전송 중인 서비스 목록을 조회합니다.

    사용 시나리오:
    - "현재 메트릭 보내는 서비스가 뭐뭐야?"
    - "어떤 서비스들이 모니터링되고 있어?"
    """
    try:
        q = urllib.parse.quote("job:http_known_services:presence", safe="")
        url = f"{_amp_base_url()}/query?query={q}"
        _log(f"[TOOL] get_active_services url={url}")
        data = _amp_signed_request(url)
        items = data.get("data", {}).get("result", [])
        _log(f"[TOOL] get_active_services items={len(items)}")

        if not items:
            return json.dumps({"message": "현재 활성 서비스 없음"})

        services = []
        for v in items:
            metric = v.get("metric", {})
            services.append({
                "job": metric.get("job", ""),
                "environment": metric.get("deployment_environment", ""),
            })

        return json.dumps({"active_services": services, "count": len(services)},
                          ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


# ============================================================
# Athena 헬퍼
# ============================================================

def _athena_run_query(sql: str, timeout_sec: int = 60) -> list[dict]:
    """Athena 쿼리 실행 후 결과를 딕셔너리 리스트로 반환"""
    if not ATHENA_OUTPUT:
        raise ValueError("ATHENA_OUTPUT_BUCKET 환경변수 미설정")

    athena = boto3.client("athena", region_name=AWS_REGION)
    resp = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": ATHENA_DB},
        ResultConfiguration={"OutputLocation": ATHENA_OUTPUT},
    )
    qid = resp["QueryExecutionId"]

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status = athena.get_query_execution(QueryExecutionId=qid)
        state = status["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        elif state in ("FAILED", "CANCELLED"):
            reason = status["QueryExecution"]["Status"].get("StateChangeReason", "")
            raise RuntimeError(f"Athena query {state}: {reason}")
        time.sleep(2)
    else:
        athena.stop_query_execution(QueryExecutionId=qid)
        raise TimeoutError(f"Athena query timed out after {timeout_sec}s")

    # 결과 수집
    pages = athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid)
    rows = []
    headers = None
    for page in pages:
        result_rows = page["ResultSet"]["Rows"]
        if headers is None:
            headers = [c["VarCharValue"] for c in result_rows[0]["Data"]]
            result_rows = result_rows[1:]
        for row in result_rows:
            values = [c.get("VarCharValue", "") for c in row["Data"]]
            rows.append(dict(zip(headers, values)))

    return rows


@tool
def query_historical_logs(
    service_name: str = "",
    severity: str = "",
    keyword: str = "",
    start_date: str = "",
    end_date: str = "",
    limit: int = 50,
    environment: str = "dev",
) -> str:
    """S3에 저장된 장기 로그 이력을 Athena로 조회합니다. OpenSearch보다 오래된 데이터 조회에 사용합니다.

    Args:
        service_name: 서비스명 (예: "springboot", "thymeleaf"). 빈 문자열이면 전체 서비스.
        severity:     로그 심각도 필터 (예: "ERROR", "WARN", "INFO"). 빈 문자열이면 전체.
        keyword:      로그 본문 키워드 검색 (예: "OutOfMemoryError", "timeout").
        start_date:   조회 시작 날짜 (YYYY-MM-DD 형식). 빈 문자열이면 오늘.
        end_date:     조회 종료 날짜 (YYYY-MM-DD 형식). 빈 문자열이면 오늘.
        limit:        최대 반환 건수 (기본 50, 최대 200).
        environment:  환경 필터 ("dev" = VM/Docker, "prod" = EC2/AWS). 기본 "dev".

    사용 시나리오:
    - "지난주 springboot ERROR 로그 검색해줘"
    - "2주 전 OutOfMemoryError 발생 이력 알려줘"
    - "3월 15일 장애 당시 로그 보여줘"
    - "prod 환경 오늘 에러 로그 보여줘"
    """
    try:
        today = datetime.now(timezone.utc)
        if not start_date:
            start_date = today.strftime("%Y-%m-%d")
        if not end_date:
            end_date = today.strftime("%Y-%m-%d")

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date, "%Y-%m-%d")

        limit = min(int(limit), 200)

        # 파티션 필터 생성 (deployment_environment 최상위 파티션 포함)
        env_clause = f"deployment_environment = '{environment}' AND " if environment else ""
        partition_filter = (
            f"{env_clause}"
            f"(year > '{start_dt.year}' OR (year = '{start_dt.year}' AND month >= '{start_dt.month:02d}')) "
            f"AND (year < '{end_dt.year}' OR (year = '{end_dt.year}' AND month <= '{end_dt.month:02d}'))"
        )

        # UNNEST 후 WHERE 조건 (파티션 필터는 외부 WHERE, 나머지는 HAVING처럼 서브쿼리로)
        svc_expr = "element_at(transform(filter(rl.resource.attributes, x -> x.key = 'service.name'), x -> x.value.stringvalue), 1)"

        extra_conditions = []
        if service_name:
            safe_svc = service_name.replace("'", "''")
            extra_conditions.append(f"lower({svc_expr}) LIKE lower('%{safe_svc}%')")
        if severity:
            extra_conditions.append(f"upper(lr.severitytext) = upper('{severity}')")
        if keyword:
            safe_kw = keyword.replace("'", "''")
            extra_conditions.append(f"lower(lr.body.stringvalue) LIKE lower('%{safe_kw}%')")

        extra_where = ("AND " + " AND ".join(extra_conditions)) if extra_conditions else ""

        sql = f"""
SELECT
    from_unixtime(cast(lr.timeunixnano AS bigint) / 1000000000)                                   AS log_time,
    element_at(transform(filter(rl.resource.attributes, x -> x.key = 'service.namespace'),
               x -> x.value.stringvalue), 1)                                                      AS service_namespace,
    {svc_expr}                                                                                     AS svc_name,
    lr.severitytext                                                                                AS severity,
    substr(lr.body.stringvalue, 1, 300)                                                           AS body,
    year,
    month,
    day,
    hour
FROM log_platform_dev_observability.otel_logs_app
CROSS JOIN UNNEST(resourcelogs) AS t(rl)
CROSS JOIN UNNEST(rl.scopelogs) AS t2(sl)
CROSS JOIN UNNEST(sl.logrecords) AS t3(lr)
WHERE {partition_filter}
  {extra_where}
ORDER BY lr.timeunixnano DESC
LIMIT {limit}
"""
        rows = _athena_run_query(sql.strip())

        if not rows:
            return json.dumps({"message": "조건에 맞는 로그 없음", "query_range": f"{start_date} ~ {end_date}"})

        return json.dumps({
            "total": len(rows),
            "query_range": f"{start_date} ~ {end_date}",
            "logs": rows,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def query_historical_traces(
    service_name: str = "",
    status_code: str = "",
    min_duration_ms: int = 0,
    start_date: str = "",
    end_date: str = "",
    limit: int = 50,
    environment: str = "dev",
) -> str:
    """S3에 저장된 장기 트레이스 이력을 Athena로 조회합니다. 느린 API, 오류 트레이스 분석에 사용합니다.

    Args:
        service_name:    서비스명 (예: "springboot"). 빈 문자열이면 전체.
        status_code:     HTTP 상태코드 필터 (예: "500", "404"). 빈 문자열이면 전체.
        min_duration_ms: 최소 응답시간(ms) 필터. 느린 요청 조회에 사용 (예: 1000 = 1초 이상).
        start_date:      조회 시작 날짜 (YYYY-MM-DD). 빈 문자열이면 오늘.
        end_date:        조회 종료 날짜 (YYYY-MM-DD). 빈 문자열이면 오늘.
        limit:           최대 반환 건수 (기본 50, 최대 200).
        environment:     환경 필터 ("dev" = VM/Docker, "prod" = EC2/AWS). 기본 "dev".

    사용 시나리오:
    - "지난주 5초 이상 걸린 API 요청 알려줘"
    - "어제 500 에러 발생한 트레이스 보여줘"
    - "3월 10일 장애 당시 느린 트레이스 분석해줘"
    """
    try:
        today = datetime.now(timezone.utc)
        if not start_date:
            start_date = today.strftime("%Y-%m-%d")
        if not end_date:
            end_date = today.strftime("%Y-%m-%d")

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date, "%Y-%m-%d")

        limit = min(int(limit), 200)

        env_clause = f"deployment_environment = '{environment}' AND " if environment else ""
        partition_filter = (
            f"{env_clause}"
            f"(year > '{start_dt.year}' OR (year = '{start_dt.year}' AND month >= '{start_dt.month:02d}')) "
            f"AND (year < '{end_dt.year}' OR (year = '{end_dt.year}' AND month <= '{end_dt.month:02d}'))"
        )

        svc_expr_trace = "element_at(transform(filter(rs.resource.attributes, x -> x.key = 'service.name'), x -> x.value.stringvalue), 1)"

        extra_conditions = []
        if service_name:
            safe_svc = service_name.replace("'", "''")
            extra_conditions.append(f"lower({svc_expr_trace}) LIKE lower('%{safe_svc}%')")
        if status_code:
            extra_conditions.append(
                f"element_at(transform(filter(sp.attributes, x -> x.key = 'http.status_code'), "
                f"x -> x.value.stringvalue), 1) = '{status_code}'"
            )
        if min_duration_ms > 0:
            min_ns = min_duration_ms * 1_000_000
            extra_conditions.append(
                f"(cast(sp.endtimeunixnano AS bigint) - cast(sp.starttimeunixnano AS bigint)) >= {min_ns}"
            )

        extra_where = ("AND " + " AND ".join(extra_conditions)) if extra_conditions else ""

        sql = f"""
SELECT
    from_unixtime(cast(sp.starttimeunixnano AS bigint) / 1000000000)                              AS span_time,
    element_at(transform(filter(rs.resource.attributes, x -> x.key = 'service.namespace'),
               x -> x.value.stringvalue), 1)                                                      AS service_namespace,
    element_at(transform(filter(rs.resource.attributes, x -> x.key = 'service.name'),
               x -> x.value.stringvalue), 1)                                                      AS svc_name,
    sp.name                                                                                        AS span_name,
    element_at(transform(filter(sp.attributes, x -> x.key = 'http.method'),
               x -> x.value.stringvalue), 1)                                                      AS http_method,
    element_at(transform(filter(sp.attributes, x -> x.key = 'http.target'),
               x -> x.value.stringvalue), 1)                                                      AS http_target,
    element_at(transform(filter(sp.attributes, x -> x.key = 'http.status_code'),
               x -> x.value.stringvalue), 1)                                                      AS http_status_code,
    round(cast(sp.endtimeunixnano AS double) / 1000000 -
          cast(sp.starttimeunixnano AS double) / 1000000, 2)                                      AS duration_ms,
    sp.traceid                                                                                     AS trace_id,
    year,
    month,
    day
FROM log_platform_dev_observability.otel_traces
CROSS JOIN UNNEST(resourcespans) AS t(rs)
CROSS JOIN UNNEST(rs.scopespans) AS t2(ss)
CROSS JOIN UNNEST(ss.spans) AS t3(sp)
WHERE {partition_filter}
  {extra_where}
ORDER BY duration_ms DESC
LIMIT {limit}
"""
        rows = _athena_run_query(sql.strip())

        if not rows:
            return json.dumps({"message": "조건에 맞는 트레이스 없음", "query_range": f"{start_date} ~ {end_date}"})

        return json.dumps({
            "total": len(rows),
            "query_range": f"{start_date} ~ {end_date}",
            "traces": rows,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})


@tool
def query_log_error_summary(
    start_date: str = "",
    end_date: str = "",
    environment: str = "dev",
) -> str:
    """기간별 서비스/심각도 별 로그 발생 건수를 집계합니다. 장애 기간의 오류 패턴 파악에 유용합니다.

    Args:
        start_date:  집계 시작 날짜 (YYYY-MM-DD). 빈 문자열이면 오늘.
        end_date:    집계 종료 날짜 (YYYY-MM-DD). 빈 문자열이면 오늘.
        environment: 환경 필터 ("dev" = VM/Docker, "prod" = EC2/AWS). 기본 "dev".

    사용 시나리오:
    - "지난 일주일 동안 서비스별 에러 건수 알려줘"
    - "어제 어떤 서비스에서 오류가 가장 많이 났어?"
    - "이번 달 ERROR 로그 통계 보여줘"
    """
    try:
        today = datetime.now(timezone.utc)
        if not start_date:
            start_date = today.strftime("%Y-%m-%d")
        if not end_date:
            end_date = today.strftime("%Y-%m-%d")

        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt   = datetime.strptime(end_date, "%Y-%m-%d")

        env_clause = f"deployment_environment = '{environment}' AND " if environment else ""
        partition_filter = (
            f"{env_clause}"
            f"(year > '{start_dt.year}' OR (year = '{start_dt.year}' AND month >= '{start_dt.month:02d}')) "
            f"AND (year < '{end_dt.year}' OR (year = '{end_dt.year}' AND month <= '{end_dt.month:02d}'))"
        )

        sql = f"""
SELECT
    year,
    month,
    day,
    element_at(transform(filter(rl.resource.attributes, x -> x.key = 'service.name'),
               x -> x.value.stringvalue), 1)     AS service_name,
    lr.severitytext                               AS severity,
    count(*)                                      AS log_count
FROM log_platform_dev_observability.otel_logs_app
CROSS JOIN UNNEST(resourcelogs) AS t(rl)
CROSS JOIN UNNEST(rl.scopelogs) AS t2(sl)
CROSS JOIN UNNEST(sl.logrecords) AS t3(lr)
WHERE {partition_filter}
  AND upper(lr.severitytext) IN ('ERROR', 'WARN', 'FATAL')
GROUP BY year, month, day,
    element_at(transform(filter(rl.resource.attributes, x -> x.key = 'service.name'),
               x -> x.value.stringvalue), 1),
    lr.severitytext
ORDER BY year DESC, month DESC, day DESC, log_count DESC
LIMIT 200
"""
        rows = _athena_run_query(sql.strip())

        if not rows:
            return json.dumps({"message": "해당 기간 ERROR/WARN 로그 없음", "query_range": f"{start_date} ~ {end_date}"})

        return json.dumps({
            "query_range": f"{start_date} ~ {end_date}",
            "summary": rows,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})
