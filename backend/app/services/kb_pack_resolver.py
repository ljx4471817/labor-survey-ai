# -*- coding: utf-8 -*-
"""Resolve account province and compatible knowledge-pack releases."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.core.constants import PACK_MODE_LEGACY, PACK_MODE_PACKS


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROVINCE_CATALOG_PATH = (
    PROJECT_ROOT / "knowledge-base" / "shared" / "province_catalog.json"
)
_ADMIN_SUFFIXES = (
    "维吾尔自治区",
    "壮族自治区",
    "回族自治区",
    "特别行政区",
    "自治区",
    "省",
    "市",
)


@dataclass(frozen=True)
class ProvinceRecord:
    """One stable province identity entry."""

    code: str
    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class PackCombination:
    """Resolved national/provincial package combination for one request."""

    mode: str
    province_code: str | None
    national_package_id: str | None
    national_release_id: str | None
    provincial_package_id: str | None
    provincial_release_id: str | None
    fallback_reason: str | None
    warning: str | None

    def __post_init__(self) -> None:
        """组合不变量：非法组合在构造点即失败，消费方无需重复校验。"""
        if self.mode == PACK_MODE_LEGACY:
            carried = (
                self.province_code, self.national_package_id,
                self.national_release_id, self.provincial_package_id,
                self.provincial_release_id, self.fallback_reason, self.warning,
            )
            if any(value is not None for value in carried):
                raise ValueError("legacy 组合不能携带任何包字段")
        elif self.mode == PACK_MODE_PACKS:
            if not self.national_package_id or not self.national_release_id:
                raise ValueError("packs 组合必须有全国包与全国版本")
            if (self.provincial_release_id is None) != (self.provincial_package_id is None):
                raise ValueError("省级包与省级版本必须同置同缺")
        else:
            raise ValueError(f"未知组合模式：{self.mode}")

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe immutable snapshot for logging."""
        return asdict(self)


class KbResolutionError(RuntimeError):
    """Raised when a bound province has no usable knowledge-pack version."""


def _normalize_alias(value: str) -> str:
    return "".join(str(value).split()).casefold()


def _stripped_admin_name(name: str) -> str:
    normalized = name
    for suffix in _ADMIN_SUFFIXES:
        if normalized.endswith(suffix):
            return normalized[: -len(suffix)]
    return normalized


def build_alias_index(records: Iterable[ProvinceRecord]) -> dict[str, str]:
    """Build a deterministic alias-to-code index and reject ambiguity."""
    index: dict[str, str] = {}
    for record in records:
        values = {
            record.code,
            record.name,
            _stripped_admin_name(record.name),
            *record.aliases,
        }
        for value in values:
            key = _normalize_alias(value)
            if not key:
                continue
            existing = index.get(key)
            if existing is not None and existing != record.code:
                raise ValueError(f"省份别名冲突：{value}")
            index[key] = record.code
    return index


def load_province_catalog(
    path: Path = DEFAULT_PROVINCE_CATALOG_PATH,
) -> tuple[ProvinceRecord, ...]:
    """Load and minimally validate the national province catalog."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError("省级行政区划目录不存在") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("省级行政区划目录格式错误") from exc

    rows = payload.get("provinces") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise ValueError("省级行政区划目录为空")

    records: list[ProvinceRecord] = []
    seen_codes: set[str] = set()
    seen_names: set[str] = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"省级行政区划第 {index} 行格式错误")
        code = row.get("code")
        name = row.get("name")
        aliases = row.get("aliases")
        if not isinstance(code, str) or not code.isdigit() or len(code) != 6:
            raise ValueError(f"省级行政区划第 {index} 行代码错误")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"省级行政区划第 {index} 行缺少名称")
        if code in seen_codes or name in seen_names:
            raise ValueError(f"省级行政区划第 {index} 行重复")
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) and alias.strip() for alias in aliases
        ):
            raise ValueError(f"省级行政区划第 {index} 行别名错误")
        seen_codes.add(code)
        seen_names.add(name)
        records.append(
            ProvinceRecord(
                code=code,
                name=name.strip(),
                aliases=tuple(alias.strip() for alias in aliases),
            )
        )
    build_alias_index(records)
    return tuple(records)


def resolve_province_code(
    value: str | None,
    records: Sequence[ProvinceRecord],
) -> str | None:
    """Normalize a province name/alias to its stable administrative code."""
    if not isinstance(value, str) or not value.strip():
        return None
    return build_alias_index(records).get(_normalize_alias(value))


def _is_usable_release(release: Mapping[str, Any]) -> bool:
    return (
        release.get("build_status") == "success"
        and release.get("eval_status") == "passed"
        and bool(release.get("published_at"))
    )


def _select_release(
    releases: Sequence[Mapping[str, Any]],
    release_id: str | None,
) -> Mapping[str, Any] | None:
    if not release_id:
        return None
    for release in releases:
        if release.get("id") == release_id and _is_usable_release(release):
            return release
    return None


def _latest_compatible_release(
    releases: Sequence[Mapping[str, Any]],
    package_id: str,
    national_release_id: str,
) -> Mapping[str, Any] | None:
    candidates = [
        release
        for release in releases
        if release.get("package_id") == package_id
        and release.get("based_on_national_release_id") == national_release_id
        and _is_usable_release(release)
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda release: (
            str(release.get("published_at") or ""),
            str(release.get("version") or ""),
        ),
    )


def resolve_pack_combination(
    *,
    province_name: str | None,
    province_catalog: Sequence[ProvinceRecord],
    packages: Sequence[Mapping[str, Any]],
    releases: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, str],
) -> PackCombination:
    """Resolve the exact package/release combination for one request.

    开关语义归 kb_pack_runtime 独享：本函数只负责解析组合本身。
    """
    province_code = resolve_province_code(province_name, province_catalog)

    national_packages = [
        package for package in packages
        if package.get("kind") == "national" and package.get("status") == "active"
    ]
    if len(national_packages) != 1:
        raise KbResolutionError("no_usable_national_release")
    national_package = national_packages[0]
    national_release = _select_release(
        releases, national_package.get("current_release_id")
    )
    if national_release is None:
        raise KbResolutionError("no_usable_national_release")

    if province_code is None:
        return PackCombination(
            mode=PACK_MODE_PACKS,
            province_code=None,
            national_package_id=str(national_package["id"]),
            national_release_id=str(national_release["id"]),
            provincial_package_id=None,
            provincial_release_id=None,
            fallback_reason=None,
            warning="province_unresolved",
        )

    package_id = bindings.get(province_code)
    if not package_id:
        return PackCombination(
            mode=PACK_MODE_PACKS,
            province_code=province_code,
            national_package_id=str(national_package["id"]),
            national_release_id=str(national_release["id"]),
            provincial_package_id=None,
            provincial_release_id=None,
            fallback_reason=None,
            warning=None,
        )

    provincial_package = next(
        (
            package for package in packages
            if package.get("id") == package_id
            and package.get("kind") == "provincial"
            and package.get("status") == "active"
        ),
        None,
    )
    if provincial_package is None:
        raise KbResolutionError("no_usable_provincial_release")

    current_release = _select_release(
        releases, provincial_package.get("current_release_id")
    )
    if (
        current_release is not None
        and current_release.get("based_on_national_release_id")
        == national_release["id"]
    ):
        return PackCombination(
            mode=PACK_MODE_PACKS,
            province_code=province_code,
            national_package_id=str(national_package["id"]),
            national_release_id=str(national_release["id"]),
            provincial_package_id=str(provincial_package["id"]),
            provincial_release_id=str(current_release["id"]),
            fallback_reason=None,
            warning=None,
        )

    compatible_release = _latest_compatible_release(
        releases, str(provincial_package["id"]), str(national_release["id"])
    )
    if compatible_release is None:
        raise KbResolutionError("no_usable_provincial_release")

    fallback_reason = (
        "incompatible_current_release"
        if current_release is not None
        else "current_release_unavailable"
    )
    return PackCombination(
        mode=PACK_MODE_PACKS,
        province_code=province_code,
        national_package_id=str(national_package["id"]),
        national_release_id=str(national_release["id"]),
        provincial_package_id=str(provincial_package["id"]),
        provincial_release_id=str(compatible_release["id"]),
        fallback_reason=fallback_reason,
        warning=None,
    )
