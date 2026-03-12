"""
AWS Observability Agent - AWS 환경용 Tool 함수
agents.py (로컬 JSON) 대신 실제 AMP/OpenSearch를 쿼리

환경변수:
    AMP_ENDPOINT        - AMP workspace endpoint
    OPENSEARCH_ENDPOINT - OpenSearch domain endpoint
    OPENSEARCH_USER     - OpenSearch 사용자
    OPENSEARCH_PASSWORD - OpenSearch 비밀번호
    AWS_REGION_NAME     - AWS 리전 (기본: ap-northeast-2)
"""

import os
import json
import boto3
import urllib.request
import urllib.parse
import base64
from datetime import datetime, timedelta

from strands import Agent, tool
from strands.models import BedrockModel

# ============================================================
# 환경변수
# ============================================================
AMP_ENDPOINT = os.environ.get("AMP_ENDPOINT", "")
OS_ENDPOINT  = os.environ.get("OPENSEARCH_ENDPOINT", "")
OS_USER      = os.environ.get("OPENSEARCH_USER", "admin")
OS_PASSWORD  = os.environ.get("OPENSEARCH_PASSWORD", "")
AWS_REGION   = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")


# ============================================================
# 공통 헬퍼: 메트릭 라벨에서 서비스명 추출
# AMP 메트릭은 service_name 대신 job 라벨을 사용함
# ============================================================
def get_service_name(metric: dict) -> str:
    """메트릭 라벨에서 서비스명 추출 (job 우선, 없으면 service_name)"""
    return metric.get("job", metric.get("service_name", "unknown"))


# ============================================================
# AMP 쿼리 헬퍼
# ============================================================
def query_amp(promql: str, time_range_minutes: int = 60) -> list:
    """AMP에 PromQL range 쿼리 실행"""
    try:
        session = boto3.Session()
        credentials = session.get_credentials().get_frozen_credentials()

        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        end_time = datetime.utcnow()
        start_time = end_time - timedelta(minutes=time_range_minutes)

        # AMP_ENDPOINT가 이미 .../api/v1/ 로 끝나므로 중복 제거
        base = AMP_ENDPOINT.rstrip("/")
        if base.endswith("/api/v1"):
            base = base  # 그대로
        else:
            base = base + "/api/v1"

        url = (
            f"{base}/query_range"
            f"?query={urllib.parse.quote(promql)}"
            f"&start={start_time.timestamp()}"
            f"&end={end_time.timestamp()}"
            f"&step=60"
        )

        request = AWSRequest(method="GET", url=url)
        SigV4Auth(credentials, "aps", AWS_REGION).add_auth(request)

        req = urllib.request.Request(url, headers=dict(request.headers))
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("data", {}).get("result", [])
    except Exception as e:
        import traceback
        print(f"[query_amp 에러] {type(e).__name__}: {e}")
        print(f"[query_amp 스택] {traceback.format_exc()}")
        print(f"[query_amp URL] {url if 'url' in dir() else 'URL 생성 전 실패'}")
        return [{"error": str(e)}]


def query_amp_instant(promql: str) -> list:
    """AMP에 즉시 쿼리 실행 (현재 값)"""
    try:
        session = boto3.Session()
        credentials = session.get_credentials().get_frozen_credentials()

        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest

        # AMP_ENDPOINT가 이미 .../api/v1/ 로 끝나므로 중복 제거
        base = AMP_ENDPOINT.rstrip("/")
        if not base.endswith("/api/v1"):
            base = base + "/api/v1"

        url = f"{base}/query?query={urllib.parse.quote(promql)}"
        request = AWSRequest(method="GET", url=url)
        SigV4Auth(credentials, "aps", AWS_REGION).add_auth(request)

        req = urllib.request.Request(url, headers=dict(request.headers))
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("data", {}).get("result", [])
    except Exception as e:
        import traceback
        print(f"[query_amp_instant 에러] {type(e).__name__}: {e}")
        print(f"[query_amp_instant 스택] {traceback.format_exc()}")
        print(f"[query_amp_instant URL] {url if 'url' in dir() else 'URL 생성 전 실패'}")
        return [{"error": str(e)}]


# ============================================================
# OpenSearch 쿼리 헬퍼
# ============================================================
def query_opensearch(index: str, query: dict) -> dict:
    """OpenSearch에 쿼리 실행"""
    try:
        credentials = base64.b64encode(f"{OS_USER}:{OS_PASSWORD}".encode()).decode()
        url = f"https://{OS_ENDPOINT}/{index}/_search"
        payload = json.dumps(query).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {credentials}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        import traceback
        print(f"[query_opensearch 에러] {type(e).__name__}: {e}")
        print(f"[query_opensearch 스택] {traceback.format_exc()}")
        print(f"[query_opensearch URL] {url if 'url' in dir() else 'URL 생성 전 실패'}")
        return {"error": str(e), "hits": {"hits": []}}


# ============================================================
# Metrics Tools
# ============================================================

@tool
def get_metrics_summary() -> str:
    """
    전체 서비스의 메트릭 요약 (CPU, 메모리, HTTP 요청/에러율)을 AMP에서 조회합니다.
    job 라벨 기준으로 서비스를 구분합니다.
    """
    lines = ["=== 메트릭 요약 ==="]
    found_any = False

    # CPU 사용률
    cpu_results = query_amp_instant("jvm_cpu_recent_utilization_ratio")
    if cpu_results and "error" not in cpu_results[0]:
        lines.append("[CPU 사용률]")
        for r in cpu_results:
            service = get_service_name(r.get("metric", {}))
            value = float((r.get("values", [[0, 0]])[-1][1] if r.get("values") else r.get("value", [0, 0])[1]))
            lines.append(f"  {service}: {value*100:.1f}%")
        found_any = True
    elif cpu_results and "error" in cpu_results[0]:
        lines.append(f"[CPU 사용률] 조회 실패: {cpu_results[0]['error']}")

    # 메모리 사용률 (AMP는 나누기 연산 미지원 → 두 쿼리로 분리 후 계산)
    mem_used = query_amp_instant("jvm_memory_used_bytes")
    mem_limit = query_amp_instant("jvm_memory_limit_bytes")

    mem_limit_map = {}
    if mem_limit and "error" not in mem_limit[0]:
        for r in mem_limit:
            instance = r.get("metric", {}).get("instance", "")
            area = r.get("metric", {}).get("type", "")  # heap/nonheap 구분
            key = f"{instance}_{area}"
            mem_limit_map[key] = float((r.get("values", [[0, 0]])[-1][1] if r.get("values") else r.get("value", [0, 0])[1]))

    if mem_used and "error" not in mem_used[0]:
        lines.append("[메모리 사용률]")
        # job+type 기준으로 집계 (heap/nonheap 합산)
        svc_used = {}
        svc_limit = {}
        for r in mem_used:
            m = r.get("metric", {})
            service = get_service_name(m)
            instance = m.get("instance", "")
            area = m.get("type", "")
            key = f"{instance}_{area}"
            used = float((r.get("values", [[0, 0]])[-1][1] if r.get("values") else r.get("value", [0, 0])[1]))
            limit = mem_limit_map.get(key, 0)
            svc_used[service] = svc_used.get(service, 0) + used
            svc_limit[service] = svc_limit.get(service, 0) + limit

        for service in svc_used:
            used = svc_used[service]
            limit = svc_limit.get(service, 0)
            if limit > 0:
                pct = used / limit * 100
                lines.append(f"  {service}: {pct:.1f}% ({used/1024/1024:.0f}MB / {limit/1024/1024:.0f}MB)")
            else:
                lines.append(f"  {service}: {used/1024/1024:.0f}MB (limit 미확인)")
        found_any = True
    elif mem_used and "error" in mem_used[0]:
        lines.append(f"[메모리 사용률] 조회 실패: {mem_used[0]['error']}")

    # HTTP 요청/초 (job 기준 집계)
    http_results = query_amp_instant(
        "sum(rate(http_server_request_duration_seconds_count[5m])) by (job)"
    )
    if http_results and "error" not in http_results[0]:
        lines.append("[HTTP 요청/초]")
        for r in http_results:
            service = r.get("metric", {}).get("job", "unknown")
            value = float((r.get("values", [[0, 0]])[-1][1] if r.get("values") else r.get("value", [0, 0])[1]))
            lines.append(f"  {service}: {value:.2f} req/s")
        found_any = True

    # HTTP 에러율 (4xx + 5xx / 전체, job 기준)
    # 에러율 - Python에서 나누기 처리
    err_cnt = query_amp_instant('sum(rate(http_server_request_duration_seconds_count{http_response_status_code=~"4..|5.."}[5m])) by (job)')
    total_cnt = query_amp_instant('sum(rate(http_server_request_duration_seconds_count[5m])) by (job)')
    if (err_cnt and "error" not in err_cnt[0] and total_cnt and "error" not in total_cnt[0]):
        total_map = {r.get("metric",{}).get("job","unknown"): float((r.get("values",[[0,0]])[-1][1])) for r in total_cnt}
        lines.append("[HTTP 에러율 (4xx+5xx)]")
        for r in err_cnt:
            job = r.get("metric", {}).get("job", "unknown")
            ev = float(r.get("values", [[0,0]])[-1][1]) if r.get("values") else 0.0
            tv = total_map.get(job, 0)
            lines.append(f"  {job}: {ev/tv*100:.2f}%" if tv > 0 else f"  {job}: 0.00%")
        found_any = True

    if not found_any:
        lines.append("수집된 메트릭 없음 (OTel 앱이 실행 중인지 확인하세요)")

    return "\n".join(lines)


@tool
def get_jvm_metrics(service_name: str = "") -> str:
    """
    JVM 메트릭 (메모리, GC, 스레드)을 AMP에서 조회합니다.
    Args:
        service_name: 조회할 job명 (예: todobackend-springboot). 비어있으면 전체
    """
    # AMP는 job 라벨 사용
    filter_str = f'{{job="{service_name}"}}' if service_name else ""
    lines = ["=== JVM 메트릭 ==="]
    found_any = False

    # bytes 단위로 받아서 Python에서 MB 변환 (PromQL 산술연산 제거)
    metrics = {
        "메모리 사용": (f"jvm_memory_used_bytes{filter_str}", "bytes"),
        "메모리 한계": (f"jvm_memory_limit_bytes{filter_str}", "bytes"),
        "GC후 메모리": (f"jvm_memory_committed_bytes{filter_str}", "bytes"),
        "스레드 수": (f"jvm_thread_count{filter_str}", "count"),
        "CPU 사용률": (f"jvm_cpu_recent_utilization_ratio{filter_str}", "ratio"),
    }

    for label, (query, unit) in metrics.items():
        results = query_amp_instant(query)
        if results and "error" not in results[0]:
            lines.append(f"[{label}]")
            for r in results:
                service = get_service_name(r.get("metric", {}))
                value = float((r.get("values", [[0, 0]])[-1][1] if r.get("values") else r.get("value", [0, 0])[1]))
                if unit == "bytes":
                    lines.append(f"  {service}: {value/1024/1024:.1f}MB")
                elif unit == "ratio":
                    lines.append(f"  {service}: {value*100:.1f}%")
                else:
                    lines.append(f"  {service}: {value:.0f}")
            found_any = True

    if not found_any:
        lines.append("JVM 메트릭 없음 (JVM 기반 서비스가 실행 중인지 확인하세요)")

    return "\n".join(lines)


@tool
def get_http_metrics(service_name: str = "") -> str:
    """
    HTTP 메트릭 (요청 수, 에러율, 상태코드별 분포, 응답시간)을 AMP에서 조회합니다.
    Args:
        service_name: 조회할 job명 (예: todobackend-springboot). 비어있으면 전체
    """
    # AMP는 job 라벨 사용
    job_filter = f'job="{service_name}"' if service_name else ""
    lines = ["=== HTTP 메트릭 ==="]
    found_any = False

    # 상태코드별 요청 수 (에러 포함 전체 현황)
    if job_filter:
        status_promql = (
            f'sum(rate(http_server_request_duration_seconds_count{{{job_filter}}}[5m]))'
            f' by (job, http_response_status_code, http_route)'
        )
    else:
        status_promql = (
            'sum(rate(http_server_request_duration_seconds_count[5m]))'
            ' by (job, http_response_status_code, http_route)'
        )
    status_results = query_amp_instant(status_promql)
    if status_results and "error" not in status_results[0]:
        lines.append("[상태코드별 요청/초]")
        for r in status_results:
            m = r.get("metric", {})
            service = m.get("job", "unknown")
            status = m.get("http_response_status_code", "?")
            route = m.get("http_route", "?")
            value = float((r.get("values", [[0, 0]])[-1][1] if r.get("values") else r.get("value", [0, 0])[1]))
            if value > 0:
                lines.append(f"  {service} [{status}] {route}: {value:.3f} req/s")
        found_any = True

    # 에러율 - 에러 건수/전체 건수를 따로 쿼리해서 Python에서 나누기 처리 (PromQL 나누기 제거)
    if job_filter:
        err_count_promql = f'sum(rate(http_server_request_duration_seconds_count{{http_response_status_code=~"4..|5..",{job_filter}}}[5m])) by (job)'
        total_count_promql = f'sum(rate(http_server_request_duration_seconds_count{{{job_filter}}}[5m])) by (job)'
    else:
        err_count_promql = 'sum(rate(http_server_request_duration_seconds_count{http_response_status_code=~"4..|5.."}[5m])) by (job)'
        total_count_promql = 'sum(rate(http_server_request_duration_seconds_count[5m])) by (job)'

    err_results = query_amp_instant(err_count_promql)
    total_results = query_amp_instant(total_count_promql)

    if (err_results and "error" not in err_results[0] and
            total_results and "error" not in total_results[0]):
        total_map = {}
        for r in total_results:
            job = r.get("metric", {}).get("job", "unknown")
            vals = r.get("values", [[0, 0]])
            total_map[job] = float(vals[-1][1]) if vals else 0.0
        lines.append("[HTTP 에러율 (4xx+5xx)]")
        for r in err_results:
            job = r.get("metric", {}).get("job", "unknown")
            vals = r.get("values", [[0, 0]])
            err_val = float(vals[-1][1]) if vals else 0.0
            total_val = total_map.get(job, 0)
            if total_val > 0:
                lines.append(f"  {job}: {err_val/total_val*100:.2f}%")
            else:
                lines.append(f"  {job}: 0.00%")
        found_any = True

    # P95 응답시간
    if job_filter:
        lat_promql = (
            f'histogram_quantile(0.95, sum(rate(http_server_request_duration_seconds_bucket{{{job_filter}}}[5m])) by (job, le))'
        )
    else:
        lat_promql = (
            'histogram_quantile(0.95, sum(rate(http_server_request_duration_seconds_bucket[5m])) by (job, le))'
        )
    latency_results = query_amp_instant(lat_promql)
    if latency_results and "error" not in latency_results[0]:
        lines.append("[P95 응답시간]")
        for r in latency_results:
            service = r.get("metric", {}).get("job", "unknown")
            value = float((r.get("values", [[0, 0]])[-1][1] if r.get("values") else r.get("value", [0, 0])[1]))
            lines.append(f"  {service}: {value*1000:.2f}ms")
        found_any = True

    if not found_any:
        lines.append("HTTP 메트릭 없음 (앱이 실행 중인지 확인하세요)")

    return "\n".join(lines)


# ============================================================
# Logs Tools
# ============================================================

@tool
def get_logs_summary() -> str:
    """
    최근 로그 요약 (서비스별 severity 분포)을 OpenSearch에서 조회합니다.
    """
    query = {
        "size": 0,
        "query": {
            "range": {
                "time": {
                    "gte": "now-1h"
                }
            }
        },
        "aggs": {
            "by_service": {
                "terms": {"field": "serviceName", "size": 10},
                "aggs": {
                    "by_severity": {
                        "terms": {"field": "severityText", "size": 5}
                    }
                }
            }
        }
    }

    result = query_opensearch("logs-*", query)
    lines = ["=== 로그 요약 (최근 1시간) ==="]

    if "error" in result:
        lines.append(f"조회 실패: {result['error']}")
        return "\n".join(lines)

    buckets = result.get("aggregations", {}).get("by_service", {}).get("buckets", [])
    for b in buckets:
        service = b.get("key", "unknown")
        lines.append(f"[{service}]")
        for sev in b.get("by_severity", {}).get("buckets", []):
            lines.append(f"  {sev['key']}: {sev['doc_count']}건")

    if not buckets:
        lines.append("로그 데이터 없음")

    return "\n".join(lines)


@tool
def search_logs(severity: str = "", keyword: str = "") -> str:
    """
    특정 severity 또는 키워드로 로그를 OpenSearch에서 검색합니다.
    Args:
        severity: 로그 레벨 (ERROR, WARN, INFO 등)
        keyword: 검색 키워드
    """
    must_clauses = [{"range": {"time": {"gte": "now-1h"}}}]

    if severity:
        must_clauses.append({"term": {"severityText": severity}})
    if keyword:
        must_clauses.append({"match": {"body": keyword}})

    query = {
        "size": 20,
        "query": {"bool": {"must": must_clauses}},
        "sort": [{"time": {"order": "desc"}}]
    }

    result = query_opensearch("logs-*", query)

    if "error" in result:
        return f"로그 검색 실패: {result['error']}"

    hits = result.get("hits", {}).get("hits", [])

    if not hits:
        return f"조건에 맞는 로그 없음 (severity={severity}, keyword={keyword})"

    lines = [f"=== 로그 검색 결과 ({len(hits)}건) ==="]
    for h in hits:
        src = h.get("_source", {})
        timestamp = src.get("time", "")
        svc = src.get("serviceName", "unknown")
        sev = src.get("severityText", "")
        body = src.get("body", "")
        lines.append(f"  [{timestamp}] [{svc}] {sev}: {body[:100]}")

    return "\n".join(lines)


@tool
def get_error_logs() -> str:
    """
    최근 ERROR/WARN 로그를 OpenSearch에서 조회합니다.
    """
    query = {
        "size": 20,
        "query": {
            "bool": {
                "must": [
                    {"range": {"time": {"gte": "now-1h"}}},
                    {"terms": {"severityText": ["ERROR", "WARN"]}}
                ]
            }
        },
        "sort": [{"time": {"order": "desc"}}]
    }

    result = query_opensearch("logs-*", query)

    if "error" in result:
        return f"로그 조회 실패: {result['error']}"

    hits = result.get("hits", {}).get("hits", [])

    if not hits:
        return "최근 1시간 ERROR/WARN 로그 없음"

    lines = [f"=== ERROR/WARN 로그 ({len(hits)}건) ==="]
    for h in hits:
        src = h.get("_source", {})
        timestamp = src.get("time", "")
        svc = src.get("serviceName", "unknown")
        sev = src.get("severityText", "")
        body = src.get("body", "")
        lines.append(f"  [{timestamp}] [{svc}] {sev}: {body[:150]}")

    return "\n".join(lines)


# ============================================================
# Traces Tools
# ============================================================

@tool
def get_traces_summary() -> str:
    """
    트레이스 요약 (서비스별 span 수, 평균 응답시간, 에러율)을 OpenSearch에서 조회합니다.
    """
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    query = {
        "size": 0,
        "aggs": {
            "recent": {
                "filter": {"range": {"startTime": {"gte": one_hour_ago}}},
                "aggs": {
                    "by_service": {
                        "terms": {"field": "serviceName", "size": 10},
                        "aggs": {
                            "avg_duration": {"avg": {"field": "durationInNanos"}},
                            "error_count": {
                                "filter": {"term": {"status.code": 2}}
                            }
                        }
                    }
                }
            }
        }
    }

    result = query_opensearch("otel-v1-apm-span-*", query)

    if "error" in result:
        return f"트레이스 조회 실패: {result['error']}"

    lines = ["=== 트레이스 요약 (최근 1시간) ==="]

    buckets = result.get("aggregations", {}).get("recent", {}).get("by_service", {}).get("buckets", [])
    for b in buckets:
        service = b.get("key", "unknown")
        count = b.get("doc_count", 0)
        avg_ns = b.get("avg_duration", {}).get("value", 0) or 0
        avg_ms = avg_ns / 1_000_000
        errors = b.get("error_count", {}).get("doc_count", 0)
        error_rate = (errors / count * 100) if count > 0 else 0
        lines.append(f"[{service}]")
        lines.append(f"  span 수: {count}개, 평균 응답시간: {avg_ms:.2f}ms, 에러율: {error_rate:.1f}%")

    if not buckets:
        lines.append("트레이스 데이터 없음")

    return "\n".join(lines)


@tool
def get_slow_spans(threshold_ms: float = 100.0) -> str:
    """
    임계값 이상의 느린 span을 OpenSearch에서 조회합니다.
    Args:
        threshold_ms: 느린 span 기준 시간(밀리초), 기본값 100ms
    """
    threshold_ns = threshold_ms * 1_000_000
    one_hour_ago = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")

    query = {
        "size": 20,
        "query": {
            "bool": {
                "must": [
                    {"range": {"startTime": {"gte": one_hour_ago}}},
                    {"range": {"durationInNanos": {"gte": threshold_ns}}}
                ]
            }
        },
        "sort": [{"durationInNanos": {"order": "desc"}}]
    }

    result = query_opensearch("otel-v1-apm-span-*", query)

    if "error" in result:
        return f"slow span 조회 실패: {result['error']}"

    hits = result.get("hits", {}).get("hits", [])

    if not hits:
        return f"threshold {threshold_ms}ms를 초과하는 slow span이 없습니다."

    lines = [f"=== Slow Spans (>{threshold_ms}ms) ==="]
    for h in hits:
        src = h.get("_source", {})
        service = src.get("serviceName", "unknown")
        name = src.get("name", "unknown")
        duration_ms = src.get("durationInNanos", 0) / 1_000_000
        trace_id = src.get("traceId", "")[:16]
        lines.append(f"  [{service}] {name}: {duration_ms:.2f}ms (traceId: {trace_id}...)")

    return "\n".join(lines)


@tool
def get_trace_by_id(trace_id: str) -> str:
    """
    특정 traceId의 전체 span을 OpenSearch에서 조회합니다.
    Args:
        trace_id: 조회할 trace ID
    """
    query = {
        "size": 50,
        "query": {"term": {"traceId.keyword": trace_id}},
        "sort": [{"startTime": {"order": "asc"}}]
    }

    result = query_opensearch("otel-v1-apm-span-*", query)

    if "error" in result:
        return f"트레이스 조회 실패: {result['error']}"

    hits = result.get("hits", {}).get("hits", [])

    if not hits:
        return f"traceId {trace_id}에 해당하는 span이 없습니다."

    lines = [f"=== Trace {trace_id[:16]}... ({len(hits)}개 span) ==="]
    for h in hits:
        src = h.get("_source", {})
        service = src.get("serviceName", "unknown")
        name = src.get("name", "unknown")
        duration_ms = src.get("durationInNanos", 0) / 1_000_000
        status = src.get("status", {}).get("code", 0)
        status_str = "❌ ERROR" if status == 2 else "✅"
        lines.append(f"  {status_str} [{service}] {name}: {duration_ms:.2f}ms")

    return "\n".join(lines)


# ============================================================
# Agent 정의 (비용 최적화: Haiku 3.5 사용)
# ============================================================

# 🔵 Haiku 3.5 모델 (비용 효율적)
# 입력: $0.8/MTok, 출력: $4/MTok (Sonnet 4 대비 75% 저렴)
haiku_model = BedrockModel(
    model_id="apac.anthropic.claude-sonnet-4-20250514-v1:0",
    region_name=AWS_REGION,
    streaming=False
)

_metrics_agent = Agent(
    model=haiku_model,
    system_prompt="""Metrics 전문 분석가. 반드시 Tool 호출.
- AMP 메트릭은 job 라벨로 서비스를 구분함 (예: todobackend-springboot, todoui-thymeleaf)
- 핵심 수치만 간결하게 정리
- 3줄 이내 결론
- 코드 예시, 권장사항 제외
- Tool은 한 번에 하나씩만 호출
- 문제 없으면 "정상" 한 줄로 끝""",
    tools=[get_metrics_summary, get_jvm_metrics, get_http_metrics],
)

_logs_agent = Agent(
    model=haiku_model,
    system_prompt="""Logs 전문 분석가. 반드시 Tool 호출.
- 발견된 이슈만 간결하게 요약
- 이슈 없으면 "정상" 한 줄로 끝
- 로그 없는 서비스는 언급하지 말 것
- 코드 예시 제외""",
    tools=[get_logs_summary, search_logs, get_error_logs],
)

_traces_agent = Agent(
    model=haiku_model,
    system_prompt="""Traces 전문 분석가. 반드시 Tool 호출.
- 서비스별 응답시간, 에러율만 표로 정리
- 느린 요청 있으면 서비스, ms만 명시
- 코드 예시 제외
- 문제 없으면 "정상" 한 줄로 끝""",
    tools=[get_traces_summary, get_slow_spans, get_trace_by_id],
)


def _extract_agent_result(result) -> str:
    """Strands AgentResult에서 텍스트 추출"""
    # 1. message 속성 (AgentResult.message)
    if hasattr(result, 'message') and result.message:
        msg = result.message
        # message가 dict인 경우 content 추출
        if isinstance(msg, dict):
            content = msg.get('content', [])
            if isinstance(content, list):
                texts = [c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text']
                if texts:
                    return '\n'.join(texts)
        return str(msg)
    # 2. stop_reason이 있는 경우 - AgentResult 객체
    if hasattr(result, 'stop_reason'):
        # messages에서 마지막 assistant 메시지 추출
        if hasattr(result, 'messages'):
            for msg in reversed(result.messages):
                if isinstance(msg, dict) and msg.get('role') == 'assistant':
                    content = msg.get('content', [])
                    if isinstance(content, list):
                        texts = [c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text']
                        if texts:
                            return '\n'.join(texts)
    # 3. 직접 문자열 변환
    return str(result)


@tool
def metrics_agent(question: str) -> str:
    """
    AMP에서 메트릭(CPU, 메모리, HTTP 에러율, 응답시간 등)을 수집하고 분석합니다.
    Args:
        question: 분석할 메트릭 관련 질문
    """
    try:
        print(f"[metrics_agent 시작] question={question[:50]}")
        result = _metrics_agent(question)
        text = _extract_agent_result(result)
        print(f"[metrics_agent 결과] {len(text)}자: {text[:200]}")
        return text
    except Exception as e:
        import traceback
        print(f"[metrics_agent 에러] {type(e).__name__}: {e}")
        print(f"[metrics_agent 스택] {traceback.format_exc()}")
        return f"metrics_agent 실패: {e}"


@tool
def logs_agent(question: str) -> str:
    """
    OpenSearch에서 에러 로그, WARN 로그를 수집하고 분석합니다.
    Args:
        question: 분석할 로그 관련 질문
    """
    try:
        print(f"[logs_agent 시작] question={question[:50]}")
        result = _logs_agent(question)
        text = _extract_agent_result(result)
        print(f"[logs_agent 결과] {len(text)}자: {text[:200]}")
        return text
    except Exception as e:
        import traceback
        print(f"[logs_agent 에러] {type(e).__name__}: {e}")
        print(f"[logs_agent 스택] {traceback.format_exc()}")
        return f"logs_agent 실패: {e}"


@tool
def traces_agent(question: str) -> str:
    """
    OpenSearch에서 트레이스(느린 요청, 에러 span, 서비스 간 호출)를 수집하고 분석합니다.
    Args:
        question: 분석할 트레이스 관련 질문
    """
    try:
        print(f"[traces_agent 시작] question={question[:50]}")
        result = _traces_agent(question)
        text = _extract_agent_result(result)
        print(f"[traces_agent 결과] {len(text)}자: {text[:200]}")
        return text
    except Exception as e:
        import traceback
        print(f"[traces_agent 에러] {type(e).__name__}: {e}")
        print(f"[traces_agent 스택] {traceback.format_exc()}")
        return f"traces_agent 실패: {e}"