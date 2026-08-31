from __future__ import annotations

from app.api import hot_questions as hot_questions_api


def test_hot_questions_endpoint_returns_service_items(monkeypatch):
    monkeypatch.setattr(
        hot_questions_api,
        "get_hot_questions",
        lambda: {"items": [{"question": "热点问法"}]},
    )

    assert hot_questions_api.hot_questions_endpoint(phone="13900000001") == {
        "items": [{"question": "热点问法"}]
    }


def test_hot_questions_endpoint_falls_back_on_service_error(monkeypatch):
    def broken():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(hot_questions_api, "get_hot_questions", broken)

    response = hot_questions_api.hot_questions_endpoint(phone="13900000001")

    assert response["items"]
    assert all(set(item) == {"question"} for item in response["items"])
