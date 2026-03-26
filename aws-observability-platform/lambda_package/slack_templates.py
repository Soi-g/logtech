"""
Slack Block Kit 템플릿 + Pydantic 스키마 정의
attachments + blocks 조합으로 색깔 선 + Block Kit 동시 구현
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta

_KST = timezone(timedelta(hours=9))
def _now_kst() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d %H:%M KST")
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ============================================================
# Pydantic 스키마
# ============================================================

Severity = Literal["info", "low", "medium", "high", "critical"]
Priority = Literal["low", "medium", "high"]
AnalysisType = Literal["log", "metric", "trace"]


class TimeRange(BaseModel):
    start: str = Field(description="분석 시작 시각 (ISO8601 문자열)")
    end: str   = Field(description="분석 종료 시각 (ISO8601 문자열)")


class EvidenceItem(BaseModel):
    source:    str           = Field(description="근거 출처. 예: logs, metrics, traces, opensearch, amp")
    detail:    str           = Field(description="근거 상세 설명")
    timestamp: Optional[str] = Field(default=None, description="근거 시각 (있으면 기록)")


class ActionItem(BaseModel):
    action:   str      = Field(description="권장 조치")
    priority: Priority = Field(description="조치 우선순위")


class AnalysisResult(BaseModel):
    analysis_type:        AnalysisType       = Field(description="분석 타입")
    service_name:         str                = Field(description="대상 서비스명")
    time_range:           TimeRange          = Field(description="분석 시간 범위")
    summary:              str                = Field(description="핵심 요약")
    evidence:             list[EvidenceItem] = Field(default_factory=list)
    suspected_root_cause: list[str]          = Field(default_factory=list)
    severity:             Severity           = Field(description="심각도")
    recommended_actions:  list[ActionItem]   = Field(default_factory=list)


class RunbookReference(BaseModel):
    source:    str = Field(description="런북 파일명")
    section:   str = Field(description="참조한 섹션명")
    relevance: str = Field(description="이 런북이 현재 장애와 어떻게 관련되는지 설명")


class IncidentReport(BaseModel):
    incident_summary:   str                                          = Field(description="최종 장애 요약")
    likely_root_causes: list[str]                                    = Field(default_factory=list)
    severity:           Literal["low", "medium", "high", "critical"] = Field(description="최종 심각도")
    impact:             str                                          = Field(description="장애 영향 범위")
    immediate_actions:  list[str]                                    = Field(default_factory=list)
    follow_up_actions:  list[str]                                    = Field(default_factory=list)
    evidence_summary:   list[str]                                    = Field(default_factory=list)
    runbook_references: list[RunbookReference]                       = Field(default_factory=list)


# ============================================================
# 상수
# ============================================================

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high":     "🟠",
    "medium":   "🟡",
    "low":      "🟢",
    "info":     "⚪",
}

SEVERITY_COLOR = {
    "critical": "#f22613",
    "high":     "#FF6600",
    "medium":   "#FFAA00",
    "low":      "#2eb886",
    "info":     "#AAAAAA",
    "unknown":  "#AAAAAA",
}


# ============================================================
# Block Kit 빌더
# ============================================================

def build_simple_alert_message(
    alert_name: str,
    severity: str = "critical",
    service_info: str = "",
    description: str = "",
    detected_at: str = "",
    similar_info: dict = None,
) -> dict:
    """FIRING 즉시 전송 — 사실 기반 단순 요약 (AI 분석 없음)
    similar_info: {'count': int, 'avg_minutes': float, 'root_cause': str, 'resolution': str}
    """
    emoji = SEVERITY_EMOJI.get(severity, "🚨")
    color = SEVERITY_COLOR.get(severity, "#AAAAAA")
    now   = detected_at or _now_kst()

    # ── 헤더: 알람명을 크게 ──────────────────────────────────────
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} {alert_name}", "emoji": True}
        },
        # 심각도 + 감지 시각 (2열)
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*심각도*\n{emoji} {severity.upper()}"},
                {"type": "mrkdwn", "text": f"*감지 시각*\n{now}"},
            ]
        },
    ]

    # 서비스 정보
    if service_info:
        blocks.append({
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*서비스*\n{service_info}"},
            ]
        })

    # 설명 — rich_text_preformatted 박스
    if description:
        blocks.append({
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "text", "text": "📄 설명", "style": {"bold": True}}
                    ]
                },
                {
                    "type": "rich_text_preformatted",
                    "elements": [
                        {"type": "text", "text": description}
                    ]
                },
            ]
        })

    # 유사 과거 사례 — rich_text_quote로 박스 처리
    if similar_info:
        count       = similar_info.get('count', 0)
        avg_minutes = similar_info.get('avg_minutes', 0)
        root_cause  = similar_info.get('root_cause', '') or '—'
        resolution  = similar_info.get('resolution', '') or '—'
        root_cause_short = root_cause.split(',')[0].strip() if ',' in root_cause else root_cause
        if len(root_cause_short) > 80:
            root_cause_short = root_cause_short[:77] + '...'

        blocks.append({"type": "divider"})
        blocks.append({
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [
                        {"type": "emoji", "name": "zap"},
                        {"type": "text", "text": " 유사 과거 사례  ", "style": {"bold": True}},
                        {"type": "text", "text": f"{count}회 발생", "style": {"code": True}},
                        {"type": "text", "text": "  ·  "},
                        {"type": "text", "text": f"평균 {avg_minutes:.0f}분 소요", "style": {"code": True}},
                    ]
                },
                {
                    "type": "rich_text_preformatted",
                    "elements": [
                        {"type": "text", "text": "이전 원인: ", "style": {"bold": True}},
                        {"type": "text", "text": root_cause_short + "\n"},
                        {"type": "text", "text": "이전 조치: ", "style": {"bold": True}},
                        {"type": "text", "text": resolution},
                    ]
                },
            ]
        })

    # 버튼
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔍 분석 요청", "emoji": True},
                "style": "primary",
                "action_id": "analyze_incident",
                "value": alert_name
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✅ 조치 완료", "emoji": True},
                "action_id": "resolve_incident",
                "value": alert_name
            }
        ]
    })

    return {"attachments": [{"color": color, "blocks": blocks}]}


def build_alert_message(alert_info: str, severity: str = "critical") -> dict:
    """분석 요청 후 '분석 중...' 상태 메시지 (chat.update용)"""
    emoji = SEVERITY_EMOJI.get(severity, "🚨")
    color = SEVERITY_COLOR.get(severity, "#AAAAAA")
    now   = _now_kst()

    return {
        "attachments": [
            {
                "color": color,
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"{emoji} Alert 감지 — 분석 중...", "emoji": True}
                    },
                    {
                        "type": "section",
                        "fields": [
                            {"type": "mrkdwn", "text": f"*알람*\n{alert_info}"},
                            {"type": "mrkdwn", "text": f"*분석 시작*\n{now}"},
                        ]
                    },
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": "⏳ AI 분석 중... 잠시 기다려 주세요."}]
                    }
                ]
            }
        ]
    }


def build_incident_report_message(
    alert_info: str,
    report: IncidentReport,
    amp_link: str = "",
    detected_at: str = "",
) -> dict:
    """IncidentReport → Slack attachments + blocks 최종 분석 메시지"""

    severity     = report.severity
    emoji        = SEVERITY_EMOJI.get(severity, "🟡")
    color        = SEVERITY_COLOR.get(severity, "#AAAAAA")
    now          = _now_kst()
    detected_at  = detected_at or now

    blocks = []

    # ── 헤더 ──────────────────────────────────────────────
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": f"{emoji} Observability 분석 완료", "emoji": True}
    })

    # ── 알람 기본 정보 ────────────────────────────────────
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*알람*\n{alert_info}"},
            {"type": "mrkdwn", "text": f"*심각도*\n{emoji} {severity.upper()}"},
        ]
    })
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*탐지 시각*\n{detected_at}"},
            {"type": "mrkdwn", "text": f"*분석 완료*\n{now}"},
        ]
    })
    blocks.append({"type": "divider"})

    # ── 장애 요약 ─────────────────────────────────────────
    if report.incident_summary:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📋 장애 요약*\n{report.incident_summary}"}
        })

    # ── 영향 범위 ─────────────────────────────────────────
    if report.impact:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*💥 영향 범위*\n{report.impact}"}
        })

    blocks.append({"type": "divider"})

    # ── 추정 원인 ─────────────────────────────────────────
    if report.likely_root_causes:
        causes_text = "\n".join(f"• {c}" for c in report.likely_root_causes)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🔍 추정 원인*\n{causes_text}"}
        })

    # ── 핵심 근거 ─────────────────────────────────────────
    if report.evidence_summary:
        ev_text = "\n".join(f"• {e}" for e in report.evidence_summary[:5])
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📊 핵심 근거*\n{ev_text}"}
        })

    blocks.append({"type": "divider"})

    # ── 즉시 조치 ─────────────────────────────────────────
    if report.immediate_actions:
        actions_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(report.immediate_actions))
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*⚡ 즉시 조치*\n{actions_text}"}
        })

    # ── 후속 조치 ─────────────────────────────────────────
    if report.follow_up_actions:
        followup_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(report.follow_up_actions))
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*📝 후속 조치*\n{followup_text}"}
        })

    blocks.append({"type": "divider"})

    # ── 참조 런북 ─────────────────────────────────────────
    if report.runbook_references:
        rb_lines = []
        for rb in report.runbook_references:
            rb_lines.append(f"• *[{rb.source}]* {rb.section}\n  _{rb.relevance}_")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📖 참조 런북*\n" + "\n".join(rb_lines)}
        })
    else:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📖 참조 런북*\n참조할 런북이 없습니다"}
        })

    # ── 해결 완료 버튼 ────────────────────────────────────
    alert_name = alert_info.split('\n')[0].strip()
    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "✅ 해결 완료", "emoji": True},
                "style": "primary",
                "action_id": "resolve_incident",
                "value": alert_name
            }
        ]
    })

    # ── 하단 컨텍스트 ─────────────────────────────────────
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"Scenario: `{alert_name}` | 분석 완료"}]
    })

    return {
        "attachments": [
            {
                "color": color,
                "blocks": blocks
            }
        ]
    }


def build_resolved_message(
    alert_name: str,
    resolution_minutes: float,
    root_cause: str = '',
    resolved_by: str = '자동 복구',
) -> dict:
    """원본 FIRING 메시지를 대체하는 복구 완료 메시지 (chat.update용)"""
    now = _now_kst()
    duration_text = f"{resolution_minutes:.0f}분" if resolution_minutes > 0 else "알 수 없음"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "✅ 장애 복구 완료", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*알람*\n{alert_name}"},
                {"type": "mrkdwn", "text": f"*복구 시각*\n{now}"},
            ]
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*소요 시간*\n{duration_text}"},
                {"type": "mrkdwn", "text": f"*처리*\n{resolved_by}"},
            ]
        },
    ]

    if root_cause:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*🔍 원인*\n{root_cause}"}
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "장기 메모리에 해결 정보가 저장되었습니다."}]
    })

    return {
        "attachments": [
            {
                "color": "#2eb886",  # 초록색
                "blocks": blocks
            }
        ]
    }


def build_analysis_append_blocks(
    alert_name: str,
    report: IncidentReport,
    history_info: str = '',
    session_id: str = '',
) -> list:
    """분석 결과 블록 목록 — 기존 alert 메시지에 append용. 조치완료 버튼 포함."""
    emoji = SEVERITY_EMOJI.get(report.severity, "🟡")
    now   = _now_kst()

    blocks: list = [
        {"type": "divider"},
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{emoji} AI 분석 결과", "emoji": True}
        },
        # 심각도 + 분석 완료 시각 (2열)
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*심각도*\n{emoji} {report.severity.upper()}"},
                {"type": "mrkdwn", "text": f"*분석 완료*\n{now}"},
            ]
        },
        {"type": "divider"},
    ]

    def _rt_preformatted_lines(items: list) -> dict:
        """여러 항목을 개행 구분해 rich_text_preformatted 박스에 담기"""
        text = "\n".join(f"• {item}" for item in items)
        return {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": text}]}

    # 장애 요약 — preformatted 박스
    if report.incident_summary:
        blocks.append({
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": "📋 장애 요약", "style": {"bold": True}}]
                },
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": report.incident_summary}]
                },
            ]
        })

    # 추정 원인 — preformatted 박스
    cause_items = report.likely_root_causes or ["—"]
    blocks.append({
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [{"type": "text", "text": "🔍 추정 원인", "style": {"bold": True}}]
            },
            _rt_preformatted_lines(cause_items),
        ]
    })

    # 핵심 근거 — preformatted 박스
    ev_items = report.evidence_summary[:4] or ["—"]
    blocks.append({
        "type": "rich_text",
        "elements": [
            {
                "type": "rich_text_section",
                "elements": [{"type": "text", "text": "📊 핵심 근거", "style": {"bold": True}}]
            },
            _rt_preformatted_lines(ev_items),
        ]
    })

    blocks.append({"type": "divider"})

    # 추천 조치사항 — preformatted 박스 (ordered list)
    all_actions = list(report.immediate_actions) + list(report.follow_up_actions)
    if all_actions:
        action_text = "\n".join(f"{i+1}. {a}" for i, a in enumerate(all_actions))
        blocks.append({
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": "💡 추천 조치사항", "style": {"bold": True}}]
                },
                {"type": "rich_text_preformatted", "elements": [{"type": "text", "text": action_text}]},
            ]
        })

    # 참조 런북 (있을 때만)
    if report.runbook_references:
        rb_lines = [
            f"• *{rb.source}* › {rb.section}\n  _{rb.relevance}_"
            for rb in report.runbook_references
        ]
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "*📖 참조 런북*\n" + "\n".join(rb_lines)}
        })

    blocks.append({"type": "divider"})

    # 조치완료 버튼
    blocks.append({
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "✅ 조치 완료", "emoji": True},
            "style": "primary",
            "action_id": "resolve_incident",
            "value": alert_name
        }]
    })

    ctx = f"분석 완료: {now}"
    if session_id:
        ctx += f" | Session: `{session_id}`"
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": ctx}]
    })

    return blocks


def build_resolved_append_blocks(
    alert_name: str,
    resolution_minutes: float,
    resolved_by: str = '자동 복구',
    actual_resolution: str = '',
) -> list:
    """조치완료 블록 목록 — 기존 메시지에 append용. 버튼 없음."""
    now           = _now_kst()
    duration_text = f"{resolution_minutes:.0f}분" if resolution_minutes > 0 else "알 수 없음"

    blocks: list = [
        {"type": "divider"},
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "✅ 장애 복구 완료", "emoji": True}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*복구 시각*\n{now}"},
                {"type": "mrkdwn", "text": f"*소요 시간*\n{duration_text}"},
            ]
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*처리*\n{resolved_by}"},
            ]
        },
    ]

    # 실제 조치 내용 — preformatted 박스
    if actual_resolution:
        blocks.append({
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_section",
                    "elements": [{"type": "text", "text": "🛠 조치 내용", "style": {"bold": True}}]
                },
                {
                    "type": "rich_text_preformatted",
                    "elements": [{"type": "text", "text": actual_resolution}]
                },
            ]
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": "장기 메모리에 해결 정보가 저장되었습니다."}]
    })

    return blocks


def build_error_message(alert_info: str, error: str) -> dict:
    """분석 실패 시 에러 메시지"""
    return {
        "attachments": [
            {
                "color": "#AAAAAA",
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": "⚠️ 분석 실패", "emoji": True}
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*알람:* {alert_info}\n*오류:* ```{error}```"
                        }
                    }
                ]
            }
        ]
    }