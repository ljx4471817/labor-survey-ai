# -*- coding: utf-8 -*-
"""知识包运行时开关与请求级检索（TDD）。"""
from __future__ import annotations

from app.services import kb_pack_runtime as runtime
from app.services.kb_pack_resolver import PackCombination


def test_disabled_flag_returns_legacy_without_registry_io(monkeypatch):
    monkeypatch.delenv("LSX_KB_PACK_ENABLED", raising=False)
    monkeypatch.setattr(
        runtime.knowledge_registry,
        "get_resolution_inputs",
        lambda: (_ for _ in ()).throw(AssertionError("disabled path must not read registry")),
    )
    assert runtime.resolve_request_combination("贵州省").mode == "legacy"


def test_enabled_flag_resolves_registry_and_catalog(monkeypatch):
    monkeypatch.setenv("LSX_KB_PACK_ENABLED", "1")
    monkeypatch.setattr(
        runtime.knowledge_registry,
        "get_resolution_inputs",
        lambda: (
            [
                {"id": "national-base", "kind": "national", "status": "active", "current_release_id": "nat-01"},
                {"id": "guizhou", "kind": "provincial", "status": "active", "current_release_id": "gz-01"},
            ],
            [
                {"id": "nat-01", "package_id": "national-base", "version": "1", "based_on_national_release_id": None, "build_status": "success", "eval_status": "passed", "published_at": "now"},
                {"id": "gz-01", "package_id": "guizhou", "version": "1", "based_on_national_release_id": "nat-01", "build_status": "success", "eval_status": "passed", "published_at": "now"},
            ],
            {"520000": "guizhou"},
        ),
    )
    result = runtime.resolve_request_combination("贵州省")
    assert result.mode == "packs"
    assert result.national_release_id == "nat-01"
    assert result.provincial_release_id == "gz-01"


def test_retrieve_for_context_dispatches_to_combination(monkeypatch):
    combination = PackCombination(
        mode="packs",
        province_code="520000",
        national_package_id="national-base",
        national_release_id="nat-01",
        provincial_package_id="guizhou",
        provincial_release_id="gz-01",
        fallback_reason=None,
        warning=None,
    )
    captured = {}

    def fake_combo(query, **kwargs):
        captured.update(kwargs)
        return [{"id": "hit"}]

    monkeypatch.setattr(runtime.combination_retriever, "retrieve_pack_combination", fake_combo)
    assert runtime.retrieve_for_context("包工头", top_k=5, combination=combination) == [{"id": "hit"}]
    assert captured["national_release_id"] == "nat-01"
    assert captured["provincial_release_id"] == "gz-01"
