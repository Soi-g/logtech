"""
DynamoDB - ongoing 인시던트 상태 추적
Phase 4: AOSS incident_memory 대체
"""
import os
import boto3
from datetime import datetime, timezone, timedelta

AWS_REGION = os.environ.get('AWS_REGION_NAME', 'ap-northeast-2')
TABLE_NAME = os.environ.get('DYNAMODB_INCIDENT_TABLE', '')

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client('dynamodb', region_name=AWS_REGION)
    return _client


def get_ongoing_incident(alert_name: str) -> dict | None:
    """ongoing 인시던트 조회"""
    if not TABLE_NAME:
        return None
    try:
        response = _get_client().get_item(
            TableName=TABLE_NAME,
            Key={'alert_name': {'S': alert_name}}
        )
        item = response.get('Item')
        if not item:
            return None
        return {
            'incident_id': item.get('incident_id', {}).get('S', ''),
            'timestamp': item.get('timestamp', {}).get('S', ''),
            'severity': item.get('severity', {}).get('S', 'high'),
            'root_cause': item.get('root_cause', {}).get('S', ''),
            'error_messages': [e['S'] for e in item.get('error_messages', {}).get('L', [])],
            'slack_ts': item.get('slack_ts', {}).get('S', ''),
            'slack_channel': item.get('slack_channel', {}).get('S', ''),
            'question': item.get('question', {}).get('S', ''),
            'agentcore_record_id': item.get('agentcore_record_id', {}).get('S', ''),
            'status': 'ongoing',
        }
    except Exception as e:
        print(f"⚠️ [DynamoDB] ongoing 조회 실패: {e}")
        return None


def put_ongoing_incident(alert_name: str, incident_data: dict):
    """ongoing 인시던트 저장 (24시간 TTL)"""
    if not TABLE_NAME:
        return
    try:
        now = datetime.now(timezone.utc)
        expires_at = int((now + timedelta(hours=24)).timestamp())
        incident_id = f"{alert_name}_{now.isoformat()}"
        error_messages = incident_data.get('error_messages', [])

        item = {
            'alert_name': {'S': alert_name},
            'incident_id': {'S': incident_id},
            'timestamp': {'S': incident_data.get('state_change_time', now.isoformat())},
            'severity': {'S': incident_data.get('severity', 'high')},
            'root_cause': {'S': incident_data.get('root_cause', '')},
            'error_messages': {'L': [{'S': e} for e in error_messages]},
            'expires_at': {'N': str(expires_at)},
        }

        # Slack 메시지 ts/channel 저장 (복구 시 원본 메시지 업데이트용)
        if incident_data.get('slack_ts'):
            item['slack_ts'] = {'S': incident_data['slack_ts']}
        if incident_data.get('slack_channel'):
            item['slack_channel'] = {'S': incident_data['slack_channel']}
        # SNS 원문 저장 (분석 버튼 클릭 시 LangGraph에 전달)
        if incident_data.get('question'):
            item['question'] = {'S': incident_data['question'][:4000]}  # DynamoDB 크기 제한 고려
        # AgentCore ongoing 레코드 ID (조치완료 시 삭제용)
        if incident_data.get('agentcore_record_id'):
            item['agentcore_record_id'] = {'S': incident_data['agentcore_record_id']}

        _get_client().put_item(TableName=TABLE_NAME, Item=item)
        print(f"✅ [DynamoDB] ongoing 저장: {alert_name}")
    except Exception as e:
        print(f"⚠️ [DynamoDB] ongoing 저장 실패: {e}")


def update_ongoing_root_cause(alert_name: str, root_cause: str):
    """분석 완료 후 root_cause 업데이트"""
    if not TABLE_NAME:
        return
    try:
        _get_client().update_item(
            TableName=TABLE_NAME,
            Key={'alert_name': {'S': alert_name}},
            UpdateExpression='SET root_cause = :rc',
            ExpressionAttributeValues={':rc': {'S': root_cause}},
        )
        print(f"✅ [DynamoDB] root_cause 업데이트: {alert_name}")
    except Exception as e:
        print(f"⚠️ [DynamoDB] root_cause 업데이트 실패: {e}")


def delete_ongoing_incident(alert_name: str):
    """ongoing 인시던트 삭제 (해결 시)"""
    if not TABLE_NAME:
        return
    try:
        _get_client().delete_item(
            TableName=TABLE_NAME,
            Key={'alert_name': {'S': alert_name}}
        )
        print(f"✅ [DynamoDB] ongoing 삭제: {alert_name}")
    except Exception as e:
        print(f"⚠️ [DynamoDB] ongoing 삭제 실패: {e}")
