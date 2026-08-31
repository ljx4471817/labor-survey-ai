from __future__ import annotations

from app.api import chat as chat_module
from app.models.schemas import ChatRequest


def _qa_source(qa_id: str = "1") -> dict:
    return {
        "id": qa_id.zfill(3),
        "document": "标准问题\n标准答案",
        "metadata": {"doc_type": "qa", "question": "标准问题"},
        "score": 0.9,
    }


def test_extract_top_qa_id_accepts_qa_metadata():
    assert chat_module._extract_top_qa_id([_qa_source("7")]) == "007"


def test_extract_top_qa_id_accepts_legacy_qa_metadata():
    source = _qa_source("8")
    del source["metadata"]["doc_type"]
    source["metadata"]["qa_id"] = "8"

    assert chat_module._extract_top_qa_id([source]) == "008"


def test_extract_top_qa_id_rejects_chunks_and_empty_sources():
    chunk = {
        "id": "chunk-001",
        "metadata": {"doc_type": "chunk"},
        "score": 0.8,
    }

    assert chat_module._extract_top_qa_id([]) is None
    assert chat_module._extract_top_qa_id([chunk]) is None


def test_chat_endpoint_logs_top_qa_id(monkeypatch):
    logged: list[dict] = []
    monkeypatch.setattr(
        chat_module,
        "get_current_user",
        lambda phone: {
            "name": "测试",
            "province": "贵州省",
            "city": "贵阳市",
            "county": "南明区",
            "community": "",
        },
    )
    monkeypatch.setattr(chat_module, "retrieve", lambda query, top_k: [_qa_source("9")])
    monkeypatch.setattr(chat_module, "llm_chat", lambda messages: "标准答案")
    monkeypatch.setattr(
        chat_module,
        "insert_query_log",
        lambda entry: logged.append(entry),
    )

    response = chat_module.chat_endpoint(
        ChatRequest(message="每周工作15小时算就业吗？"), phone="13900000001"
    )

    assert response.mode == "rag"
    assert len(logged) == 1
    assert logged[0]["top_qa_id"] == "009"
