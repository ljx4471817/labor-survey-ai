from __future__ import annotations

import asyncio

import httpx
import pytest

from app.infra.auth import require_user
from app.main import app
from app.persistence import conversations as conversations_persistence


@pytest.fixture
def conversation_db(tmp_path, monkeypatch):
    """每个测试使用独立会话库，避免污染运行时数据。"""
    monkeypatch.setattr(
        conversations_persistence, "DB_PATH", tmp_path / "conversations.db"
    )
    conversations_persistence.reset_conn()
    yield conversations_persistence
    conversations_persistence.reset_conn()


def _source(qa_id: str = "001") -> dict:
    return {
        "qa_id": qa_id,
        "question": "标准问法",
        "source": "制度依据",
        "category": "就业",
        "score": 0.9,
        "image": None,
    }


def test_save_exchange_creates_and_extends_conversation(conversation_db):
    conversation = conversation_db.save_exchange(
        phone="13900000001",
        conversation_id=None,
        user_message="退休人员务工怎么填",
        assistant_message="先问是否从事有酬劳动。",
        mode="rag",
        sources=[_source("001")],
        retrieval_score=0.9,
        request_id="request-001",
    )

    assert conversation["title"] == "退休人员务工怎么填"
    messages = conversation_db.list_messages(
        phone="13900000001", conversation_id=conversation["id"]
    )
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[1]["sources"] == [_source("001")]

    conversation_db.save_exchange(
        phone="13900000001",
        conversation_id=conversation["id"],
        user_message="临时回老家算外出吗",
        assistant_message="按外出时间规则判断。",
        mode="rag",
        sources=[],
        retrieval_score=0.8,
        request_id="request-002",
    )

    conversations = conversation_db.list_conversations(
        phone="13900000001", limit=10
    )
    assert len(conversations) == 1
    assert conversations[0]["title"] == "退休人员务工怎么填"
    assert len(conversation_db.list_messages(
        phone="13900000001", conversation_id=conversation["id"]
    )) == 4


def test_conversations_are_isolated_by_phone(conversation_db):
    first = conversation_db.save_exchange(
        phone="13900000001",
        conversation_id=None,
        user_message="甲的问题",
        assistant_message="甲的答案",
        mode="rag",
        sources=[],
        retrieval_score=None,
        request_id="request-a",
    )
    second = conversation_db.save_exchange(
        phone="13900000002",
        conversation_id=None,
        user_message="乙的问题",
        assistant_message="乙的答案",
        mode="rag",
        sources=[],
        retrieval_score=None,
        request_id="request-b",
    )

    assert conversation_db.list_conversations(
        phone="13900000001", limit=10
    ) == [first]
    assert conversation_db.list_conversations(
        phone="13900000002", limit=10
    ) == [second]
    assert conversation_db.get_conversation(
        phone="13900000001", conversation_id=second["id"]
    ) is None
    with pytest.raises(ValueError, match="会话不存在"):
        conversation_db.save_exchange(
            phone="13900000001",
            conversation_id=second["id"],
            user_message="越权",
            assistant_message="拒绝",
            mode="rag",
            sources=[],
            retrieval_score=None,
            request_id="request-c",
        )


def test_delete_conversation_removes_messages_only_for_owner(conversation_db):
    conversation = conversation_db.save_exchange(
        phone="13900000001",
        conversation_id=None,
        user_message="待删除问题",
        assistant_message="待删除答案",
        mode="rag",
        sources=[],
        retrieval_score=None,
        request_id="request-delete",
    )

    assert conversation_db.delete_conversation(
        phone="13900000002", conversation_id=conversation["id"]
    ) is False
    assert conversation_db.delete_conversation(
        phone="13900000001", conversation_id=conversation["id"]
    ) is True
    assert conversation_db.get_conversation(
        phone="13900000001", conversation_id=conversation["id"]
    ) is None
    assert conversation_db.list_messages(
        phone="13900000001", conversation_id=conversation["id"]
    ) == []


def test_chat_creates_conversation_and_persists_full_turn(
    conversation_db, monkeypatch
):
    monkeypatch.setattr(
        "app.api.chat.get_current_user",
        lambda _: {
            "name": "测试",
            "province": "贵州省",
            "city": "贵阳市",
            "county": "南明区",
            "community": "",
        },
    )
    monkeypatch.setattr("app.api.chat.retrieve", lambda *_, **__: [])
    monkeypatch.setattr(
        "app.api.chat.llm_chat",
        lambda *_: "知识库中未找到相关内容。",
    )
    app.dependency_overrides[require_user] = lambda: "13900000001"

    try:
        async def send_request() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.post(
                    "/api/chat",
                    json={"message": "装修工月收入怎么填", "history": []},
                    headers={"X-Phone": "13900000001"},
                )

        response = asyncio.run(send_request())
    finally:
        app.dependency_overrides.pop(require_user, None)

    assert response.status_code == 200
    body = response.json()
    conversation_id = body["conversation_id"]
    assert conversation_id
    messages = conversation_db.list_messages(
        phone="13900000001", conversation_id=conversation_id
    )
    assert [(item["role"], item["mode"]) for item in messages] == [
        ("user", None),
        ("assistant", "out_of_kb"),
    ]


def test_chat_extends_conversation_after_out_of_scope_turn(
    conversation_db, monkeypatch
):
    monkeypatch.setattr(
        "app.api.chat.get_current_user",
        lambda _: {
            "name": "测试",
            "province": "贵州省",
            "city": "贵阳市",
            "county": "南明区",
            "community": "",
        },
    )
    monkeypatch.setattr("app.api.chat.retrieve", lambda *_, **__: [])
    monkeypatch.setattr(
        "app.api.chat.llm_chat",
        lambda *_: "知识库中未找到相关内容。",
    )
    app.dependency_overrides[require_user] = lambda: "13900000001"

    try:
        async def send_requests() -> tuple[httpx.Response, httpx.Response]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                headers = {"X-Phone": "13900000001"}
                first = await client.post(
                    "/api/chat",
                    json={"message": "今天天气如何", "history": []},
                    headers=headers,
                )
                conversation_id = first.json()["conversation_id"]
                second = await client.post(
                    "/api/chat",
                    json={
                        "message": "你是谁",
                        "history": [],
                        "conversation_id": conversation_id,
                    },
                    headers=headers,
                )
                return first, second

        first, second = asyncio.run(send_requests())
    finally:
        app.dependency_overrides.pop(require_user, None)

    assert first.status_code == 200
    assert first.json()["mode"] == "out_of_scope"
    assert second.status_code == 200
    assert second.json()["mode"] == "out_of_kb"
    messages = conversation_db.list_messages(
        phone="13900000001",
        conversation_id=first.json()["conversation_id"],
    )
    assert [(item["role"], item["mode"]) for item in messages] == [
        ("user", None),
        ("assistant", "out_of_scope"),
        ("user", None),
        ("assistant", "out_of_kb"),
    ]


def test_conversation_messages_endpoint_restores_feedback_state(
    conversation_db, monkeypatch, tmp_path
):
    from app.api import conversations as conversations_api

    conversation = conversation_db.save_exchange(
        phone="13900000001",
        conversation_id=None,
        user_message="装修工月收入怎么填",
        assistant_message="先问发放周期。",
        mode="rag",
        sources=[_source()],
        retrieval_score=0.9,
        request_id="request-feedback",
    )
    feedback_path = tmp_path / "feedback.jsonl"
    feedback_path.write_text(
        '{"phone":"13900000001","request_id":"request-feedback","rating":"down"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(conversations_api, "FEEDBACK_PATH", feedback_path)
    app.dependency_overrides[require_user] = lambda: "13900000001"

    try:
        async def send_request() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.get(
                    f"/api/chat/conversations/{conversation['id']}/messages",
                    headers={"X-Phone": "13900000001"},
                )

        response = asyncio.run(send_request())
    finally:
        app.dependency_overrides.pop(require_user, None)

    assert response.status_code == 200
    messages = response.json()["messages"]
    assert messages[1]["feedback_state"] == "down"
