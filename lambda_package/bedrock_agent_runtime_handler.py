"""
Bedrock Agent Runtime Handler with Strands Agents
SNS 알람 → Bedrock Agent (Strands Agents 사용) → Slack

장기 메모리: OpenSearch Serverless (벡터 검색)
"""

import os
import json
import boto3
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
def _now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")

# LangGraph 임포트
from graph_agent_with_memory import build_graph, lookup_memory

# AgentCore Memory 임포트
from agentcore_memory import AgentCoreMemory

# DynamoDB ongoing 추적 (Phase 4: AOSS 대체)
from dynamodb_incident import (
    get_ongoing_incident, put_ongoing_incident, delete_ongoing_incident,
    update_ongoing_root_cause
)

# Slack 템플릿 임포트
from slack_templates import (
    build_alert_message,
    build_simple_alert_message,
    build_incident_report_message,
    build_analysis_append_blocks,
    build_resolved_append_blocks,
    SEVERITY_COLOR,
    IncidentReport,
    RunbookReference
)

# ============================================================
# 클라이언트 초기화
# ============================================================
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime', region_name='ap-northeast-2')
BEDROCK_KB_ID = os.environ.get('BEDROCK_KB_ID', '')

def search_runbooks(alert_name: str) -> list:
    """Knowledge Base에서 런북 직접 검색"""
    if not BEDROCK_KB_ID:
        return []
    try:
        response = bedrock_agent_runtime.retrieve(
            knowledgeBaseId=BEDROCK_KB_ID,
            retrievalQuery={'text': f'{alert_name} runbook response procedure'},
            retrievalConfiguration={
                'vectorSearchConfiguration': {'numberOfResults': 3}
            }
        )
        results = []
        for item in response.get('retrievalResults', []):
            content = item.get('content', {}).get('text', '')
            score = item.get('score', 0)
            location = item.get('location', {})
            s3_uri = location.get('s3Location', {}).get('uri', '')
            filename = s3_uri.split('/')[-1] if s3_uri else ''
            if not filename:
                metadata = item.get('metadata', {})
                filename = metadata.get('x-amz-bedrock-kb-source-uri', '').split('/')[-1]
            print(f"   runbook: {filename}, score: {score:.3f}")
            if score > 0.3 and content:
                results.append({'title': filename, 'content': content[:500], 'score': score})
        print(f"runbook search: {len(results)} results")
        return results
    except Exception as e:
        print(f"runbook search failed: {e}")
        return []

AGENT_ID = os.environ.get('BEDROCK_AGENT_ID')
AGENT_ALIAS_ID = os.environ.get('BEDROCK_AGENT_ALIAS_ID')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
SLACK_CHANNEL = os.environ.get('SLACK_CHANNEL')


# ============================================================
# Slack 전송
# ============================================================
def send_to_slack(alert_name: str, result: dict, message_ts: str = None, channel: str = None):
    """Slack으로 분석 결과 전송 (slack_templates.py 사용)

    Args:
        alert_name: 알람명
        result: AI 분석 결과
        message_ts: 업데이트할 메시지의 타임스탬프 (있으면 업데이트, 없으면 새 메시지)
        channel: 전송할 채널 (없으면 환경변수 SLACK_CHANNEL 사용)
    """
    target_channel = channel or SLACK_CHANNEL
    
    report_dict = result['report']
    stats = result['stats']
    session_id = result['session_id']
    similar = result.get('similar', [])

    # history_info는 초기 알람 메시지에 이미 포함되므로 분석 결과에는 제외
    history_info = ''
    
    # IncidentReport 객체 생성
    try:
        runbook_refs = []
        for rb in report_dict.get('runbook_references', []):
            runbook_refs.append(RunbookReference(
                source=rb.get('title', rb.get('source', 'Unknown')),
                section=rb.get('section', ''),
                relevance=rb.get('summary', rb.get('relevance', ''))
            ))

        incident_report = IncidentReport(
            incident_summary=report_dict.get('incident_summary', 'N/A'),
            likely_root_causes=report_dict.get('likely_root_causes', []),
            severity=report_dict.get('severity', 'medium').lower(),
            impact=report_dict.get('impact', 'N/A'),
            immediate_actions=report_dict.get('immediate_actions', []),
            follow_up_actions=report_dict.get('follow_up_actions', []),
            evidence_summary=report_dict.get('evidence_summary', []),
            runbook_references=runbook_refs
        )

        if message_ts:
            # ── append 모드: 현재 메시지 블록 유지 + 분석 결과 append ──
            color, curr_blocks = _get_slack_message_attachment(target_channel, message_ts)
            base_blocks = _strip_action_and_status_blocks(curr_blocks)
            append_blocks = build_analysis_append_blocks(
                alert_name=alert_name,
                report=incident_report,
                history_info=history_info,
                session_id=session_id,
            )
            new_color = SEVERITY_COLOR.get(incident_report.severity, color)
            _update_slack_message({
                'channel': target_channel,
                'ts': message_ts,
                'attachments': [{'color': new_color, 'blocks': base_blocks + append_blocks}]
            })
            # root_cause DynamoDB 업데이트
            root_cause = ', '.join(report_dict.get('likely_root_causes', []))
            if root_cause:
                update_ongoing_root_cause(alert_name, root_cause)
        else:
            # ── 새 메시지 모드 (fallback) ──
            alert_info = f"{alert_name}\n{history_info}"
            message = build_incident_report_message(
                alert_info=alert_info,
                report=incident_report,
                detected_at=_now_kst()
            )
            message['channel'] = target_channel
            _send_slack_message(message)

    except Exception as e:
        print(f"⚠️ Slack 템플릿 생성 실패: {e}")
        fallback_message = {
            "channel": target_channel,
            "text": f"🚨 {alert_name}\n\n{report_dict.get('incident_summary', 'N/A')}"
        }
        if message_ts:
            fallback_message['ts'] = message_ts
            _update_slack_message(fallback_message)
        else:
            _send_slack_message(fallback_message)


def send_resolution_to_slack(alert_name: str, resolution_minutes: float):
    """Slack으로 장애 해결 알림 전송"""
    
    message = {
        "channel": SLACK_CHANNEL,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"✅ {alert_name} 해결"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*소요 시간:* {resolution_minutes:.1f}분\n*상태:* 정상 복구"
                }
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": "장기 메모리에 해결 정보가 저장되었습니다."}
                ]
            }
        ]
    }
    
    _send_slack_message(message)


def _send_slack_message(message: dict):
    """Slack API 호출 헬퍼 (새 메시지 전송)"""
    import urllib.request
    import urllib.parse
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}'
    }
    
    data = json.dumps(message).encode('utf-8')
    req = urllib.request.Request(
        'https://slack.com/api/chat.postMessage',
        data=data,
        headers=headers
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                print(f"✅ Slack 전송 완료")
            else:
                print(f"⚠️ Slack 전송 실패: {result.get('error')}")
    except Exception as e:
        print(f"❌ Slack 전송 오류: {e}")


def _send_slack_message_and_get_ts(message: dict) -> str:
    """Slack API 호출 후 타임스탬프 반환"""
    import urllib.request
    import urllib.parse
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}'
    }
    
    data = json.dumps(message).encode('utf-8')
    req = urllib.request.Request(
        'https://slack.com/api/chat.postMessage',
        data=data,
        headers=headers
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                return result.get('ts', '')
            else:
                print(f"⚠️ Slack 전송 실패: {result.get('error')}")
                return ''
    except Exception as e:
        print(f"❌ Slack 전송 오류: {e}")
        return ''


def _open_slack_modal(trigger_id: str, modal: dict):
    """Slack 모달 열기 (views.open)"""
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}'
    }
    data = json.dumps({'trigger_id': trigger_id, 'view': modal}).encode('utf-8')
    req = urllib.request.Request('https://slack.com/api/views.open', data=data, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            if result.get('ok'):
                print(f"✅ Slack 모달 열기 완료")
            else:
                print(f"⚠️ Slack 모달 열기 실패: {result.get('error')}")
    except Exception as e:
        print(f"❌ Slack 모달 오류: {e}")


def _get_slack_message_attachment(channel: str, ts: str) -> tuple:
    """conversations.history로 메시지 attachments 조회. (color, blocks) 반환."""
    params = urllib.parse.urlencode({'channel': channel, 'latest': ts, 'limit': 1, 'inclusive': 'true'})
    req = urllib.request.Request(
        f'https://slack.com/api/conversations.history?{params}',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {SLACK_BOT_TOKEN}'}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            print(f"[conversations.history] ok={result.get('ok')} error={result.get('error')} channel={channel} ts={ts}")
            if not result.get('ok'):
                print(f"⚠️ conversations.history 실패: {result.get('error')}")
                return '#AAAAAA', []
            messages = result.get('messages', [])
            if not messages:
                print(f"⚠️ conversations.history: 메시지 없음")
                return '#AAAAAA', []
            att = messages[0].get('attachments', [{}])[0]
            blocks = att.get('blocks', [])
            print(f"[conversations.history] 블록 {len(blocks)}개 조회 성공")
            return att.get('color', '#AAAAAA'), blocks
    except Exception as e:
        print(f"⚠️ conversations.history 오류: {e}")
    return '#AAAAAA', []


def _strip_action_and_status_blocks(blocks: list) -> list:
    """actions 블록과 '분석 중' / 'AI 분석이 필요하면' 안내 context 블록 제거."""
    result = []
    for b in blocks:
        if b.get('type') == 'actions':
            continue
        if b.get('type') == 'context':
            text = b.get('elements', [{}])[0].get('text', '')
            if '분석 중' in text or '분석이 필요' in text:
                continue
        result.append(b)
    return result


def _update_slack_message(message: dict):
    """Slack 메시지 업데이트"""
    import urllib.request
    import urllib.parse
    
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {SLACK_BOT_TOKEN}'
    }
    
    data = json.dumps(message).encode('utf-8')
    req = urllib.request.Request(
        'https://slack.com/api/chat.update',
        data=data,
        headers=headers
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            if result.get('ok'):
                print(f"✅ Slack 메시지 업데이트 완료")
            else:
                print(f"⚠️ Slack 업데이트 실패: {result.get('error')}")
    except Exception as e:
        print(f"❌ Slack 업데이트 오류: {e}")


# ============================================================
# Slack 슬래시 커맨드 처리 (/analyze <질문>)
# ============================================================
SLACK_SIGNING_SECRET = os.environ.get('SLACK_SIGNING_SECRET', '')

def _verify_slack_signature(headers: dict, body_bytes: bytes) -> bool:
    import hmac, hashlib, time
    if not SLACK_SIGNING_SECRET:
        return True  # secret 미설정 시 스킵 (개발용)
    ts = headers.get('x-slack-request-timestamp') or headers.get('X-Slack-Request-Timestamp', '')
    sig = headers.get('x-slack-signature') or headers.get('X-Slack-Signature', '')
    if not ts or not sig:
        return False
    if abs(int(time.time()) - int(ts)) > 300:
        return False
    basestring = f"v0:{ts}:".encode() + body_bytes
    digest = hmac.new(SLACK_SIGNING_SECRET.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={digest}", sig)

def _handle_slash_command(event: dict, body_bytes: bytes) -> dict:
    headers = event.get('headers') or {}
    if not _verify_slack_signature(headers, body_bytes):
        return {'statusCode': 200, 'headers': {'Content-Type': 'text/plain'}, 'body': '❌ 서명 검증 실패'}

    form = urllib.parse.parse_qs(body_bytes.decode('utf-8', errors='replace'))
    query = (form.get('text', ['']) or [''])[0].strip()
    user_name = (form.get('user_name', ['unknown']) or ['unknown'])[0]

    if not query:
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'text/plain; charset=utf-8'},
            'body': '사용법: /analyze <질문>\n예) /analyze prod springboot 에러율 확인해줘'
        }

    print(f"[ENTRY] Slack /analyze: {query} (by @{user_name})")

    # 비동기로 자신을 invoke → 3초 내 응답 가능
    try:
        lambda_client = boto3.client('lambda', region_name=os.environ.get('AWS_REGION_NAME', 'ap-northeast-2'))
        lambda_client.invoke(
            FunctionName=os.environ.get('AWS_LAMBDA_FUNCTION_NAME'),
            InvocationType='Event',
            Payload=json.dumps({
                '_action': 'debug_query',
                'query': query,
                'environment': 'dev' if ('dev' in query.lower() and 'prod' not in query.lower()) else ('prod' if ('prod' in query.lower() and 'dev' not in query.lower()) else 'both'),
            }).encode()
        )
    except Exception as e:
        print(f"❌ /analyze 비동기 invoke 실패: {e}")

    return {'statusCode': 200, 'body': ''}


# ============================================================
# Slack 버튼 클릭 처리
# ============================================================
def _handle_slack_interaction(event: dict) -> dict:
    """
    Slack 버튼 클릭 → AOSS resolved 업데이트 → Slack 메시지 수정
    """
    import urllib.parse
    import base64

    try:
        body = event.get('body', '')
        if event.get('isBase64Encoded'):
            body = base64.b64decode(body).decode('utf-8')

        # Slack URL 검증 challenge 처리
        try:
            challenge_data = json.loads(body)
            if 'challenge' in challenge_data:
                print("🔐 Slack challenge 응답")
                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'challenge': challenge_data['challenge']})
                }
        except Exception:
            pass  # JSON 아니면 그냥 진행

        parsed = urllib.parse.parse_qs(body)
        payload = json.loads(parsed.get('payload', ['{}'])[0])

        payload_type = payload.get('type', '')

        # ── 모달 제출 처리 ──────────────────────────────────────────
        if payload_type == 'view_submission':
            view = payload.get('view', {})
            if view.get('callback_id') == 'resolve_modal':
                meta = json.loads(view.get('private_metadata', '{}'))
                alert_name   = meta.get('alert_name', '')
                channel_id   = meta.get('channel_id', SLACK_CHANNEL)
                message_ts   = meta.get('message_ts', '')
                detected_at_str = meta.get('detected_at', '')
                user_name    = payload.get('user', {}).get('name', 'unknown')

                # 실제 조치 내용
                values = view.get('state', {}).get('values', {})
                actual_resolution = (
                    values.get('resolution_block', {})
                          .get('resolution_input', {})
                          .get('value', '')
                )

                # 해결 시간 계산
                try:
                    detected_at = datetime.fromisoformat(detected_at_str.replace('Z', '+00:00'))
                    resolution_minutes = (
                        datetime.utcnow() - detected_at.replace(tzinfo=None)
                    ).total_seconds() / 60
                except Exception:
                    resolution_minutes = 0

                # DynamoDB에서 root_cause 미리 조회 (save_resolution이 삭제 전에)
                ongoing = get_ongoing_incident(alert_name)
                root_cause = ongoing.get('root_cause', '') if ongoing else ''

                # AgentCore resolved 저장 + DynamoDB 삭제
                from graph_agent_with_memory import save_resolution
                save_resolution(alert_name, actual_resolution, resolution_minutes)

                # 현재 메시지 블록 유지 + 조치완료 블록 append
                color, curr_blocks = _get_slack_message_attachment(channel_id, message_ts)
                base_blocks = _strip_action_and_status_blocks(curr_blocks)
                resolved_blocks = build_resolved_append_blocks(
                    alert_name=alert_name,
                    resolution_minutes=resolution_minutes,
                    resolved_by=f'@{user_name}',
                    actual_resolution=actual_resolution,
                )
                _update_slack_message({
                    'channel': channel_id,
                    'ts': message_ts,
                    'attachments': [{'color': '#2eb886', 'blocks': base_blocks + resolved_blocks}]
                })

                print(f"✅ 모달 조치완료 처리: {alert_name} ({resolution_minutes:.0f}분)")

            # view_submission은 빈 200 응답 필요 (모달 닫힘)
            return {'statusCode': 200, 'body': ''}

        # ── 버튼 클릭 처리 ─────────────────────────────────────────
        action = payload.get('actions', [{}])[0]
        action_id = action.get('action_id', '')
        alert_name = action.get('value', '')
        user_name = payload.get('user', {}).get('name', 'unknown')
        channel_id = payload.get('channel', {}).get('id', SLACK_CHANNEL)
        message_ts = payload.get('message', {}).get('ts', '')
        trigger_id = payload.get('trigger_id', '')

        print(f"🔘 버튼 클릭: action={action_id}, alert={alert_name}, user={user_name}")

        if action_id == 'analyze_incident':
            ongoing = get_ongoing_incident(alert_name)
            if not ongoing:
                print(f"⚠️ 분석 요청: ongoing 인시던트 없음 - {alert_name}")
                return {'statusCode': 200, 'body': ''}

            # 1. 현재 메시지 블록 가져오기 → 버튼/안내 제거 → "분석 중..." 추가
            color, curr_blocks = _get_slack_message_attachment(channel_id, message_ts)
            new_blocks = _strip_action_and_status_blocks(curr_blocks)
            new_blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "⏳ AI 분석 중... 잠시 기다려 주세요."}]
            })
            _update_slack_message({
                'channel': channel_id,
                'ts': message_ts,
                'attachments': [{'color': color, 'blocks': new_blocks}]
            })

            # 2. Lambda 자신을 비동기 호출 (LangGraph 실행)
            try:
                lambda_client = boto3.client('lambda', region_name=os.environ.get('AWS_REGION_NAME', 'ap-northeast-2'))
                lambda_client.invoke(
                    FunctionName=os.environ.get('AWS_LAMBDA_FUNCTION_NAME'),
                    InvocationType='Event',  # 비동기
                    Payload=json.dumps({
                        '_action': 'run_analysis',
                        'alert_name': alert_name,
                        'slack_ts': message_ts,
                        'slack_channel': channel_id,
                        'severity': ongoing.get('severity', 'high'),
                        'question': ongoing.get('question', alert_name),
                        'state_change_time': ongoing.get('timestamp', datetime.utcnow().isoformat()),
                        'memory_stats': ongoing.get('memory_stats', {}),
                        'similar_incidents': ongoing.get('similar_incidents', []),
                    }).encode()
                )
                print(f"✅ 비동기 분석 요청 완료: {alert_name}")
            except Exception as e:
                print(f"❌ 비동기 분석 호출 실패: {e}")

            return {'statusCode': 200, 'body': ''}

        elif action_id == 'resolve_incident':
            # 조치 내용 입력 모달 팝업 (실제 처리는 view_submission에서)
            ongoing = get_ongoing_incident(alert_name)
            root_cause_text = ongoing.get('root_cause', '분석 결과 없음') if ongoing else '분석 결과 없음'
            detected_at_str = ongoing.get('timestamp', '') if ongoing else ''

            modal = {
                'type': 'modal',
                'callback_id': 'resolve_modal',
                'private_metadata': json.dumps({
                    'alert_name': alert_name,
                    'channel_id': channel_id,
                    'message_ts': message_ts,
                    'detected_at': detected_at_str,
                }),
                'title': {'type': 'plain_text', 'text': '조치 완료 처리'},
                'submit': {'type': 'plain_text', 'text': '완료'},
                'close':  {'type': 'plain_text', 'text': '취소'},
                'blocks': [
                    {
                        'type': 'section',
                        'text': {'type': 'mrkdwn', 'text': f'*알람:* `{alert_name}`'},
                    },
                    {
                        'type': 'section',
                        'text': {'type': 'mrkdwn', 'text': f'*근본 원인*\n```{root_cause_text}```'},
                    },
                    {
                        'type': 'input',
                        'block_id': 'resolution_block',
                        'label': {'type': 'plain_text', 'text': '실제 조치 내용'},
                        'element': {
                            'type': 'plain_text_input',
                            'action_id': 'resolution_input',
                            'multiline': True,
                            'placeholder': {
                                'type': 'plain_text',
                                'text': '실제로 수행한 조치 내용을 입력하세요...',
                            },
                        },
                    },
                ],
            }

            if trigger_id:
                _open_slack_modal(trigger_id, modal)
                print(f'✅ 조치완료 모달 오픈: {alert_name}')
            else:
                print(f'⚠️ trigger_id 없음 → 모달 열기 실패: {alert_name}')

        # Slack에는 반드시 200 빠르게 응답해야 함 (3초 이내)
        return {
            'statusCode': 200,
            'body': ''
        }

    except Exception as e:
        print(f"❌ Slack interaction 처리 오류: {e}")
        return {'statusCode': 200, 'body': ''}


def _post_resolve_comment(channel_id: str, thread_ts: str, text: str):
    """해결 완료 메시지를 스레드에 추가"""
    try:
        slack_token = os.environ.get('SLACK_BOT_TOKEN', '')
        payload = {
            'channel': channel_id,
            'thread_ts': thread_ts,
            'text': text
        }
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            'https://slack.com/api/chat.postMessage',
            data=data,
            headers={
                'Authorization': f'Bearer {slack_token}',
                'Content-Type': 'application/json'
            }
        )
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
            if not result.get('ok'):
                print(f"⚠️ Slack 스레드 메시지 오류: {result.get('error')}")
    except Exception as e:
        print(f"❌ Slack 스레드 메시지 전송 오류: {e}")


def _extract_alert_description(message_text: str, alert_name: str, service_info: str) -> str:
    """SNS 메시지에서 description/summary 어노테이션 추출 (순수 Python, AI 없음)
    Alertmanager가 이미 실제 값으로 치환해서 메시지에 포함시켜 줌
    """
    import re

    # description 먼저 시도 (가장 구체적인 값 포함)
    desc = re.search(r'description\s*[=:]\s*(.+?)(?:\n|Labels:|Annotations:|$)', message_text, re.IGNORECASE | re.DOTALL)
    if desc:
        text = desc.group(1).strip().split('\n')[0].strip()
        if text:
            return text

    # summary fallback
    summary = re.search(r'summary\s*[=:]\s*(.+?)(?:\n|$)', message_text, re.IGNORECASE)
    if summary:
        text = summary.group(1).strip()
        if text:
            return text

    # 최후 fallback: 구조화된 문자열
    service_part = f"{service_info} 서비스에서 " if service_info else ""
    return f"{service_part}{alert_name} 알람이 감지되었습니다."


def _extract_service_info(subject: str) -> str:
    """Subject에서 service_name, service_namespace 추출"""
    import re
    service_name = re.search(r'service_name="?([^",)]+)"?', subject)
    namespace = re.search(r'service_namespace="?([^",)]+)"?', subject)
    parts = []
    if service_name:
        parts.append(service_name.group(1).strip())
    if namespace:
        parts.append(namespace.group(1).strip())
    return ' / '.join(parts) if parts else ''


AGENTCORE_RUNTIME_ARN = os.environ.get('AGENTCORE_RUNTIME_ARN', '')


def _run_analysis_async(event: dict) -> dict:
    """비동기로 호출된 분석 실행 - AgentCore Runtime 우선, Lambda fallback"""
    if AGENTCORE_RUNTIME_ARN:
        print(f"🚀 AgentCore Runtime으로 분석 위임: {event.get('alert_name')}")
        try:
            import json as _json
            client = boto3.client('bedrock-agentcore', region_name=os.environ.get('AWS_REGION_NAME', 'ap-northeast-2'))
            response = client.invoke_agent_runtime(
                agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
                payload=_json.dumps(event).encode('utf-8'),
                contentType='application/json',
                accept='application/json',
            )
            # Fire-and-forget: AgentCore Runtime이 독립적으로 분석 후 Slack 업데이트
            print(f"✅ AgentCore Runtime 위임 완료 (async) - response keys: {list(response.keys())}")
            return {'statusCode': 200, 'body': 'delegated to AgentCore Runtime'}
        except Exception as e:
            print(f"⚠️ AgentCore Runtime 호출 실패, Lambda fallback: {e}")
            return _run_analysis_lambda(event)
    else:
        return _run_analysis_lambda(event)


def _run_analysis_lambda(event: dict) -> dict:
    """Lambda에서 직접 LangGraph 실행 (AgentCore Runtime fallback)"""
    alert_name = event['alert_name']
    slack_ts = event['slack_ts']
    slack_channel = event['slack_channel']
    severity = event.get('severity', 'high')
    question = event.get('question', alert_name)
    state_change_time = event.get('state_change_time', datetime.utcnow().isoformat())

    print(f"🔷 [Async] LangGraph 분석 시작: {alert_name}")

    # SNS 수신 시 이미 조회한 메모리 결과 재사용 (중복 조회 방지)
    memory_stats     = event.get('memory_stats', {})
    similar_incidents = event.get('similar_incidents', [])

    app = build_graph()
    graph_state = app.invoke({
        "question": question,
        "alert_name": alert_name,
        "severity": severity,
        "amp_link": "",
        "category": [],
        "memory_stats": memory_stats,
        "similar_incidents": similar_incidents,
        "final_answer": "",
        "session_id": "",
        "state_change_time": state_change_time,
        "slack_ts": slack_ts,
        "slack_channel": slack_channel,
    })

    final_answer = graph_state.get("final_answer", "{}")
    try:
        report_dict = json.loads(final_answer)
    except Exception:
        report_dict = {}

    result = {
        'report': report_dict,
        'session_id': graph_state.get("session_id", ""),
        'stats': graph_state.get("memory_stats", {'is_new': True, 'count': 0}),
        'similar': graph_state.get("similar_incidents", [])
    }

    # Slack 메시지 업데이트 (분석 완료 내용 + 조치완료 버튼만 남김)
    send_to_slack(alert_name, result, message_ts=slack_ts, channel=slack_channel)
    print(f"✅ [Async] 분석 완료 및 Slack 업데이트")

    return {'statusCode': 200, 'body': 'analysis complete'}


# ============================================================
# Lambda Handler
# ============================================================
def lambda_handler(event, context):
    """
    1. SNS 알람 → 단순 요약 Slack (분석은 버튼 클릭 시)
    2. Slack 분석요청 버튼 → Lambda self-invoke → LangGraph → Slack 업데이트
    3. Slack 조치완료 버튼 → DynamoDB 기록 → Slack 업데이트
    """

    print(f"📥 이벤트 수신: {json.dumps(event)[:500]}")

    # ============================================================
    # 비동기 분석 실행 (분석 요청 버튼 → Lambda self-invoke)
    # ============================================================
    if event.get('_action') == 'run_analysis':
        print(f"🔷 비동기 분석 실행")
        return _run_analysis_async(event)

    # ============================================================
    # [DEBUG/TEST] 자유 질문 직접 분석 (툴 호출 품질 테스트용)
    # 사용법: aws lambda invoke --payload '{"_action":"debug_query","query":"질문"}'
    # ============================================================
    if event.get('_action') == 'debug_query':
        query = event.get('query', '')
        environment = event.get('environment', 'prod')
        print(f"🧪 [DEBUG] 자유 질문 분석 시작: {query}")

        slack_token = os.environ.get('SLACK_BOT_TOKEN', '')
        channel = os.environ.get('SLACK_CHANNEL', SLACK_CHANNEL)

        # Slack에 시작 알림
        def _post(text):
            try:
                data = json.dumps({'channel': channel, 'text': text}).encode()
                req = urllib.request.Request(
                    'https://slack.com/api/chat.postMessage', data=data,
                    headers={'Authorization': f'Bearer {slack_token}', 'Content-Type': 'application/json'}
                )
                urllib.request.urlopen(req)
            except Exception as e:
                print(f"⚠️ Slack 전송 오류: {e}")

        _post(f"🧪 *[DEBUG 분석 시작]*\n질문: {query}\n환경: {environment}")

        try:
            app = build_graph()
            graph_state = app.invoke({
                "question": f"[DEBUG] deployment_environment={environment} {query}",
                "alert_name": "DebugQuery",
                "severity": "low",
                "amp_link": "",
                "category": [],
                "memory_stats": {},
                "similar_incidents": [],
                "final_answer": "",
                "session_id": "",
                "state_change_time": datetime.utcnow().isoformat(),
                "slack_ts": "",
                "slack_channel": channel,
            })

            final_answer = graph_state.get("final_answer", "{}")
            try:
                report = json.loads(final_answer)
            except Exception:
                report = {"summary": final_answer}

            root_cause = report.get("root_cause", report.get("incident_summary", report.get("summary", "분석 결과 없음")))
            evidence = report.get("evidence", {})
            if isinstance(evidence, dict):
                evidence_text = "\n".join(
                    f"• *{k}*: {v}" for k, v in evidence.items()
                    if v and v != "not queried"
                ) or "없음"
            elif isinstance(evidence, list):
                evidence_text = "\n".join(f"• {e}" for e in evidence[:5]) or "없음"
            else:
                evidence_text = str(evidence) if evidence else "없음"

            _post(
                f"🧪 *[DEBUG 분석 완료]*\n"
                f"*질문:* {query}\n"
                f"*결론:* {root_cause}\n"
                f"*근거:*\n{evidence_text}"
            )
            print(f"✅ [DEBUG] 분석 완료")
        except Exception as e:
            import traceback
            _post(f"❌ [DEBUG 분석 실패] {e}")
            traceback.print_exc()

        return {'statusCode': 200, 'body': 'debug_query complete'}

    # ============================================================
    # Slack POST 이벤트 처리 (Function URL) — 슬래시 커맨드 or 버튼 클릭
    # ============================================================
    if 'requestContext' in event and event.get('requestContext', {}).get('http', {}).get('method') == 'POST':
        body_raw = event.get('body', '') or ''
        if event.get('isBase64Encoded'):
            import base64
            body_bytes = base64.b64decode(body_raw)
        else:
            body_bytes = body_raw.encode('utf-8')

        # 슬래시 커맨드 감지 (payload= 없고 command= 있으면)
        if b'command=%2Fanalyze' in body_bytes or b'command=/analyze' in body_bytes:
            print("🔍 슬래시 커맨드 /analyze 감지")
            return _handle_slash_command(event, body_bytes)

        print(f"🔘 Slack interaction 이벤트 감지")
        return _handle_slack_interaction(event)

    try:
        # SNS 메시지 파싱
        sns_record = event['Records'][0]['Sns']
        message_text = sns_record['Message']
        subject = sns_record.get('Subject') or ''
        
        print(f"📧 Subject: {subject}")
        print(f"📄 Message: {message_text[:200]}...")
        
        # Alertmanager 메시지 파싱
        # Subject 형식: "[FIRING:1] HighHttpErrorRate (GET 405 /error ...)"
        # Message 형식: 텍스트 (JSON 아님)
        
        # Alert 이름 추출 (FIRING, RESOLVED 둘 다 처리)
        # Subject 형식: "[FIRING:1] HighHttpErrorRate (...)" 또는 "[RESOLVED] HighHttpErrorRate (...)"
        alert_name = 'Unknown'
        if '[FIRING' in subject or '[RESOLVED' in subject:
            parts = subject.split(']', 1)
            if len(parts) > 1:
                alert_name = parts[1].strip().split('(')[0].strip()
        
        # 상태 판단
        if '[FIRING' in subject:
            new_state = 'ALARM'
        elif '[RESOLVED' in subject:
            new_state = 'OK'
        else:
            new_state = 'ALARM'  # 기본값
        
        # Severity 추출 (MessageAttributes에서)
        severity = sns_record.get('MessageAttributes', {}).get('severity', {}).get('Value', 'high')
        
        state_change_time = sns_record.get('Timestamp', datetime.utcnow().isoformat())
        
        print(f"🚨 알람: {alert_name} ({new_state}, severity: {severity})")
        
        # ============================================================
        # ALARM 상태: 사실 기반 단순 요약만 전송 (분석은 버튼 클릭 시)
        # ============================================================
        if new_state == 'ALARM':
            # 이미 ongoing 인시던트 존재하면 중복 전송 방지
            existing = get_ongoing_incident(alert_name)
            if existing:
                print(f"⏭️ 이미 ongoing 인시던트 존재, 스킵: {alert_name}")
                return {'statusCode': 200, 'body': 'Skipped (already ongoing)'}

            # 서비스 정보 추출 (Subject에서)
            service_info = _extract_service_info(subject)

            # SNS 메시지에서 description 추출 (AI 없음, 순수 파싱)
            description = _extract_alert_description(message_text, alert_name, service_info)

            # AgentCore Memory 빠른 조회 (유사 과거 사례 초기 메시지에 포함)
            similar_info = None
            try:
                mem_stats, mem_similar = lookup_memory(alert_name)
                SIMILARITY_THRESHOLD = 0.5
                high_sim = [
                    s for s in mem_similar
                    if s.get('similarity_score', 0) >= SIMILARITY_THRESHOLD
                    and s.get('resolution') not in ('', 'ongoing', 'Unknown', None)
                ]
                if high_sim and not mem_stats.get('is_new'):
                    best = high_sim[0]
                    similar_info = {
                        'count':       mem_stats.get('count', 0),
                        'avg_minutes': mem_stats.get('avg_resolution_time', 0),
                        'root_cause':  best.get('root_cause', ''),
                        'resolution':  best.get('resolution', ''),
                    }
            except Exception as e:
                print(f"⚠️ [lookup_memory] 초기 조회 실패 (무시): {e}")

            # 단순 요약 메시지 전송
            simple_msg = build_simple_alert_message(
                alert_name=alert_name,
                severity=severity,
                service_info=service_info,
                description=description,
                detected_at=_now_kst(),
                similar_info=similar_info,
            )
            simple_msg['channel'] = SLACK_CHANNEL
            message_ts = _send_slack_message_and_get_ts(simple_msg)
            print(f"📤 단순 요약 메시지 전송 완료 (ts: {message_ts})")

            # DynamoDB에 ongoing 저장 (분석 버튼 클릭 시 필요한 정보 포함)
            put_ongoing_incident(alert_name, {
                'severity': severity,
                'state_change_time': state_change_time,
                'slack_ts': message_ts,
                'slack_channel': SLACK_CHANNEL,
                'question': message_text,
                'root_cause': '',
                'error_messages': [],
                'memory_stats': mem_stats if similar_info is not None else {},
                'similar_incidents': mem_similar if similar_info is not None else [],
            })

            return {
                'statusCode': 200,
                'body': json.dumps({'message': '알람 감지 - 단순 요약 전송 완료'})
            }
        
        # ============================================================
        # OK 상태: 해결 정보 업데이트 (Phase 3: AgentCore primary, AOSS secondary)
        # ============================================================
        elif new_state == 'OK':
            # AMP 자동 복구 감지 → DynamoDB 정리만, Slack 메시지 없음
            # (조치 완료는 담당자가 버튼으로 직접 처리)
            recent_incident = get_ongoing_incident(alert_name)

            if recent_incident:
                try:
                    start_time = datetime.fromisoformat(recent_incident['timestamp'].replace('Z', '+00:00'))
                    end_time = datetime.fromisoformat(state_change_time.replace('Z', '+00:00'))
                    resolution_minutes = (end_time - start_time).total_seconds() / 60
                except Exception:
                    resolution_minutes = 0

                # AgentCore 해결 이력 저장
                try:
                    AgentCoreMemory().save_incident({
                        'alert_name': alert_name,
                        'severity': recent_incident.get('severity', 'high'),
                        'root_cause': recent_incident.get('root_cause', ''),
                        'resolution': '자동 복구 (AMP 감지)',
                        'resolution_time': resolution_minutes,
                        'status': 'resolved',
                        'error_messages': recent_incident.get('error_messages', []),
                        'state_change_time': state_change_time,
                    })
                except Exception as _e:
                    print(f"⚠️ [AgentCore] resolved 저장 실패: {_e}")

                delete_ongoing_incident(alert_name)
                print(f"✅ 자동 복구 감지 - DynamoDB 정리 완료: {resolution_minutes:.1f}분 소요")
            else:
                print(f"⚠️ 해결할 ongoing 장애를 찾을 수 없음 (DynamoDB)")
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': '장애 해결 업데이트',
                    'resolution_time_minutes': resolution_minutes if recent_incident else 0
                })
            }
        
        # ============================================================
        # 기타 상태 (INSUFFICIENT_DATA 등)
        # ============================================================
        else:
            print(f"⏭️ {new_state} 상태, 스킵")
            return {'statusCode': 200, 'body': f'Skipped ({new_state})'}
        
    except Exception as e:
        print(f"❌ 오류: {e}")
        import traceback
        traceback.print_exc()
        
        return {
            'statusCode': 500,
            'body': json.dumps({'error': str(e)})
        }