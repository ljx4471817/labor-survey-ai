import asyncio

import httpx

from app.infra.auth import require_user
from app.main import app


def test_chat_rag_answer_keeps_source_derived_anchors(monkeypatch):
    monkeypatch.setattr(
        "app.api.chat.retrieve",
        lambda *_, **__: [{
            "id": "qa-357",
            "score": 0.93,
            "document": "装修工月收入浮动较大，F27先问发放周期。",
            "metadata": {"doc_type": "qa", "question": "装修工月收入怎么填", "source": "test"},
        }],
    )
    monkeypatch.setattr("app.api.chat.llm_chat", lambda *_: "先问发放周期。")
    app.dependency_overrides[require_user] = lambda: "13985000001"

    try:
        async def send_request() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                return await client.post(
                    "/api/chat",
                    json={"message": "装修工作月收入怎么填", "history": []},
                    headers={"X-Phone": "13985000001"},
                )

        response = asyncio.run(send_request())
    finally:
        app.dependency_overrides.pop(require_user, None)

    assert response.status_code == 200
    assert "适用场景：装修工；适用指标：F27。" in response.json()["answer"]
