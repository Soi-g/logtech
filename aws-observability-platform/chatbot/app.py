"""
Observability 챗봇 FastAPI 서버
"""

from dotenv import load_dotenv
load_dotenv(dotenv_path=__import__("pathlib").Path(__file__).parent / ".env")

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel

import database as db
import chat_agent

app = FastAPI(title="Observability Chatbot")

# DB 초기화
db.init_db()


# ── 요청/응답 모델 ─────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


class ConversationCreate(BaseModel):
    message: str = ""


# ── 라우트 ─────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index():
    html = (Path(__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


@app.get("/conversations")
def list_conversations():
    return db.list_conversations()


@app.post("/conversations")
def create_conversation(body: ConversationCreate):
    return db.create_conversation(body.message)


@app.get("/conversations/{cid}")
def get_conversation(cid: str):
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")
    messages = db.get_messages(cid)
    return {"conversation": conv, "messages": messages}


@app.delete("/conversations/{cid}")
def delete_conversation(cid: str):
    db.delete_conversation(cid)
    return {"ok": True}


@app.post("/conversations/{cid}/chat")
def chat(cid: str, body: ChatRequest):
    conv = db.get_conversation(cid)
    if not conv:
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다")

    # 이전 메시지 로드
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in db.get_messages(cid)
    ]

    # 사용자 메시지 저장
    db.add_message(cid, "user", body.message)

    # 에이전트 호출
    try:
        response = chat_agent.chat(history, body.message)
    except Exception as e:
        response = f"오류가 발생했습니다: {str(e)}"

    # 응답 저장
    msg = db.add_message(cid, "assistant", response)
    return {"message": msg}
