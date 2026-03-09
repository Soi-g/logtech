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

# Strands Agents 임포트
from agents_aws import metrics_agent, logs_agent, traces_agent

# IncidentMemory 임포트
from incident_memory import IncidentMemory

# ============================================================
# 클라이언트 초기화
# ============================================================
bedrock_agent_runtime = boto3.client('bedrock-agent-runtime', region_name='ap-northeast-2')

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

📋 분석 요청:
1. 현재 메트릭, 로그, 트레이스를 조회하여 근본 원인 파악
2. 과거 이력을 참조하여 빠른 해결 방법 제시
3. 즉시 조치 및 후속 조치 권장
4. 관련 런북 참조

반드시 JSON 형식으로 응답하세요:
{
  "incident_summary": "한 문장 요약",
  "is_recurring": true/false,
  "past_occurrences": 숫자,
  "likely_root_causes": ["원인1", "원인2"],
  "severity": "심각도",
  "impact": "영향 범위",
  "immediate_actions": ["즉시 조치1", "즉시 조치2"],
  "follow_up_actions": ["후속 조치1"],
  "evidence_summary": ["근거1", "근거2"],
  "runbook_references": [{"source": "파일명", "section": "섹션", "relevance": "관련성"}]
}
"""
    
    # 3. 세션 ID 생성
    session_id = f"incident-{alert_name}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    
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
def send_to_slack(alert_name: str, result: dict):
    """Slack으로 분석 결과 전송"""
    
    report = result['report']
    stats = result['stats']
    session_id = result['session_id']
    
    # 재발 여부 표시
    if stats['is_new']:
        history_badge = "⚠️ 신규 장애 패턴"
    else:
        history_badge = f"🔄 재발 ({stats['count']}회 발생, 평균 {stats['avg_resolution_time']:.0f}분 소요)"
    
    # Slack 메시지 구성
    message = {
        "channel": SLACK_CHANNEL,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚨 {alert_name}"
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*심각도:* {report.get('severity', 'medium')}"},
                    {"type": "mrkdwn", "text": f"*과거 이력:* {history_badge}"}
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*📝 요약*\n{report.get('incident_summary', 'N/A')}"
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*🔍 가능한 원인*\n" + "\n".join([f"• {cause}" for cause in report.get('likely_root_causes', [])])
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*⚡ 즉시 조치*\n" + "\n".join([f"{i+1}. {action}" for i, action in enumerate(report.get('immediate_actions', []))])
                }
            }
        ]
    }
    
    # 런북 참조 추가
    runbooks = report.get('runbook_references', [])
    if runbooks:
        runbook_text = "*📖 관련 런북*\n"
        for rb in runbooks[:3]:
            runbook_text += f"• [{rb.get('source', 'Unknown')}] {rb.get('section', '')}\n"
        
        message['blocks'].append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": runbook_text}
        })
    
    # 세션 정보
    message['blocks'].append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"Session: `{session_id}`"}
        ]
    })
    
    _send_slack_message(message)


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
    """Slack API 호출 헬퍼"""
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


# ============================================================
# Lambda Handler
# ============================================================
def lambda_handler(event, context):
    """
    SNS 알람 → Bedrock Agent → Slack
    
    메모리 전환 전략:
    1. ALARM 상태: 초기 분석 + AOSS 저장 (status: ongoing)
    2. OK 상태: 해결 정보 업데이트 (status: resolved)
    """
    
    print(f"📥 이벤트 수신: {json.dumps(event)}")
    
    try:
        # SNS 메시지 파싱
        sns_message = json.loads(event['Records'][0]['Sns']['Message'])
        
        alert_name = sns_message.get('AlarmName', 'Unknown')
        alert_description = sns_message.get('AlarmDescription', '')
        new_state = sns_message.get('NewStateValue', 'ALARM')
        state_change_time = sns_message.get('StateChangeTime', datetime.utcnow().isoformat())
        
        print(f"🚨 알람: {alert_name} ({new_state})")
        
        memory = IncidentMemory()
        
        # ============================================================
        # ALARM 상태: 분석 + 장기 메모리 저장
        # ============================================================
        if new_state == 'ALARM':
            # Bedrock Agent 호출
            result = invoke_agent(
                alert_name=alert_name,
                alert_description=alert_description,
                severity='high'
            )
            
            # Slack 전송
            send_to_slack(alert_name, result)
            
            # 장기 메모리 저장 (초기 분석)
            report = result['report']
            incident_data = {
                'alert_name': alert_name,
                'severity': report.get('severity', 'high'),
                'root_cause': ', '.join(report.get('likely_root_causes', [])),
                'resolution': 'ongoing',  # 아직 미해결
                'resolution_time': 0,
                'metrics': {
                    'session_id': result['session_id'],
                    'is_recurring': not result['stats']['is_new'],
                    'past_occurrences': result['stats']['count']
                },
                'error_messages': report.get('evidence_summary', []),
                'state_change_time': state_change_time,
                'status': 'ongoing'
            }
            
            memory.save_incident(incident_data)
            print(f"✅ 장기 메모리 저장 완료 (status: ongoing)")
            
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': '분석 완료 및 메모리 저장',
                    'session_id': result['session_id'],
                    'is_recurring': not result['stats']['is_new']
                })
            }
        
        # ============================================================
        # OK 상태: 해결 정보 업데이트
        # ============================================================
        elif new_state == 'OK':
            # 가장 최근 ongoing 장애 조회
            recent_incident = memory.get_recent_ongoing_incident(alert_name)
            
            if recent_incident:
                # 해결 시간 계산
                start_time = datetime.fromisoformat(recent_incident['timestamp'].replace('Z', '+00:00'))
                end_time = datetime.fromisoformat(state_change_time.replace('Z', '+00:00'))
                resolution_minutes = (end_time - start_time).total_seconds() / 60
                
                # 해결 정보 업데이트
                memory.update_incident_resolution(
                    incident_id=recent_incident['incident_id'],
                    resolution='자동 복구',  # 실제로는 Bedrock Agent에게 물어볼 수도 있음
                    resolution_time_minutes=resolution_minutes
                )
                
                print(f"✅ 장애 해결 업데이트: {resolution_minutes:.1f}분 소요")
                
                # Slack 알림 (선택)
                send_resolution_to_slack(alert_name, resolution_minutes)
            else:
                print(f"⚠️ 해결할 ongoing 장애를 찾을 수 없음")
            
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
