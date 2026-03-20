"""
DynamoDB 대화 저장소
- chatbot-conversations: 대화 메타데이터 (PK: conversation_id)
- chatbot-messages:      대화 메시지 + 요약 (PK: conversation_id, SK: message_id)
"""

import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

AWS_REGION = os.environ.get("AWS_REGION_NAME", "ap-northeast-2")
CONVERSATIONS_TABLE = os.environ.get("CHATBOT_CONVERSATIONS_TABLE", "")
MESSAGES_TABLE = os.environ.get("CHATBOT_MESSAGES_TABLE", "")

# 요약 전략 설정
SUMMARY_THRESHOLD = 40   # 비요약 메시지가 이 수를 초과하면 요약 생성
RECENT_TURNS = 20        # 요약 후 최근 몇 턴을 보존

_dynamodb = None


def _db():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
    return _dynamodb


def _conv_table():
    return _db().Table(CONVERSATIONS_TABLE)


def _msg_table():
    return _db().Table(MESSAGES_TABLE)


def init_db():
    pass  # DynamoDB 테이블은 Terraform으로 생성


# ── Conversations ──────────────────────────────────────────────

def list_conversations() -> list[dict]:
    resp = _conv_table().scan()
    items = resp.get("Items", [])
    return sorted(
        [_fmt_conv(i) for i in items],
        key=lambda x: x["updated_at"],
        reverse=True,
    )


def create_conversation(first_message: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    cid = str(uuid.uuid4())
    title = first_message[:40] + "..." if len(first_message) > 40 else first_message or "새 대화"
    _conv_table().put_item(Item={
        "conversation_id": cid,
        "title": title,
        "created_at": now,
        "updated_at": now,
    })
    return {"id": cid, "title": title, "created_at": now, "updated_at": now}


def get_conversation(cid: str) -> dict | None:
    resp = _conv_table().get_item(Key={"conversation_id": cid})
    item = resp.get("Item")
    return _fmt_conv(item) if item else None


def delete_conversation(cid: str):
    _conv_table().delete_item(Key={"conversation_id": cid})
    # 해당 대화의 메시지 전체 삭제
    table = _msg_table()
    last_key = None
    while True:
        kwargs = {"KeyConditionExpression": Key("conversation_id").eq(cid)}
        if last_key:
            kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**kwargs)
        with table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={
                    "conversation_id": cid,
                    "message_id": item["message_id"],
                })
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break


def _fmt_conv(item: dict) -> dict:
    return {
        "id": item["conversation_id"],
        "title": item["title"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
    }


def _touch_conversation(cid: str):
    now = datetime.now(timezone.utc).isoformat()
    _conv_table().update_item(
        Key={"conversation_id": cid},
        UpdateExpression="SET updated_at = :t",
        ExpressionAttributeValues={":t": now},
    )


# ── Messages ───────────────────────────────────────────────────

def get_messages(cid: str) -> list[dict]:
    """UI 표시용 전체 메시지 (요약 제외)"""
    resp = _msg_table().query(KeyConditionExpression=Key("conversation_id").eq(cid))
    items = resp.get("Items", [])
    items.sort(key=lambda x: x["created_at"])
    return [_fmt_msg(i) for i in items if not i.get("is_summary")]


def get_context_messages(cid: str) -> list[dict]:
    """에이전트 컨텍스트용: 최신 요약본 + 최근 RECENT_TURNS 턴"""
    resp = _msg_table().query(KeyConditionExpression=Key("conversation_id").eq(cid))
    items = sorted(resp.get("Items", []), key=lambda x: x["created_at"])

    summary = next((i for i in reversed(items) if i.get("is_summary")), None)
    non_summary = [i for i in items if not i.get("is_summary")]
    recent = non_summary[-(RECENT_TURNS * 2):]

    result = []
    if summary:
        result.append(_fmt_msg(summary))
    result.extend([_fmt_msg(i) for i in recent])
    return result


def add_message(cid: str, role: str, content: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    mid = str(uuid.uuid4())
    _msg_table().put_item(Item={
        "conversation_id": cid,
        "message_id": mid,
        "role": role,
        "content": content,
        "created_at": now,
        "is_summary": False,
    })
    _touch_conversation(cid)
    return {"id": mid, "conversation_id": cid, "role": role, "content": content, "created_at": now}


def add_summary(cid: str, content: str):
    """기존 요약 삭제 후 새 요약 저장"""
    table = _msg_table()
    # 기존 요약 삭제
    resp = table.query(KeyConditionExpression=Key("conversation_id").eq(cid))
    with table.batch_writer() as batch:
        for item in resp.get("Items", []):
            if item.get("is_summary"):
                batch.delete_item(Key={
                    "conversation_id": cid,
                    "message_id": item["message_id"],
                })
    # 새 요약 저장 (created_at을 최초 메시지 시각으로 설정해 정렬 시 맨 앞에 오도록)
    now = datetime.now(timezone.utc).isoformat()
    table.put_item(Item={
        "conversation_id": cid,
        "message_id": str(uuid.uuid4()),
        "role": "user",
        "content": f"[이전 대화 요약]\n{content}",
        "created_at": "0000-" + now[5:],  # 항상 가장 앞에 정렬
        "is_summary": True,
    })


def count_non_summary_messages(cid: str) -> int:
    resp = _msg_table().query(KeyConditionExpression=Key("conversation_id").eq(cid))
    return sum(1 for i in resp.get("Items", []) if not i.get("is_summary"))


def _fmt_msg(item: dict) -> dict:
    return {
        "id": item["message_id"],
        "conversation_id": item["conversation_id"],
        "role": item["role"],
        "content": item["content"],
        "created_at": item["created_at"],
        "is_summary": item.get("is_summary", False),
    }
