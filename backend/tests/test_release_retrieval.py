# -*- coding: utf-8 -*-
"""Phase 2 release 索引与组合检索（TDD）。"""
from __future__ import annotations

import json

import pytest

from app.rag import index_paths
from app.rag import combination_retriever as combo
from app.rag import retriever
from app.rag import bm25


def test_release_paths_are_isolated_and_validated(tmp_path, monkeypatch):
    monkeypatch.setattr(index_paths, "RELEASES_ROOT", tmp_path)
    assert index_paths.release_dir("nat-2026-01") == tmp_path / "nat-2026-01"
    assert index_paths.chroma_dir("nat-2026-01") == tmp_path / "nat-2026-01" / "chroma"
    assert index_paths.bm25_path("nat-2026-01") == tmp_path / "nat-2026-01" / "bm25_index.json"
    assert index_paths.chunks_path("nat-2026-01") == tmp_path / "nat-2026-01" / "chunks.jsonl"

    with pytest.raises(ValueError):
        index_paths.release_dir("../escape")
    with pytest.raises(ValueError):
        index_paths.release_dir("")


def test_release_root_resolves_relative_paths_against_project_root(tmp_path, monkeypatch):
    relative = "custom/releases"
    monkeypatch.setenv("KB_RELEASES_ROOT", relative)
    assert index_paths._resolve_releases_root(relative) == index_paths.PROJECT_ROOT / relative
    absolute = tmp_path / "releases"
    assert index_paths._resolve_releases_root(str(absolute)) == absolute
    assert index_paths._resolve_releases_root(None) == index_paths.DEFAULT_RELEASES_ROOT


def test_bm25_search_can_load_release_specific_index(tmp_path, monkeypatch):
    release = "gz-2026-01"
    release_dir = tmp_path / release
    release_dir.mkdir(parents=True)
    (release_dir / "bm25_index.json").write_text(
        json.dumps(
            {
                "ids": ["001", "002", "003"],
                "tokenized_corpus": [["贵州", "包工头"], ["全国", "制度"], ["其他", "内容"]],
                "meta_by_id": {
                    "001": {
                        "doc_type": "qa",
                        "question": "贵州包工头",
                        "answer": "按贵州口径",
                        "category": "就业",
                        "source": "贵州包",
                        "rule_key": "F10.guizhou",
                    },
                    "002": {
                        "doc_type": "qa",
                        "question": "全国制度",
                        "answer": "按全国制度",
                        "category": "就业",
                        "source": "全国包",
                    },
                    "003": {
                        "doc_type": "qa",
                        "question": "其他内容",
                        "answer": "其他答案",
                        "category": "其他",
                        "source": "全国包",
                    },
                },
                "built_at": "2026-10-19T00:00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(index_paths, "RELEASES_ROOT", tmp_path)
    bm25._load_index.cache_clear()
    try:
        result = bm25.search("贵州包工头", 5, release_id=release)
    finally:
        bm25._load_index.cache_clear()

    assert result[0]["id"] == "001"
    assert result[0]["metadata"]["rule_key"] == "F10.guizhou"


def test_retriever_caches_embeddings_per_release():
    class FakeCollection:
        def __init__(self, ids, embeddings):
            self._ids = ids
            self._embeddings = embeddings

        def count(self):
            return len(self._ids)

        def get(self, ids=None, include=None):
            if ids is None:
                return {"ids": list(self._ids)}
            selected = [(doc_id, emb) for doc_id, emb in zip(self._ids, self._embeddings) if doc_id in ids]
            return {
                "ids": [item[0] for item in selected],
                "metadatas": [{"doc_type": "qa"} for _ in selected],
                "embeddings": [item[1] for item in selected],
            }

    retriever._FULL_EMBED_CACHE.clear()
    first = FakeCollection(["a"], [[1.0, 0.0]])
    second = FakeCollection(["b"], [[0.0, 1.0]])
    ids1, emb1 = retriever._get_all_embeddings(first, "release-a")
    ids2, emb2 = retriever._get_all_embeddings(second, "release-b")
    assert ids1 == ["a"]
    assert ids2 == ["b"]
    assert emb1 == [[1.0, 0.0]]
    assert emb2 == [[0.0, 1.0]]


def test_combination_retriever_prefers_provincial_rule_override(monkeypatch):
    national = [
        {
            "id": "001",
            "document": "全国规则",
            "metadata": {"doc_type": "qa", "qa_id": "001", "rule_key": "F10.common"},
            "score": 0.9,
        },
        {
            "id": "002",
            "document": "全国其他",
            "metadata": {"doc_type": "qa", "qa_id": "002"},
            "score": 0.8,
        },
    ]
    provincial = [
        {
            "id": "001",
            "document": "贵州覆盖规则",
            "metadata": {"doc_type": "qa", "qa_id": "001", "rule_key": "F10.common"},
            "score": 0.7,
        },
        {
            "id": "003",
            "document": "贵州独有",
            "metadata": {"doc_type": "qa", "qa_id": "003", "rule_key": "F27.guizhou"},
            "score": 0.6,
        },
    ]

    def fake_retrieve(query, top_k, release_id, kb_id):
        return provincial if release_id == "gz-01" else national

    result = combo.retrieve_pack_combination(
        "贵州包工头",
        top_k=5,
        retrieve_fn=fake_retrieve,
        national_release_id="nat-01",
        provincial_release_id="gz-01",
        national_package_id="national-base",
        provincial_package_id="guizhou",
    )

    assert result[0]["metadata"]["override_used"] is True
    assert result[0]["metadata"]["kb_id"] == "guizhou"
    assert result[0]["metadata"]["global_entry_key"] == "guizhou:gz-01:001"
    assert result[0]["document"] == "贵州覆盖规则"
    assert not any(
        item["metadata"].get("rule_key") == "F10.common"
        and item["metadata"].get("kb_id") == "national-base"
        for item in result
    )
    assert any(item["metadata"].get("global_entry_key") == "national-base:nat-01:002" for item in result)
    assert all("qa_id" not in item["metadata"] or item["metadata"]["qa_id"] for item in result)


def test_annotate_release_metadata_generates_global_entry_key():
    item = {
        "id": "002",
        "document": "全国制度",
        "metadata": {"doc_type": "qa"},
        "score": 0.9,
    }
    annotated = retriever._annotate_release_metadata(item, "nat-01", "national-base")
    assert annotated["metadata"]["release_id"] == "nat-01"
    assert annotated["metadata"]["kb_id"] == "national-base"
    assert annotated["metadata"]["global_entry_key"] == "national-base:nat-01:002"
    # 原 item 不被修改（immutability 约定）
    assert "global_entry_key" not in item["metadata"]

    legacy = retriever._annotate_release_metadata(item, None, None)
    assert "global_entry_key" not in legacy["metadata"]


def test_direct_hit_override_qualifies_vector_hits_only():
    from app.rag.pure import qualifies_for_direct_hit_override as q

    # 哨兵值：BM25-only 条目（score=0.0）永远不够格，无论其他参数
    assert q(0.0, 0.0, 0.75) is False
    assert q(None, 0.0, 0.75) is False
    # 向量命中：正常资格判定
    assert q(0.80, 0.70, 0.75) is True
    # cosine 不足
    assert q(0.70, 0.60, 0.75) is False
    # 不敌全国最高分
    assert q(0.80, 0.90, 0.75) is False
    # 边界：>= 语义
    assert q(0.75, 0.75, 0.75) is True


def test_combination_bm25_only_provincial_item_gets_no_override(monkeypatch):
    national = [
        {"id": "n1", "document": "全国", "metadata": {"doc_type": "qa"}, "score": 0.55},
    ]
    provincial = [
        # BM25-only：score=0.0 哨兵（simulate rrf_fuse 输出），vector 弱
        {"id": "g1", "document": "省级BM25强命中", "metadata": {"doc_type": "qa", "rule_key": "F27.gx"}, "score": 0.0},
    ]

    def fake_retrieve(query, top_k, release_id, kb_id):
        return provincial if release_id == "gz-01" else national

    result = combo.retrieve_pack_combination(
        "装修工资浮动",
        top_k=5,
        retrieve_fn=fake_retrieve,
        national_release_id="nat-01",
        provincial_release_id="gz-01",
        national_package_id="national-base",
        provincial_package_id="guizhou",
    )
    g1 = next(item for item in result if item["metadata"].get("global_entry_key") == "guizhou:gz-01:g1")
    assert g1["metadata"].get("override_used") is None  # 哨兵值不触发 direct-hit override
