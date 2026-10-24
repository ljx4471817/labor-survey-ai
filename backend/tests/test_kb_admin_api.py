# -*- coding: utf-8 -*-
"""知识包后台 API（TDD）。"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api import kb_admin
from app.persistence import db
from app.persistence import knowledge_registry as registry
from app.rag import index_paths
from app.services import release_artifacts
from app.services.kb_pack_resolver import PackCombination


@pytest.fixture
def temp_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("LSX_DB_KB_ADMIN", raising=False)
    monkeypatch.setattr(registry, "DB_PATH", tmp_path / "knowledge_admin.db")
    monkeypatch.setattr(registry, "_schema_ready_for", None)
    monkeypatch.setattr(index_paths, "RELEASES_ROOT", tmp_path / "kb_releases")
    registry.reset_conn()
    yield registry
    registry.reset_conn()


def _write_release_artifact(release_id: str, root: Path):
    root = root / release_id
    for relpath in ("bm25_index.json", "chunks.jsonl", "chroma/chroma.sqlite3"):
        target = root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relpath.encode("utf-8"))
    release_artifacts.write_artifact_manifest(root, release_id)


def _system_admin() -> dict:
    return {
        "phone": "13985000001",
        "name": "系统管理员",
        "province": "贵州省",
        "admin_level": "省级",
        "sys_role": "系统管理员",
    }


def _province_admin() -> dict:
    return {
        "phone": "13800000001",
        "name": "省级业务管理员",
        "province": "贵州省",
        "admin_level": "省级",
        "sys_role": "业务管理员",
    }


def _published_registry(temp_registry):
    temp_registry.create_package(
        package_id="national-base", name="全国基础知识包", kind="national",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="nat-2026-01", package_id="national-base", version="2026.01",
        artifact_path="data/kb_releases/nat-2026-01", content_hash="nat",
        build_status="success", eval_status="passed", created_by="13985000001",
    )
    _write_release_artifact("nat-2026-01", index_paths.RELEASES_ROOT)
    temp_registry.publish_release("national-base", "nat-2026-01", actor="13985000001")
    temp_registry.create_package(
        package_id="guizhou", name="贵州知识包", kind="provincial", created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="gz-2026-01", package_id="guizhou", version="2026.01",
        artifact_path="data/kb_releases/gz-2026-01", content_hash="gz",
        based_on_national_release_id="nat-2026-01",
        build_status="success", eval_status="passed", created_by="13985000001",
    )
    _write_release_artifact("gz-2026-01", index_paths.RELEASES_ROOT)
    temp_registry.publish_release("guizhou", "gz-2026-01", actor="13985000001")
    temp_registry.bind_province("520000", "guizhou", actor="13985000001")


def test_update_package_changes_name_and_status_with_audit(temp_registry):
    temp_registry.create_package(
        package_id="guizhou", name="贵州知识包", kind="provincial", created_by="13985000001"
    )
    updated = temp_registry.update_package(
        "guizhou", actor="13985000001", name="贵州口径包", status="disabled"
    )
    assert updated["name"] == "贵州口径包"
    assert updated["status"] == "disabled"
    assert temp_registry.list_audit()[-1]["action"] == "update_package"


def test_list_packages_scopes_bindings_by_province_admin(temp_registry):
    _published_registry(temp_registry)
    payload = kb_admin.list_kb_packages(user=_province_admin())
    assert payload["can_manage"] is False
    assert {item["id"] for item in payload["packages"]} == {"national-base", "guizhou"}
    assert payload["bindings"] == {"520000": "guizhou"}

    system = kb_admin.list_kb_packages(user=_system_admin())
    assert system["can_manage"] is True
    assert system["bindings"] == {"520000": "guizhou"}


def _city_admin() -> dict:
    return {
        "phone": "13800000002",
        "name": "市级业务管理员",
        "province": "贵州省",
        "city": "贵阳市",
        "admin_level": "市级",
        "sys_role": "业务管理员",
    }


def test_list_packages_hides_other_provincial_packages(temp_registry):
    _published_registry(temp_registry)
    temp_registry.create_package(
        package_id="sichuan", name="四川知识包", kind="provincial",
        created_by="13985000001",
    )

    payload = kb_admin.list_kb_packages(user=_province_admin())
    assert {item["id"] for item in payload["packages"]} == {"national-base", "guizhou"}
    assert all(
        item["package_id"] in {"national-base", "guizhou"}
        for item in payload["releases"]
    )


def test_city_business_admin_cannot_list_kb_packages(temp_registry):
    with pytest.raises(HTTPException) as exc:
        kb_admin.list_kb_packages(user=_city_admin())
    assert exc.value.status_code == 403



def test_package_write_endpoints_require_system_admin(temp_registry):
    with pytest.raises(HTTPException) as exc:
        kb_admin.create_kb_package(
            kb_admin.KbPackageCreate(package_id="xx", name="X", kind="national"),
            user=_province_admin(),
        )
    assert exc.value.status_code == 403


def test_create_package_and_release_endpoints(temp_registry):
    created = kb_admin.create_kb_package(
        kb_admin.KbPackageCreate(package_id="national-base", name="全国基础知识包", kind="national"),
        user=_system_admin(),
    )
    assert created["status"] == "draft"

    release = kb_admin.create_kb_release(
        "national-base",
        kb_admin.KbReleaseCreate(
            release_id="nat-2026-01", version="2026.01",
            artifact_path="data/kb_releases/nat-2026-01", content_hash="abc",
        ),
        user=_system_admin(),
    )
    assert release["build_status"] == "pending"

    with pytest.raises(HTTPException) as exc:
        kb_admin.create_kb_package(
            kb_admin.KbPackageCreate(package_id="national-base", name="重复", kind="national"),
            user=_system_admin(),
        )
    assert exc.value.status_code == 409


def test_publish_rollback_bind_and_audit_endpoints(temp_registry):
    _published_registry(temp_registry)
    published = kb_admin.publish_kb_release(
        "national-base", "nat-2026-01", user=_system_admin(),
    )
    assert published["current_release_id"] == "nat-2026-01"

    binding = kb_admin.bind_kb_province(
        "520000", kb_admin.KbBindingCreate(package_id="guizhou"), user=_system_admin(),
    )
    assert binding["package_id"] == "guizhou"
    audit = kb_admin.list_kb_audit(user=_system_admin(), limit=20)
    assert audit["ok"] is True
    assert any(row["action"] == "bind_province" for row in audit["items"])


def test_bind_rejects_unknown_province_code(temp_registry):
    _published_registry(temp_registry)
    with pytest.raises(HTTPException) as exc:
        kb_admin.bind_kb_province(
            "999999", kb_admin.KbBindingCreate(package_id="guizhou"),
            user=_system_admin(),
        )
    assert exc.value.status_code == 422


def test_preview_does_not_leak_internal_error_details(monkeypatch):
    def fail_preview(query, top_k, combination):
        raise RuntimeError("secret-internal-path")

    monkeypatch.setattr(kb_admin, "resolve_request_combination", lambda province_name, *, force=False: PackCombination(
        mode="packs", province_code="520000",
        national_package_id="national-base", national_release_id="nat-01",
        provincial_package_id="guizhou", provincial_release_id="gz-01",
        fallback_reason=None, warning=None,
    ))
    monkeypatch.setattr(kb_admin, "retrieve_for_context", fail_preview)

    with pytest.raises(HTTPException) as exc:
        kb_admin.preview_kb(
            query="测试", province_name="贵州省", top_k=5, user=_system_admin()
        )
    assert exc.value.status_code == 500
    assert "secret-internal-path" not in str(exc.value.detail)


def test_update_release_status_api_returns_validation_error(temp_registry):
    _published_registry(temp_registry)
    with pytest.raises(HTTPException) as exc:
        kb_admin.update_kb_release_status(
            "nat-2026-01",
            kb_admin.KbReleaseStatusUpdate(eval_status="pending"),
            user=_system_admin(),
        )
    assert exc.value.status_code == 422
    assert "已发布版本状态不可修改" in exc.value.detail


def test_preview_uses_release_retrieval(monkeypatch):
    combination = PackCombination(
        mode="packs", province_code="520000",
        national_package_id="national-base", national_release_id="nat-01",
        provincial_package_id="guizhou", provincial_release_id="gz-01",
        fallback_reason=None, warning=None,
    )
    source = {
        "id": "guizhou:gz-01:429", "document": "贵州规则",
        "metadata": {
            "doc_type": "qa", "qa_id": "429", "kb_id": "guizhou",
            "release_id": "gz-01", "global_entry_key": "guizhou:gz-01:429",
            "override_used": True,
        },
        "score": 0.9,
    }
    monkeypatch.setattr(kb_admin, "resolve_request_combination", lambda province_name, *, force=False: combination)
    monkeypatch.setattr(kb_admin, "retrieve_for_context", lambda query, top_k, combination: [source])

    result = kb_admin.preview_kb(
        query="包工头没有项目", province_name="贵州省", top_k=5, user=_system_admin()
    )
    assert result["ok"] is True
    assert result["combination"]["mode"] == "packs"
    assert result["results"][0]["qa_id"] == "429"
    assert result["results"][0]["global_entry_key"] == "guizhou:gz-01:429"


def test_publish_unknown_release_returns_404(temp_registry):
    with pytest.raises(HTTPException) as exc:
        kb_admin._validate_release_artifacts("no-such-release")
    assert exc.value.status_code == 404


def test_publish_rejects_hex_content_hash_mismatch(temp_registry):
    _published_registry(temp_registry)
    real = release_artifacts.validate_release_dir(
        index_paths.release_dir("gz-2026-01"), release_id="gz-2026-01"
    )
    real_hash = release_artifacts.manifest_content_hash(real)
    fake_hash = "f" * 64
    assert fake_hash != real_hash

    temp_registry.create_release(
        release_id="gz-2026-02", package_id="guizhou", version="2026.02",
        artifact_path="data/kb_releases/gz-2026-02", content_hash=fake_hash,
        based_on_national_release_id="nat-2026-01",
        build_status="success", eval_status="passed", created_by="13985000001",
    )
    _write_release_artifact("gz-2026-02", index_paths.RELEASES_ROOT)

    with pytest.raises(HTTPException) as exc:
        kb_admin._validate_release_artifacts("gz-2026-02")
    assert exc.value.status_code == 409
    assert "content_hash" in exc.value.detail


def test_publish_accepts_matching_hex_content_hash(temp_registry):
    _published_registry(temp_registry)
    _write_release_artifact("gz-2026-03", index_paths.RELEASES_ROOT)
    own_manifest = release_artifacts.validate_release_dir(
        index_paths.release_dir("gz-2026-03"), release_id="gz-2026-03"
    )
    temp_registry.create_release(
        release_id="gz-2026-03", package_id="guizhou", version="2026.03",
        artifact_path="data/kb_releases/gz-2026-03",
        content_hash=release_artifacts.manifest_content_hash(own_manifest),
        based_on_national_release_id="nat-2026-01",
        build_status="success", eval_status="passed", created_by="13985000001",
    )
    kb_admin._validate_release_artifacts("gz-2026-03")  # 不抛即通过
