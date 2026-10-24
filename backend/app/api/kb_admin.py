# -*- coding: utf-8 -*-
"""Knowledge-package admin APIs (write operations are system-admin only)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from app.core.constants import AdminLevel, SysRole
from app.infra.auth import require_whitelist_admin
from app.persistence import knowledge_registry as registry
from app.persistence.db import IntegrityError as RegistryIntegrityError
from app.rag import index_paths
from app.services import release_publish
from app.services.kb_pack_resolver import KbResolutionError, load_province_catalog, resolve_province_code
from app.services.kb_pack_runtime import resolve_request_combination, retrieve_for_context

router = APIRouter()


def _ensure_kb_read_admin(user: dict) -> dict:
    """Allow system admins and province business admins to view KB state."""
    role = user.get("sys_role") or ""
    if role == SysRole.SYSTEM_ADMIN.value or (
        role == SysRole.BUSINESS_ADMIN.value
        and user.get("admin_level") == AdminLevel.PROVINCE.value
    ):
        return user
    raise HTTPException(403, "无知识包管理权限")


def _validate_release_artifacts(release_id: str) -> None:
    """产物门禁委托给发布用例（唯一入口见 app/services/release_publish.py）。"""
    try:
        release_publish.validate_release_artifacts(release_id)
    except release_publish.ReleaseNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except release_publish.ReleaseArtifactError as exc:
        raise HTTPException(409, str(exc)) from exc


def _ensure_system_admin(user: dict) -> str:
    """Return actor phone for write endpoints; writes are system-admin only."""
    _ensure_kb_read_admin(user)
    if (user.get("sys_role") or "") != SysRole.SYSTEM_ADMIN.value:
        raise HTTPException(403, "仅系统管理员可操作")
    phone = str(user.get("phone") or "").strip()
    if not phone:
        raise HTTPException(401, "登录已失效，请重新登录")
    return phone


class KbPackageCreate(BaseModel):
    package_id: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    kind: Literal["national", "provincial"]
    status: Literal["draft"] = "draft"


class KbPackageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["draft", "active", "disabled"] | None = None


class KbReleaseCreate(BaseModel):
    release_id: str = Field(..., min_length=2, max_length=64)
    version: str = Field(..., min_length=1, max_length=40)
    artifact_path: str = Field(..., min_length=1, max_length=500)
    content_hash: str = Field(..., min_length=1, max_length=128)
    based_on_national_release_id: str | None = Field(default=None, max_length=64)
    build_status: Literal["pending", "success", "failed"] = "pending"
    eval_status: Literal["pending", "passed", "failed"] = "pending"


class KbReleaseStatusUpdate(BaseModel):
    build_status: Literal["pending", "success", "failed"] | None = None
    eval_status: Literal["pending", "passed", "failed"] | None = None


class KbBindingCreate(BaseModel):
    package_id: str = Field(..., min_length=2, max_length=64)


@router.get("/kb/packages")
def list_kb_packages(user: dict = Depends(require_whitelist_admin)) -> dict:
    """List packages/releases; province admins only see their own combination."""
    user = _ensure_kb_read_admin(user)
    packages, releases, bindings = registry.get_resolution_inputs()
    packages = sorted(packages, key=lambda item: (item["kind"], item["id"]))
    is_system = (user.get("sys_role") or "") == SysRole.SYSTEM_ADMIN.value
    if not is_system:
        province_code = resolve_province_code(
            user.get("province"), load_province_catalog()
        )
        bindings = {
            code: package_id
            for code, package_id in bindings.items()
            if code == province_code
        }
        visible_package_ids = {
            item["id"] for item in packages if item["kind"] == "national"
        } | set(bindings.values())
        packages = [
            item for item in packages if item["id"] in visible_package_ids
        ]
        releases = [
            item for item in releases if item["package_id"] in visible_package_ids
        ]
    return {
        "ok": True,
        "can_manage": is_system,
        "packages": packages,
        "releases": releases,
        "bindings": bindings,
    }


@router.post("/kb/packages")
def create_kb_package(req: KbPackageCreate, user: dict = Depends(require_whitelist_admin)) -> dict:
    """Create one knowledge package."""
    phone = _ensure_system_admin(user)
    try:
        package = registry.create_package(
            package_id=req.package_id,
            name=req.name,
            kind=req.kind,
            status=req.status,
            created_by=phone,
        )
    except RegistryIntegrityError as exc:
        raise HTTPException(409, "知识包 ID 已存在") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return package


@router.patch("/kb/packages/{package_id}")
def update_kb_package(
    package_id: str,
    req: KbPackageUpdate,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Update display name or status."""
    phone = _ensure_system_admin(user)
    try:
        return registry.update_package(
            package_id, actor=phone, name=req.name, status=req.status
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/kb/packages/{package_id}/releases")
def create_kb_release(
    package_id: str,
    req: KbReleaseCreate,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Register one immutable release candidate."""
    phone = _ensure_system_admin(user)
    try:
        return registry.create_release(
            release_id=req.release_id,
            package_id=package_id,
            version=req.version,
            artifact_path=req.artifact_path,
            content_hash=req.content_hash,
            based_on_national_release_id=req.based_on_national_release_id,
            build_status=req.build_status,
            eval_status=req.eval_status,
            created_by=phone,
        )
    except RegistryIntegrityError as exc:
        raise HTTPException(409, "版本 ID 或同包版本号已存在") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/kb/releases/{release_id}/status")
def update_kb_release_status(
    release_id: str,
    req: KbReleaseStatusUpdate,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Update build/Eval gate status after offline validation."""
    phone = _ensure_system_admin(user)
    try:
        return registry.update_release_status(
            release_id=release_id,
            actor=phone,
            build_status=req.build_status,
            eval_status=req.eval_status,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/kb/packages/{package_id}/releases/{release_id}/publish")
def publish_kb_release(
    package_id: str,
    release_id: str,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Publish a build/Eval passed release."""
    phone = _ensure_system_admin(user)
    try:
        return release_publish.publish_release(package_id, release_id, actor=phone)
    except release_publish.ReleaseNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except release_publish.ReleaseArtifactError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/kb/packages/{package_id}/releases/{release_id}/rollback")
def rollback_kb_release(
    package_id: str,
    release_id: str,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Roll back to another passed release."""
    phone = _ensure_system_admin(user)
    try:
        return release_publish.rollback_release(package_id, release_id, actor=phone)
    except release_publish.ReleaseNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except release_publish.ReleaseArtifactError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/kb/bindings/{province_code}")
def bind_kb_province(
    province_code: str,
    req: KbBindingCreate,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Bind one province to a published provincial package."""
    phone = _ensure_system_admin(user)
    if not resolve_province_code(province_code, load_province_catalog()):
        raise HTTPException(422, "未知省级行政区划代码")
    try:
        return registry.bind_province(
            province_code, req.package_id, actor=phone
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete("/kb/bindings/{province_code}")
def unbind_kb_province(
    province_code: str,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Remove one province binding."""
    return {"ok": registry.unbind_province(province_code, actor=_ensure_system_admin(user))}


@router.get("/kb/audit")
def list_kb_audit(
    limit: int = 100,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Return registry audit records."""
    _ensure_system_admin(user)
    return {"ok": True, "items": registry.list_audit(limit=limit)}


@router.get("/kb/preview")
def preview_kb(
    query: str = Query(..., min_length=1, max_length=500),
    province_name: str = "贵州省",
    top_k: int = 5,
    user: dict = Depends(require_whitelist_admin),
) -> dict:
    """Preview retrieval against registered releases without changing runtime flag."""
    _ensure_system_admin(user)
    try:
        combination = resolve_request_combination(province_name, force=True)
        sources = retrieve_for_context(
            query,
            top_k=max(1, min(top_k, 20)),
            combination=combination,
        )
    except KbResolutionError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("knowledge package preview failed")
        raise HTTPException(500, "知识包预览失败") from exc
    return {
        "ok": True,
        "combination": combination.as_dict(),
        "results": [
            {
                "qa_id": (item.get("metadata") or {}).get("qa_id") or item.get("id"),
                "score": item.get("score"),
                "document": item.get("document"),
                **(item.get("metadata") or {}),
            }
            for item in sources
        ],
    }
