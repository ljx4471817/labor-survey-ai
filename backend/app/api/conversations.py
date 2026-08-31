"""会话历史查询与删除端点。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import PROJECT_ROOT
from app.infra.auth import require_user
from app.models.schemas import (
    ConversationListResponse,
    ConversationMessagesResponse,
)
from app.persistence.conversations import (
    delete_conversation,
    get_conversation,
    list_conversations,
    list_messages,
)
from app.services.jsonl_utils import read_jsonl

router = APIRouter()

FEEDBACK_PATH = PROJECT_ROOT / "backend" / "data" / "feedback.jsonl"


@router.get("/chat/conversations", response_model=ConversationListResponse)
def list_conversations_endpoint(
    phone: str = Depends(require_user),
    limit: int = Query(default=10, ge=1, le=10),
    offset: int = Query(default=0, ge=0),
) -> ConversationListResponse:
    """分页返回当前手机号的历史会话标题。"""
    items = list_conversations(phone, limit=limit, offset=offset)
    return ConversationListResponse(items=items)


@router.get(
    "/chat/conversations/{conversation_id}/messages",
    response_model=ConversationMessagesResponse,
)
def list_conversation_messages_endpoint(
    conversation_id: str,
    phone: str = Depends(require_user),
) -> ConversationMessagesResponse:
    """返回一个会话最近 10 轮消息，并还原用户已提交的反馈状态。"""
    conversation = get_conversation(phone, conversation_id)
    if conversation is None:
        raise HTTPException(404, "会话不存在")

    messages = list_messages(phone, conversation_id, limit=20)
    feedback_states = {
        (str(item.get("phone") or ""), str(item.get("request_id") or "")):
        item.get("rating")
        for item in read_jsonl(FEEDBACK_PATH)
        if item.get("rating") in ("up", "down")
    }
    for message in messages:
        request_id = str(message.get("request_id") or "")
        message["feedback_state"] = feedback_states.get((phone, request_id))

    return ConversationMessagesResponse(
        conversation_id=conversation["id"],
        title=conversation["title"],
        messages=messages,
    )


@router.delete("/chat/conversations/{conversation_id}")
def delete_conversation_endpoint(
    conversation_id: str,
    phone: str = Depends(require_user),
) -> dict:
    """物理删除当前手机号的一个会话及消息。"""
    if not delete_conversation(phone, conversation_id):
        raise HTTPException(404, "会话不存在")
    return {"ok": True}
