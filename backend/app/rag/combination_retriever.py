# -*- coding: utf-8 -*-
"""Combine national and provincial release retrieval results."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from app.core.config import settings
from app.rag.pure import qualifies_for_direct_hit_override, rrf_fuse
from app.rag.retriever import retrieve as default_retrieve


def _globalize(
    item: dict,
    *,
    kb_id: str,
    release_id: str,
) -> dict:
    """Create a globally unique entry key without changing source QA ids."""
    original_id = str(item.get("id") or "")
    global_key = f"{kb_id}:{release_id}:{original_id}"
    metadata = dict(item.get("metadata") or {})
    if metadata.get("doc_type") == "qa" or metadata.get("qa_id"):
        metadata["qa_id"] = str(metadata.get("qa_id") or original_id)
    metadata["kb_id"] = kb_id
    metadata["release_id"] = release_id
    metadata["global_entry_key"] = global_key
    return {
        **item,
        "id": global_key,
        "metadata": metadata,
    }


def retrieve_pack_combination(
    query: str,
    *,
    top_k: int | None,
    national_release_id: str,
    provincial_release_id: str,
    national_package_id: str,
    provincial_package_id: str,
    retrieve_fn: Callable[..., list[dict]] | None = None,
) -> list[dict]:
    """Retrieve two isolated releases and apply deterministic rule-key overrides."""
    k = top_k or 5
    retrieve_fn = retrieve_fn or default_retrieve
    with ThreadPoolExecutor(max_workers=2) as executor:
        national_future = executor.submit(
            retrieve_fn,
            query,
            k,
            national_release_id,
            national_package_id,
        )
        provincial_future = executor.submit(
            retrieve_fn,
            query,
            k,
            provincial_release_id,
            provincial_package_id,
        )
        national = national_future.result()
        provincial = provincial_future.result()

    national_global = [
        _globalize(item, kb_id=national_package_id, release_id=national_release_id)
        for item in national
    ]
    provincial_global = [
        _globalize(item, kb_id=provincial_package_id, release_id=provincial_release_id)
        for item in provincial
    ]

    national_rule_keys = {
        item["metadata"].get("rule_key")
        for item in national_global
        if item["metadata"].get("rule_key")
    }
    provincial_rule_keys = {
        item["metadata"].get("rule_key")
        for item in provincial_global
        if item["metadata"].get("rule_key")
    }
    overridden_national_keys = national_rule_keys & provincial_rule_keys
    threshold = float(
        getattr(settings, "rag_direct_hit_threshold", 0.75)
    )
    national_top_score = max(
        (float(item.get("score") or 0.0) for item in national_global),
        default=0.0,
    )
    # A provincial direct hit is a local口径 even when no matching national
    # rule_key was returned by the national pack; it must stay ahead of
    # generic national guidance. cosine 资格仅对向量命中生效（pure 侧显式排除
    # rrf_fuse 的 score=0.0 哨兵）。
    overrides = [
        {
            **item,
            "metadata": {**item["metadata"], "override_used": True},
        }
        for item in provincial_global
        if item["metadata"].get("rule_key")
        and (
            item["metadata"].get("rule_key") in overridden_national_keys
            or qualifies_for_direct_hit_override(
                item.get("score"), national_top_score, threshold
            )
        )
    ]
    remaining_national = [
        item for item in national_global
        if item["metadata"].get("rule_key") not in overridden_national_keys
    ]
    remaining_provincial = [
        item for item in provincial_global
        if item["metadata"].get("rule_key") not in overridden_national_keys
    ]
    fused = rrf_fuse(
        remaining_national,
        remaining_provincial,
        k=max(k * 2, 10),
    )
    result: list[dict] = []
    seen: set[str] = set()
    for item in overrides + fused:
        key = item["id"]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result[:k]
