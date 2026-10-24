# -*- coding: utf-8 -*-
"""Request-scoped knowledge-pack runtime orchestration."""
from __future__ import annotations

import os
from functools import lru_cache

from app.rag import combination_retriever
from app.rag.retriever import retrieve
from app.services.kb_pack_resolver import (
    PackCombination,
    load_province_catalog,
    resolve_pack_combination,
)
from app.core.constants import PACK_MODE_LEGACY
from app.persistence import knowledge_registry


def kb_pack_enabled() -> bool:
    """Return the explicit runtime feature flag; off keeps legacy behavior."""
    return os.environ.get("LSX_KB_PACK_ENABLED", "0") == "1"


def _legacy_combination() -> PackCombination:
    return PackCombination(
        mode=PACK_MODE_LEGACY,
        province_code=None,
        national_package_id=None,
        national_release_id=None,
        provincial_package_id=None,
        provincial_release_id=None,
        fallback_reason=None,
        warning=None,
    )


@lru_cache(maxsize=1)
def _province_catalog():
    return load_province_catalog()


def resolve_request_combination(
    province_name: str | None,
    *,
    force: bool = False,
) -> PackCombination:
    """Resolve one request's package combination; disabled path does no IO."""
    if not force and not kb_pack_enabled():
        return _legacy_combination()
    packages, releases, bindings = knowledge_registry.get_resolution_inputs()
    return resolve_pack_combination(
        province_name=province_name,
        province_catalog=_province_catalog(),
        packages=packages,
        releases=releases,
        bindings=bindings,
    )


def get_request_combination(phone: str) -> PackCombination:
    """Resolve the whitelist account's province before retrieval."""
    if not kb_pack_enabled():
        return _legacy_combination()
    from app.infra.auth import get_current_user

    user = get_current_user(phone)
    province_name = user.get("province") if isinstance(user, dict) else None
    return resolve_request_combination(province_name, force=True)


def retrieve_for_context(
    query: str,
    *,
    top_k: int | None,
    combination: PackCombination,
) -> list[dict]:
    """检索唯一入口：legacy / 单包 / 全国+省级组合的分派集中在此。

    组合合法性由 PackCombination 构造不变量保证，此处不再重复校验。
    """
    if combination.mode == PACK_MODE_LEGACY:
        return retrieve(query, top_k=top_k)
    if combination.provincial_release_id:
        return combination_retriever.retrieve_pack_combination(
            query,
            top_k=top_k,
            national_release_id=combination.national_release_id,
            provincial_release_id=combination.provincial_release_id,
            national_package_id=combination.national_package_id,
            provincial_package_id=combination.provincial_package_id,
        )
    return retrieve(
        query,
        top_k=top_k,
        release_id=combination.national_release_id,
        kb_id=combination.national_package_id,
    )


def pack_log_fields(sources: list[dict], combination: PackCombination) -> dict:
    """query_log 的知识包 provenance 字段；legacy 模式返回空。

    fallback 链与 combination_retriever 的 override 编排同源：
    top1 来自省包优先（rule_key / direct-hit override 后置顶）。
    """
    if combination.mode == PACK_MODE_LEGACY:
        return {}
    top_meta = (sources[0].get("metadata") or {}) if sources else {}
    return {
        "province_code": combination.province_code,
        "pack_mode": combination.mode,
        "kb_id": (
            top_meta.get("kb_id")
            or combination.provincial_package_id
            or combination.national_package_id
        ),
        "release_id": (
            top_meta.get("release_id")
            or combination.provincial_release_id
            or combination.national_release_id
        ),
        "global_entry_key": top_meta.get("global_entry_key"),
        "override_used": int(bool(top_meta.get("override_used", False))),
        "fallback_reason": combination.fallback_reason,
        "national_package_id": combination.national_package_id,
        "national_release_id": combination.national_release_id,
        "provincial_package_id": combination.provincial_package_id,
        "provincial_release_id": combination.provincial_release_id,
    }


def clear_catalog_cache() -> None:
    """Reset the cached province catalog after test/config changes."""
    _province_catalog.cache_clear()
