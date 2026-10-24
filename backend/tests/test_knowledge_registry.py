# -*- coding: utf-8 -*-
"""知识包注册库持久化（TDD）。"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from app.persistence import db
from app.persistence import knowledge_registry as registry
from app.services import kb_pack_resolver as resolver


UTC8 = timezone(timedelta(hours=8))


@pytest.fixture
def temp_registry(tmp_path, monkeypatch):
    monkeypatch.delenv("LSX_DB_KB_ADMIN", raising=False)
    monkeypatch.setattr(registry, "DB_PATH", tmp_path / "knowledge_admin.db")
    monkeypatch.setattr(registry, "_schema_ready_for", None)
    registry.reset_conn()
    yield registry
    registry.reset_conn()


def _now() -> str:
    return datetime.now(UTC8).isoformat(timespec="seconds")


def test_schema_has_registry_tables(temp_registry):
    conn = temp_registry._get_conn()
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "kb_packages",
        "kb_releases",
        "province_kb_bindings",
        "kb_admin_audit",
    } <= tables


def test_create_package_is_audited_and_rejects_duplicate(temp_registry):
    package = temp_registry.create_package(
        package_id="national-base",
        name="全国基础知识包",
        kind="national",
        created_by="13985000001",
    )
    assert package["status"] == "draft"
    assert package["current_release_id"] is None

    with pytest.raises(db.IntegrityError):
        temp_registry.create_package(
            package_id="national-base",
            name="重复包",
            kind="national",
            created_by="13985000001",
        )

    audit = temp_registry.list_audit()
    assert audit[-1]["action"] == "create_package"
    assert audit[-1]["target"] == "kb_package:national-base"
    assert audit[-1]["actor"] == "13985000001"


def test_create_package_only_allows_draft_status(temp_registry):
    with pytest.raises(ValueError, match="draft"):
        temp_registry.create_package(
            package_id="guizhou", name="贵州知识包", kind="provincial",
            status="active", created_by="13985000001",
        )


def test_failed_write_rolls_back_before_and_after_snapshots(temp_registry, monkeypatch):
    package = temp_registry.create_package(
        package_id="guizhou", name="贵州知识包", kind="provincial",
        created_by="13985000001",
    )

    def fail_audit(*args, **kwargs):
        raise RuntimeError("audit failed")

    monkeypatch.setattr(registry, "_write_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit failed"):
        temp_registry.update_package(
            "guizhou", actor="13985000001", name="贵州口径包"
        )

    assert temp_registry.get_package("guizhou") == package
    assert [row["action"] for row in registry.list_audit()] == ["create_package"]


def test_release_id_must_be_runtime_safe(temp_registry):
    temp_registry.create_package(
        package_id="guizhou", name="贵州知识包", kind="provincial",
        created_by="13985000001",
    )
    with pytest.raises(ValueError, match="非法知识包版本"):
        temp_registry.create_release(
            release_id="../escape", package_id="guizhou", version="2026.01",
            artifact_path="data/kb_releases/escape", content_hash="abc",
            based_on_national_release_id="nat-001",
            created_by="13985000001",
        )


def test_release_requires_compatible_based_on_reference(temp_registry):
    temp_registry.create_package(
        package_id="national-base",
        name="全国基础知识包",
        kind="national",
        created_by="13985000001",
    )
    with pytest.raises(ValueError, match="全国包版本不能基于其他版本"):
        temp_registry.create_release(
            release_id="nat-001",
            package_id="national-base",
            version="2026.01",
            artifact_path="releases/nat-001",
            content_hash="abc",
            based_on_national_release_id="missing",
            created_by="13985000001",
        )

    temp_registry.create_release(
        release_id="nat-001",
        package_id="national-base",
        version="2026.01",
        artifact_path="releases/nat-001",
        content_hash="abc",
        created_by="13985000001",
    )
    temp_registry.create_package(
        package_id="guizhou",
        name="贵州知识包",
        kind="provincial",
        created_by="13985000001",
    )
    with pytest.raises(ValueError, match="省级包版本必须基于"):
        temp_registry.create_release(
            release_id="gz-001",
            package_id="guizhou",
            version="2026.01",
            artifact_path="releases/gz-001",
            content_hash="def",
            created_by="13985000001",
        )


def test_publish_requires_successful_build_and_passed_eval(temp_registry):
    temp_registry.create_package(
        package_id="national-base",
        name="全国基础知识包",
        kind="national",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="nat-001",
        package_id="national-base",
        version="2026.01",
        artifact_path="releases/nat-001",
        content_hash="abc",
        build_status="success",
        eval_status="passed",
        created_by="13985000001",
    )

    published = temp_registry.publish_release(
        "national-base", "nat-001", actor="13985000001"
    )
    assert published["current_release_id"] == "nat-001"
    assert published["status"] == "active"
    assert temp_registry.get_release("nat-001")["published_at"]
    assert temp_registry.list_audit()[-1]["action"] == "publish_release"


def test_publish_rejects_unsuccessful_release(temp_registry):
    temp_registry.create_package(
        package_id="national-base",
        name="全国基础知识包",
        kind="national",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="nat-001",
        package_id="national-base",
        version="2026.01",
        artifact_path="releases/nat-001",
        content_hash="abc",
        build_status="success",
        eval_status="pending",
        created_by="13985000001",
    )

    with pytest.raises(ValueError, match="Eval"):
        temp_registry.publish_release(
            "national-base", "nat-001", actor="13985000001"
        )


def test_published_release_gate_status_is_immutable(temp_registry):
    temp_registry.create_package(
        package_id="national-base", name="全国基础知识包", kind="national",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="nat-001", package_id="national-base", version="2026.01",
        artifact_path="data/kb_releases/nat-001", content_hash="abc",
        build_status="success", eval_status="passed", created_by="13985000001",
    )
    temp_registry.publish_release("national-base", "nat-001", actor="13985000001")

    with pytest.raises(ValueError, match="已发布版本状态不可修改"):
        temp_registry.update_release_status(
            release_id="nat-001", actor="13985000001", eval_status="pending"
        )


def test_bind_and_unbind_province_are_audited(temp_registry):
    temp_registry.create_package(
        package_id="national-base",
        name="全国基础知识包",
        kind="national",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="nat-001",
        package_id="national-base",
        version="2026.01",
        artifact_path="releases/nat-001",
        content_hash="abc",
        build_status="success",
        eval_status="passed",
        created_by="13985000001",
    )
    temp_registry.publish_release(
        "national-base", "nat-001", actor="13985000001"
    )

    temp_registry.create_package(
        package_id="guizhou",
        name="贵州知识包",
        kind="provincial",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="gz-001",
        package_id="guizhou",
        version="2026.01",
        artifact_path="releases/gz-001",
        content_hash="def",
        based_on_national_release_id="nat-001",
        build_status="success",
        eval_status="passed",
        created_by="13985000001",
    )
    temp_registry.publish_release("guizhou", "gz-001", actor="13985000001")

    with pytest.raises(ValueError, match="知识包不存在"):
        temp_registry.bind_province("520000", "missing-package", actor="13985000001")

    assert temp_registry.bind_province(
        "520000", "guizhou", actor="13985000001"
    )["package_id"] == "guizhou"
    assert temp_registry.get_resolution_inputs()[2] == {"520000": "guizhou"}
    assert temp_registry.unbind_province(
        "520000", actor="13985000001"
    ) is True
    assert temp_registry.get_resolution_inputs()[2] == {}
    assert [row["action"] for row in temp_registry.list_audit()[-3:]] == [
        "publish_release",
        "bind_province",
        "unbind_province",
    ]


def test_registry_exposes_resolver_inputs(temp_registry):
    temp_registry.create_package(
        package_id="national-base",
        name="全国基础知识包",
        kind="national",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="nat-001",
        package_id="national-base",
        version="2026.01",
        artifact_path="releases/nat-001",
        content_hash="abc",
        build_status="success",
        eval_status="passed",
        created_by="13985000001",
    )
    temp_registry.publish_release(
        "national-base", "nat-001", actor="13985000001"
    )

    packages, releases, bindings = temp_registry.get_resolution_inputs()
    assert packages[0]["current_release_id"] == "nat-001"
    assert releases[0]["id"] == "nat-001"
    assert bindings == {}


def test_registry_resolution_inputs_feed_pure_resolver(temp_registry):
    temp_registry.create_package(
        package_id="national-base",
        name="全国基础知识包",
        kind="national",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="nat-001",
        package_id="national-base",
        version="2026.01",
        artifact_path="releases/nat-001",
        content_hash="abc",
        build_status="success",
        eval_status="passed",
        created_by="13985000001",
    )
    temp_registry.publish_release("national-base", "nat-001", actor="13985000001")

    temp_registry.create_package(
        package_id="guizhou",
        name="贵州知识包",
        kind="provincial",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="gz-001",
        package_id="guizhou",
        version="2026.01",
        artifact_path="releases/gz-001",
        content_hash="def",
        based_on_national_release_id="nat-001",
        build_status="success",
        eval_status="passed",
        created_by="13985000001",
    )
    temp_registry.publish_release("guizhou", "gz-001", actor="13985000001")
    temp_registry.bind_province("520000", "guizhou", actor="13985000001")

    packages, releases, bindings = temp_registry.get_resolution_inputs()
    result = resolver.resolve_pack_combination(
        province_name="黔",
        province_catalog=resolver.load_province_catalog(),
        packages=packages,
        releases=releases,
        bindings=bindings,
    )
    assert result.national_release_id == "nat-001"
    assert result.provincial_release_id == "gz-001"


@pytest.mark.skipif(
    os.environ.get("LSX_TEST_PG_DSN") is None,
    reason="未设置 LSX_TEST_PG_DSN，跳过 PostgreSQL schema 用例",
)
def test_registry_pg_schema_is_idempotent():
    conn = db.connect(os.environ["LSX_TEST_PG_DSN"])
    conn.executescript(registry._SCHEMA_PG)
    conn.commit()


def test_registry_target_supports_environment_override(monkeypatch):
    monkeypatch.setenv("LSX_DB_KB_ADMIN", "sqlite:///tmp/kb-admin.db")
    assert registry._db_target() == "sqlite:///tmp/kb-admin.db"
