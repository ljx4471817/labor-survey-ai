# -*- coding: utf-8 -*-
"""Phase 1 省级目录与知识包版本兼容解析纯函数（TDD）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.kb_pack_resolver import (
    KbResolutionError,
    PackCombination,
    load_province_catalog,
    resolve_pack_combination,
    resolve_province_code,
)


CATALOG = load_province_catalog()


def test_province_catalog_is_national_and_alias_keys_are_unique():
    assert len(CATALOG) == 34
    assert len({item.code for item in CATALOG}) == 34
    assert len({item.name for item in CATALOG}) == 34


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("贵州省", "520000"),
        ("贵州", "520000"),
        ("黔", "520000"),
        ("新疆维吾尔自治区", "650000"),
        ("新疆", "650000"),
        ("新", "650000"),
    ],
)
def test_resolve_province_code_accepts_names_and_aliases(value, code):
    assert resolve_province_code(value, CATALOG) == code


def test_resolve_province_code_rejects_unknown_and_blank_values():
    assert resolve_province_code("不存在省", CATALOG) is None
    assert resolve_province_code("  ", CATALOG) is None


def _releases():
    return [
        {
            "id": "nat-2026-02",
            "package_id": "national-base",
            "version": "2026.02",
            "based_on_national_release_id": None,
            "build_status": "success",
            "eval_status": "passed",
            "published_at": "2026-02-01T00:00:00Z",
        },
        {
            "id": "nat-2026-03",
            "package_id": "national-base",
            "version": "2026.03",
            "based_on_national_release_id": None,
            "build_status": "success",
            "eval_status": "passed",
            "published_at": "2026-03-01T00:00:00Z",
        },
        {
            "id": "gz-2026-01",
            "package_id": "guizhou",
            "version": "2026.01",
            "based_on_national_release_id": "nat-2026-02",
            "build_status": "success",
            "eval_status": "passed",
            "published_at": "2026-02-02T00:00:00Z",
        },
        {
            "id": "gz-2026-02",
            "package_id": "guizhou",
            "version": "2026.02",
            "based_on_national_release_id": "nat-2026-03",
            "build_status": "success",
            "eval_status": "passed",
            "published_at": "2026-03-02T00:00:00Z",
        },
    ]


def _packages(guizhou_release_id="gz-2026-02"):
    return [
        {
            "id": "national-base",
            "kind": "national",
            "status": "active",
            "current_release_id": "nat-2026-03",
        },
        {
            "id": "guizhou",
            "kind": "provincial",
            "status": "active",
            "current_release_id": guizhou_release_id,
        },
    ]


def test_resolves_recognized_bound_province():
    result = resolve_pack_combination(
        province_name="黔",
        province_catalog=CATALOG,
        packages=_packages(),
        releases=_releases(),
        bindings={"520000": "guizhou"},
    )
    assert result == PackCombination(
        mode="packs",
        province_code="520000",
        national_package_id="national-base",
        national_release_id="nat-2026-03",
        provincial_package_id="guizhou",
        provincial_release_id="gz-2026-02",
        fallback_reason=None,
        warning=None,
    )


def test_unbound_province_uses_national_package_only():
    result = resolve_pack_combination(
        province_name="四川省",
        province_catalog=CATALOG,
        packages=_packages(),
        releases=_releases(),
        bindings={"520000": "guizhou"},
    )
    assert result.provincial_package_id is None
    assert result.provincial_release_id is None
    assert result.province_code == "510000"


def test_unresolved_province_uses_national_package_with_warning():
    result = resolve_pack_combination(
        province_name="不存在省",
        province_catalog=CATALOG,
        packages=_packages(),
        releases=_releases(),
        bindings={"520000": "guizhou"},
    )
    assert result.province_code is None
    assert result.national_release_id == "nat-2026-03"
    assert result.warning == "province_unresolved"


def test_pack_combination_invariants_enforced_at_construction():
    from app.core.constants import PACK_MODE_LEGACY, PACK_MODE_PACKS
    from app.services.kb_pack_resolver import PackCombination

    # packs：必须有全国包与全国版本
    with pytest.raises(ValueError, match="全国包与全国版本"):
        PackCombination(
            mode=PACK_MODE_PACKS, province_code=None,
            national_package_id=None, national_release_id=None,
            provincial_package_id=None, provincial_release_id=None,
            fallback_reason=None, warning=None,
        )
    # packs：省级包与省级版本同置同缺
    with pytest.raises(ValueError, match="同置同缺"):
        PackCombination(
            mode=PACK_MODE_PACKS, province_code="520000",
            national_package_id="national-base", national_release_id="nat-01",
            provincial_package_id="guizhou", provincial_release_id=None,
            fallback_reason=None, warning=None,
        )
    # legacy：不能携带任何包字段
    with pytest.raises(ValueError, match="legacy"):
        PackCombination(
            mode=PACK_MODE_LEGACY, province_code=None,
            national_package_id="national-base", national_release_id=None,
            provincial_package_id=None, provincial_release_id=None,
            fallback_reason=None, warning=None,
        )
    # 未知模式拒绝
    with pytest.raises(ValueError, match="未知组合模式"):
        PackCombination(
            mode="magic", province_code=None,
            national_package_id=None, national_release_id=None,
            provincial_package_id=None, provincial_release_id=None,
            fallback_reason=None, warning=None,
        )


def test_incompatible_provincial_release_uses_last_compatible_release():
    releases = _releases()
    releases.append(
        {
            "id": "gz-2026-03",
            "package_id": "guizhou",
            "version": "2026.03",
            "based_on_national_release_id": "nat-2026-04",
            "build_status": "success",
            "eval_status": "passed",
            "published_at": "2026-04-02T00:00:00Z",
        }
    )
    result = resolve_pack_combination(
        province_name="贵州省",
        province_catalog=CATALOG,
        packages=_packages(guizhou_release_id="gz-2026-03"),
        releases=releases,
        bindings={"520000": "guizhou"},
    )
    assert result.national_release_id == "nat-2026-03"
    assert result.provincial_release_id == "gz-2026-02"
    assert result.fallback_reason == "incompatible_current_release"


def test_bound_province_without_compatible_release_fails_loudly():
    releases = _releases()
    for release in releases:
        if release["package_id"] == "guizhou":
            release["eval_status"] = "pending"

    with pytest.raises(KbResolutionError, match="no_usable_provincial_release"):
        resolve_pack_combination(
            province_name="贵州省",
            province_catalog=CATALOG,
            packages=_packages(),
            releases=releases,
            bindings={"520000": "guizhou"},
        )


def test_unusable_national_release_fails_service():
    releases = _releases()
    for release in releases:
        if release["id"] == "nat-2026-03":
            release["build_status"] = "failed"

    with pytest.raises(KbResolutionError, match="no_usable_national_release"):
        resolve_pack_combination(
            province_name="贵州省",
            province_catalog=CATALOG,
            packages=_packages(guizhou_release_id="gz-2026-01"),
            releases=releases,
            bindings={"520000": "guizhou"},
        )


def test_catalog_file_lives_in_shared_knowledge_base():
    assert (Path(__file__).resolve().parents[2] / "knowledge-base" / "shared" / "province_catalog.json").exists()
