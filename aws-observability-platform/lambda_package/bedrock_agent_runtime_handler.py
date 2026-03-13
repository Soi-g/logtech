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
from datetime import datetime

# LangGraph 임포트
from graph_agent_with_memory import build_graph

# IncidentMemory 임포트 (AOSS - primary)
from incident_memory import IncidentMemory

# AgentCore Memory 임포트 (Phase 1: 병렬 운영)
from agentcore_memory import AgentCoreMemory

# Slack 템플릿 임포트
from slack_templates import (
    build_alert_message, 
    build_incident_report_message, 
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
# Strands Agents로 데이터 수집
# ============================================================
def collect_observability_data(question: str) -> dict:
    """
    Strands Agents를 사용하여 메트릭/로그/트레이스 수집
    """
    print(f"📊 Strands Agents로 데이터 수집 중...")
    
    results = {}
    
    try:
        # 메트릭 수집
        print(f"  - Metrics Agent 실행")
        metrics_result = metrics_agent(question)
        results['metrics'] = str(metrics_result)
    except Exception as e:
        print(f"  ⚠️ Metrics 수집 실패: {e}")
        results['metrics'] = f"수집 실패: {e}"
    
    try:
        # 로그 수집
        print(f"  - Logs Agent 실행")
        logs_result = logs_agent(question)
        results['logs'] = str(logs_result)
    except Exception as e:
        print(f"  ⚠️ Logs 수집 실패: {e}")
        results['logs'] = f"수집 실패: {e}"
    
    try:
        # 트레이스 수집
        print(f"  - Traces Agent 실행")
        traces_result = traces_agent(question)
        results['traces'] = str(traces_result)
    except Exception as e:
        print(f"  ⚠️ Traces 수집 실패: {e}")
        results['traces'] = f"수집 실패: {e}"
    
    print(f"✅ 데이터 수집 완료")
    return results


# ============================================================
# Bedrock Agent 호출
# ============================================================
def invoke_agent(alert_name: str, alert_description: str, severity: str = "medium") -> dict:
    """
    Bedrock Agent Runtime 호출
    
    Args:
        alert_name: 알람명
        alert_description: 알람 설명
        severity: 심각도
    
    Returns:
        Agent 응답 결과
    """
    
    # 1. 과거 이력 조회
    memory = IncidentMemory()
    stats = memory.get_stats(alert_name)
    similar = memory.search_similar_incidents(alert_name, limit=3)

    # 1-1. 런북 직접 검색
    runbooks = search_runbooks(alert_name)
    
    # 2. 컨텍스트 구성
    context = f"""
🚨 알람 발생
알람명: {alert_name}
심각도: {severity}
설명: {alert_description}

📊 과거 장애 이력:
"""
    
    if stats['is_new']:
        context += "⚠️ 신규 장애 패턴 (과거 이력 없음)\n"
    else:
        context += f"""
✅ 과거 {stats['count']}회 발생
📈 평균 해결 시간: {stats['avg_resolution_time']:.1f}분
🔍 가장 흔한 원인: {stats['most_common_cause']}

최근 3건:
"""
        for idx, incident in enumerate(similar[:3], 1):
            context += f"""
{idx}. {incident['timestamp']}
   원인: {incident.get('root_cause', 'Unknown')}
   해결: {incident.get('resolution', 'Unknown')}
   소요: {incident.get('resolution_time_minutes', 0)}분
"""
    
    context += """

📚 관련 런북:
{runbook_context}

📋 분석 요청:
1. 위 런북 내용을 참고하여 근본 원인 파악
2. 과거 이력을 참조하여 빠른 해결 방법 제시
3. 즉시 조치 및 후속 조치 권장

반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{
  "incident_summary": "한 문장 요약",
  "is_recurring": true/false,
  "past_occurrences": 숫자,
  "likely_root_causes": ["원인1", "원인2"],
  "severity": "심각도",
  "impact": "영향 범위",
  "immediate_actions": ["즉시 조치1", "즉시 조치2"],
  "follow_up_actions": ["후속 조치1"],
  "evidence_summary": ["근거1", "근거2"],
  "runbook_references": [
    {{"title": "런북파일명.md", "summary": "핵심 대응 절차"}}
  ]
}}

⚠️ 주의: runbook_references에는 위 런북 섹션에서 제공된 런북만 포함하세요. 런북이 없으면 빈 배열 []로 두세요.
"""
    
    # 2-1. 런북 컨텍스트 구성
    if runbooks:
        runbook_context = ""
        for rb in runbooks:
            runbook_context += f"[{rb['title']}]\n{rb['content']}\n\n"
    else:
        runbook_context = "검색된 런북 없음"
    context = context.replace("{runbook_context}", runbook_context)

    # 3. 세션 ID 생성 (공백 제거)
    session_id = f"incident-{alert_name.replace(' ', '-')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    
    print(f"🤖 Bedrock Agent 호출 시작")
    print(f"   Agent ID: {AGENT_ID}")
    print(f"   Session ID: {session_id}")
    print(f"   과거 이력: {stats['count']}건")
    
    try:
        # 4. Agent 호출
        response = bedrock_agent_runtime.invoke_agent(
            agentId=AGENT_ID,
            agentAliasId=AGENT_ALIAS_ID,
            sessionId=session_id,
            enableTrace=True,
            inputText=context
        )
        
        # 5. 응답 스트림 처리
        result_text = ""
        trace_data = []
        
        for event in response['completion']:
            if 'chunk' in event:
                chunk = event['chunk']
                if 'bytes' in chunk:
                    result_text += chunk['bytes'].decode('utf-8')
            
            if 'trace' in event:
                trace_data.append(event['trace'])
        
        print(f"✅ Agent 응답 완료 ({len(result_text)} 글자)")
        
        # 6. JSON 파싱 (개선된 로직)
        try:
            # 1단계: 마크다운 코드 블록 제거
            cleaned_text = result_text.strip()
            
            if '```json' in cleaned_text:
                cleaned_text = cleaned_text.split('```json')[1].split('```')[0].strip()
            elif '```' in cleaned_text:
                cleaned_text = cleaned_text.split('```')[1].split('```')[0].strip()
            
            # 2단계: JSON 객체 추출 (중괄호 기준)
            if '{' in cleaned_text and '}' in cleaned_text:
                start_idx = cleaned_text.find('{')
                end_idx = cleaned_text.rfind('}') + 1
                cleaned_text = cleaned_text[start_idx:end_idx]
            
            # 3단계: JSON 파싱
            report = json.loads(cleaned_text)
            print(f"✅ JSON 파싱 성공")
            
            # runbook_references가 비어있으면 직접 채움
            if not report.get("runbook_references") and runbooks:
                seen = set()
                unique_runbooks = []
                for rb in runbooks:
                    if rb["title"] not in seen:
                        seen.add(rb["title"])
                        unique_runbooks.append(rb)
                report["runbook_references"] = [
                    {"title": rb["title"], "summary": rb["title"].replace(".md", "") + " 런북 참조"}
                    for rb in unique_runbooks
                ]
                print(f"📚 runbook_references 직접 주입: {len(runbooks)}건")
            
        except json.JSONDecodeError as e:
            print(f"⚠️ JSON 파싱 실패: {e}")
            print(f"   원본 텍스트 (처음 500자): {result_text[:500]}")
            
            # Fallback: 텍스트 응답을 구조화
            report = {
                "incident_summary": f"{alert_name} 알람 발생 - AI 분석 완료",
                "is_recurring": not stats['is_new'],
                "past_occurrences": stats['count'],
                "likely_root_causes": [stats.get('most_common_cause', '분석 필요')],
                "severity": severity,
                "impact": "영향 범위 파악 중",
                "immediate_actions": ["시스템 로그 확인", "담당자 호출"],
                "follow_up_actions": ["근본 원인 분석"],
                "evidence_summary": [f"AI 응답: {result_text[:200]}..."],
                "runbook_references": [],
                "raw_response": result_text  # 원본 응답 포함
            }
        
        return {
            'session_id': session_id,
            'report': report,
            'stats': stats,
            'trace': trace_data[:5] if trace_data else []  # 처음 5개만
        }
        
    except Exception as e:
        print(f"❌ Agent 호출 실패: {e}")
        raise


# ============================================================
# Slack 전송
# ============================================================
def send_to_slack(alert_name: str, result: dict, message_ts: str = None):
    """Slack으로 분석 결과 전송 (slack_templates.py 사용)
    
    Args:
        alert_name: 알람명
        result: AI 분석 결과
        message_ts: 업데이트할 메시지의 타임스탬프 (있으면 업데이트, 없으면 새 메시지)
    """
    
    report_dict = result['report']
    stats = result['stats']
    session_id = result['session_id']
    
    # 재발 여부 표시
    if stats['is_new']:
        history_info = "⚠️ 신규 장애 패턴"
    else:
        history_info = f"🔄 재발 ({stats['count']}회 발생, 평균 {stats['avg_resolution_time']:.0f}분 소요)"
    
    # IncidentReport 객체 생성
    try:
        # RunbookReference 객체 생성
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
            severity=report_dict.get('severity', 'medium'),
            impact=report_dict.get('impact', 'N/A'),
            immediate_actions=report_dict.get('immediate_actions', []),
            follow_up_actions=report_dict.get('follow_up_actions', []),
            evidence_summary=report_dict.get('evidence_summary', []),
            runbook_references=runbook_refs
        )
        
        # 알람 정보 (과거 이력 포함)
        alert_info = f"{alert_name}\n{history_info}"
        
        # Slack 메시지 생성
        message = build_incident_report_message(
            alert_info=alert_info,
            report=incident_report,
            detected_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        )
        
        # 채널 추가
        message['channel'] = SLACK_CHANNEL
        
        # 메시지 업데이트인 경우 ts 추가
        if message_ts:
            message['ts'] = message_ts
        
        # 세션 정보 추가 (컨텍스트에)
        if message.get('attachments') and message['attachments'][0].get('blocks'):
            blocks = message['attachments'][0]['blocks']
            # 마지막 context 블록 수정
            for block in reversed(blocks):
                if block.get('type') == 'context':
                    block['elements'][0]['text'] += f" | Session: `{session_id}`"
                    break
        
        if message_ts:
            _update_slack_message(message)
        else:
            _send_slack_message(message)
        
    except Exception as e:
        print(f"⚠️ Slack 템플릿 생성 실패: {e}")
        # Fallback: 간단한 메시지
        fallback_message = {
            "channel": SLACK_CHANNEL,
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

        action = payload.get('actions', [{}])[0]
        action_id = action.get('action_id', '')
        alert_name = action.get('value', '')
        user_name = payload.get('user', {}).get('name', 'unknown')
        channel_id = payload.get('channel', {}).get('id', SLACK_CHANNEL)
        message_ts = payload.get('message', {}).get('ts', '')

        print(f"🔘 버튼 클릭: action={action_id}, alert={alert_name}, user={user_name}")

        if action_id == 'resolve_incident':
            # AOSS에서 ongoing 인시던트 찾아서 resolved 업데이트
            memory = IncidentMemory()
            ongoing = memory.get_recent_ongoing_incident(alert_name)

            if ongoing:
                incident_id = ongoing.get('incident_id', '')
                detected_at_str = ongoing.get('detected_at', '')
                try:
                    detected_at = datetime.fromisoformat(detected_at_str.replace('Z', '+00:00'))
                    resolution_minutes = (datetime.utcnow() - detected_at.replace(tzinfo=None)).total_seconds() / 60
                except Exception:
                    resolution_minutes = 0

                memory.update_incident_resolution(
                    incident_id=incident_id,
                    resolution=f'수동 해결 완료 by @{user_name}',
                    resolution_time_minutes=resolution_minutes
                )
                print(f'✅ AOSS resolved 업데이트 완료: {incident_id} ({resolution_minutes:.0f}분)')
                status_text = f'✅ *해결 완료* — `{alert_name}`\n해결자: @{user_name} | 소요시간: {resolution_minutes:.0f}분 | {datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")}'
            else:
                print(f'⚠️ AOSS에서 ongoing 인시던트 못 찾음: {alert_name}')
                status_text = f'⚠️ 진행 중인 인시던트를 찾지 못했습니다: `{alert_name}`'

            # Slack 메시지에 해결 완료 표시 추가
            _post_resolve_comment(channel_id, message_ts, status_text)

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


# ============================================================
# Lambda Handler
# ============================================================
def lambda_handler(event, context):
    """
    1. SNS 알람 → LangGraph → Slack
    2. Slack 버튼 클릭 → AOSS resolved 업데이트
    """
    
    print(f"📥 이벤트 수신: {json.dumps(event)}")

    # ============================================================
    # Slack 버튼 클릭 이벤트 처리 (Function URL)
    # ============================================================
    # Slack 버튼 클릭 (Function URL로 들어오는 HTTP POST)
    if 'requestContext' in event and event.get('requestContext', {}).get('http', {}).get('method') == 'POST':
        print(f"🔘 Slack interaction 이벤트 감지")
        return _handle_slack_interaction(event)

    try:
        # SNS 메시지 파싱
        sns_record = event['Records'][0]['Sns']
        message_text = sns_record['Message']
        subject = sns_record.get('Subject', '')
        
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
        
        memory = IncidentMemory()
        
        # ============================================================
        # ALARM 상태: 분석 + 장기 메모리 저장
        # ============================================================
        if new_state == 'ALARM':
            # 1. "분석 중..." 메시지 먼저 전송
            analyzing_message = build_alert_message(
                alert_info=alert_name,
                severity=severity
            )
            analyzing_message['channel'] = SLACK_CHANNEL
            
            # 메시지 전송 후 ts 받기
            message_ts = _send_slack_message_and_get_ts(analyzing_message)
            print(f"📤 '분석 중...' 메시지 전송 완료 (ts: {message_ts})")
            
            # 2. LangGraph 실행 (Agent as Tools)
            # Phase 2: memory_save_node가 그래프 내부에서 메모리 저장을 처리함
            print(f"🔷 LangGraph 실행 시작")
            app = build_graph()
            graph_state = app.invoke({
                "question": message_text,
                "alert_name": alert_name,
                "severity": severity,
                "amp_link": "",
                "category": [],
                "memory_stats": {},
                "similar_incidents": [],
                "collection_result": "",
                "metrics_analysis": "",
                "logs_analysis": "",
                "traces_analysis": "",
                "analysis_result": "",
                "runbook_result": "",
                "session_id": "",
                "state_change_time": state_change_time,  # Phase 2: 전달
                "final_answer": "",
            })

            # graph 결과를 기존 result 형식으로 변환
            final_answer = graph_state.get("final_answer", "{}")
            try:
                report_dict = json.loads(final_answer)
            except Exception:
                report_dict = {}

            result = {
                'report': report_dict,
                'session_id': graph_state.get("session_id", ""),
                'stats': graph_state.get("memory_stats", {'is_new': True, 'count': 0})
            }

            # 3. Slack 메시지 업데이트 (같은 ts로)
            send_to_slack(alert_name, result, message_ts=message_ts)
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': '분석 완료 및 메모리 저장',
                    'session_id': result['session_id'],
                    'is_recurring': not result['stats']['is_new']
                })
            }
        
        # ============================================================
        # OK 상태: 해결 정보 업데이트 (Phase 3: AgentCore primary, AOSS secondary)
        # ============================================================
        elif new_state == 'OK':
            resolution_minutes = 0

            # AgentCore에서 ongoing 인시던트 조회 (primary)
            recent_incident = None
            agentcore_source = False
            try:
                agentcore_mem = AgentCoreMemory()
                if agentcore_mem.memory_id:
                    recent_incident = agentcore_mem.get_recent_ongoing_incident(alert_name)
                    if recent_incident:
                        agentcore_source = True
            except Exception as _e:
                print(f"⚠️ [AgentCore] ongoing 조회 실패, AOSS 폴백: {_e}")

            # AgentCore 실패 또는 미설정 시 AOSS 폴백
            if not recent_incident:
                recent_incident = memory.get_recent_ongoing_incident(alert_name)

            if recent_incident:
                # 해결 시간 계산
                try:
                    start_time = datetime.fromisoformat(recent_incident['timestamp'].replace('Z', '+00:00'))
                    end_time = datetime.fromisoformat(state_change_time.replace('Z', '+00:00'))
                    resolution_minutes = (end_time - start_time).total_seconds() / 60
                except Exception:
                    resolution_minutes = 0

                incident_id = recent_incident.get('incident_id', '')
                severity = recent_incident.get('severity', 'high')
                root_cause = recent_incident.get('root_cause', '')

                # AgentCore 해결 이력 저장 (primary)
                try:
                    agentcore_mem = AgentCoreMemory()
                    agentcore_mem.save_incident({
                        'alert_name': alert_name,
                        'severity': severity,
                        'root_cause': root_cause,
                        'resolution': '자동 복구',
                        'resolution_time': resolution_minutes,
                        'status': 'resolved',
                        'error_messages': recent_incident.get('error_messages', []),
                        'state_change_time': state_change_time,
                    })
                except Exception as _e:
                    print(f"⚠️ [AgentCore] resolved 저장 실패: {_e}")

                # AOSS 해결 정보 업데이트 (secondary)
                try:
                    if not agentcore_source:
                        # AOSS에서 찾은 경우 update
                        memory.update_incident_resolution(
                            incident_id=incident_id,
                            resolution='자동 복구',
                            resolution_time_minutes=resolution_minutes
                        )
                    else:
                        # AgentCore에서 찾은 경우 AOSS도 조회 후 update
                        aoss_incident = memory.get_recent_ongoing_incident(alert_name)
                        if aoss_incident:
                            memory.update_incident_resolution(
                                incident_id=aoss_incident.get('incident_id', ''),
                                resolution='자동 복구',
                                resolution_time_minutes=resolution_minutes
                            )
                except Exception as _e:
                    print(f"⚠️ [AOSS] resolved 업데이트 실패: {_e}")

                print(f"✅ 장애 해결 업데이트: {resolution_minutes:.1f}분 소요")
                send_resolution_to_slack(alert_name, resolution_minutes)
            else:
                print(f"⚠️ 해결할 ongoing 장애를 찾을 수 없음 (AgentCore + AOSS 모두 없음)")
            
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