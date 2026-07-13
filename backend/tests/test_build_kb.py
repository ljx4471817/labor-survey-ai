"""QA 向量全量重建的共享 collection 回归测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import build_kb


class FakeCollection:
    def __init__(self) -> None:
        self.ids = ["007", "regulations#001"]
        self.metadatas = [
            {"qa_id": "007", "embed_hash": "legacy-qa"},
            {"doc_type": "chunk", "embed_hash": "chunk-hash"},
        ]
        self.deleted_ids: list[str] = []
        self.upserted_ids: list[str] = []

    def get(self, include: list[str]) -> dict:
        return {"ids": self.ids, "metadatas": self.metadatas}

    def delete(self, ids: list[str]) -> None:
        self.deleted_ids.extend(ids)

    def upsert(self, *, ids: list[str], **_: object) -> None:
        self.upserted_ids.extend(ids)


class FakeClient:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection

    def get_or_create_collection(self, **_: object) -> FakeCollection:
        return self.collection


class FakeEmbeddingClient:
    def __init__(self, _provider: str) -> None:
        pass

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2] for _ in texts]


def test_full_build_replaces_qas_but_preserves_chunks(
    monkeypatch, tmp_path: Path
) -> None:
    faq_path = tmp_path / "faq.json"
    faq_path.write_text(
        json.dumps(
            [
                {
                    "id": 1,
                    "question": "测试问题",
                    "answer": "测试答案",
                    "category": "测试",
                    "source": "测试来源",
                    "keywords": ["测试"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    collection = FakeCollection()
    client = FakeClient(collection)
    fake_chromadb = type(
        "FakeChromaDb",
        (),
        {"PersistentClient": staticmethod(lambda **_: client)},
    )
    monkeypatch.setattr(build_kb, "chromadb", fake_chromadb)
    monkeypatch.setattr(build_kb, "EmbeddingClient", FakeEmbeddingClient)

    summary = build_kb.build(
        faq_path=faq_path,
        chroma_dir=tmp_path / "chroma",
        collection_name="test",
        provider="dashscope",
        full=True,
    )

    assert collection.deleted_ids == ["007"]
    assert collection.upserted_ids == ["001"]
    assert summary == {"total": 1, "added": 1, "skipped": 0, "failed": 0}


def test_qa_records_are_explicitly_typed() -> None:
    _, _, metadata = build_kb.qa_to_chroma_record(
        {"id": 1, "question": "问题", "answer": "答案"}
    )

    assert metadata["doc_type"] == "qa"
