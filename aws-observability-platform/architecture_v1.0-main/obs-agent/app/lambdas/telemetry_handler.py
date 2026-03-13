from __future__ import annotations

import json
from typing import Any, Dict, List

from app.core.telemetry import query_service_errors


def _parameters_to_map(parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for p in parameters or []:
        name = p.get("name")
        value = p.get("value")
        if name:
            result[name] = value
    return result


def _bedrock_text_response(event: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bedrock Agents Lambda action group 응답 형식에 맞춘 래퍼.
    payload 는 우리가 정의한 JSON(요약/데이터)이다.
    """
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup"),
            "function": event.get("function"),
            "functionResponse": {
                "responseBody": {
                    "TEXT": {
                        "body": json.dumps(payload, ensure_ascii=False)
                    }
                }
            },
        },
    }


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Bedrock Agent Action Group -> Lambda 엔트리 포인트 (Telemetry 전용)

    예상 event 예시:
    {
      "messageVersion": "1.0",
      "actionGroup": "TelemetryActions",
      "function": "QueryServiceErrors",
      "parameters": [
        { "name": "service", "value": "springboot" },
        { "name": "environment", "value": "dev" },
        { "name": "lastMinutes", "value": "15" }
      ]
    }
    """
    function_name = event.get("function")
    params = _parameters_to_map(event.get("parameters", []))

    try:
        if function_name == "QueryServiceErrors":
            service = params.get("service", "")
            environment = params.get("environment", "dev")
            last_minutes_raw = params.get("lastMinutes", "15")
            try:
                last_minutes = int(last_minutes_raw)
            except Exception:
                last_minutes = 15

            result = query_service_errors(
                service=service,
                environment=environment,
                last_minutes=last_minutes,
            )
            return _bedrock_text_response(event, result)

        # 지원하지 않는 함수
        return _bedrock_text_response(
            event,
            {
                "status": "error",
                "summary": f"Unsupported function: {function_name}",
                "data": None,
            },
        )

    except Exception as exc:
        return _bedrock_text_response(
            event,
            {
                "status": "error",
                "summary": "Unhandled exception in telemetry_handler",
                "data": {
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
            },
        )

