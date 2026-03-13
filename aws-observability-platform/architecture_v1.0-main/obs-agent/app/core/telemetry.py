from __future__ import annotations

import json
import time
from typing import Any, Dict, List

from app.shared.aws_clients import amp_signed_get, get_opensearch_client
from urllib.parse import quote


def _pick_instant_value(resp: Dict[str, Any], service: str, environment: str) -> float | None:
    """
    AMP query 결과에서 가장 적절한 시계열의 value를 숫자로 추출한다.
    - recording rule 메트릭은 라벨 구성이 환경별로 다를 수 있어, service/environment 라벨이 없을 수도 있음
    - 우선순위: (service, env) 매칭 가능한 시계열 -> service만 매칭 -> 첫 번째
    """
    try:
        result = resp["data"]["result"]
        if not result:
            return None

        def _score(item: Dict[str, Any]) -> int:
            metric = item.get("metric", {}) or {}
            score = 0
            # 흔히 사용되는 서비스 라벨 후보들
            for k in ("service_name", "service", "job", "resource_service_name"):
                if metric.get(k) == service:
                    score += 10
            # 환경 라벨 후보들
            for k in ("deployment_environment", "environment"):
                if metric.get(k) == environment:
                    score += 5
            return score

        best = max(result, key=_score)
        value = best["value"][1]
        return float(value)
    except Exception:
        return None


def query_service_errors(
    service: str,
    environment: str = "dev",
    last_minutes: int = 15,
) -> Dict[str, Any]:
    """
    AMP + OpenSearch 를 사용해서 최근 에러/지연 상태를 요약한다.
    - AMP: error ratio / latency p95 파생 메트릭
    - OpenSearch: 최근 에러 로그 샘플
    """
    if not service:
        return {"status": "error", "summary": "service is required", "data": None}

    queries = {
        # recording rules는 라벨 구성이 달라질 수 있어, 우선 전체 시계열을 조회 후 Lambda에서 선택한다.
        "error_ratio": "app_http_server_error_ratio_5m",
        "latency_p95": "app_http_server_latency_p95_5m",
    }

    amp_results: Dict[str, Any] = {}
    for key, promql in queries.items():
        promql_encoded = quote(promql, safe="")
        raw = amp_signed_get("/api/v1/query", f"query={promql_encoded}")
        parsed = json.loads(raw)
        amp_results[key] = parsed

    error_ratio = _pick_instant_value(amp_results["error_ratio"], service=service, environment=environment)
    latency_p95 = _pick_instant_value(amp_results["latency_p95"], service=service, environment=environment)

    os_client = get_opensearch_client()
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - last_minutes * 60 * 1000

    log_query: Dict[str, Any] = {
        "size": 5,
        "sort": [{"@timestamp": {"order": "desc"}}],
        "query": {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": from_ms, "lte": now_ms, "format": "epoch_millis"}}},
                    {"term": {"resource.service.name": service}},
                    {"term": {"resource.deployment.environment": environment}},
                ],
            }
        },
    }

    logs_resp = os_client.search(index="logs-app", body=log_query)
    hits: List[Dict[str, Any]] = logs_resp.get("hits", {}).get("hits", [])
    log_samples = [
        {
            "timestamp": h["_source"].get("@timestamp"),
            "body": h["_source"].get("body")
            or h["_source"].get("log")
            or h["_source"].get("message"),
        }
        for h in hits
    ]

    summary_parts: List[str] = []
    if error_ratio is not None:
        summary_parts.append(f"error_ratio={error_ratio:.4f}")
    if latency_p95 is not None:
        summary_parts.append(f"p95_latency={latency_p95:.0f}")

    summary = f"{service} metrics checked for last {last_minutes}m"
    if summary_parts:
        summary += " (" + ", ".join(summary_parts) + ")"

    return {
        "status": "success",
        "summary": summary,
        "data": {
            "service": service,
            "environment": environment,
            "window_minutes": last_minutes,
            "metrics": {
                "error_ratio": error_ratio,
                "latency_p95": latency_p95,
            },
            "log_samples": log_samples,
        },
    }

