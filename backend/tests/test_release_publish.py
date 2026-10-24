# -*- coding: utf-8 -*-
"""release_publish 发布用例（TDD）：产物门禁 + 状态门禁的单一入口。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.persistence import knowledge_registry as registry
from app.rag import index_paths
from app.services import release_artifacts, release_publish


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
    target_root = root / release_id
    for relpath in ("bm25_index.json", "chunks.jsonl", "chroma/chroma.sqlite3"):
        target = target_root / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relpath.encode("utf-8"))
    release_artifacts.write_artifact_manifest(target_root, release_id)


def _seed_published_national(temp_registry):
    temp_registry.create_package(
        package_id="national-base", name="全国基础知识包", kind="national",
        created_by="13985000001",
    )
    temp_registry.create_release(
        release_id="nat-2026-01", package_id="national-base", version="2026.01",
        artifact_path="data/kb_releases/nat-2026-01", content_hash="legacy",
        build_status="success", eval_status="passed", created_by="13985000001",
    )
    _write_release_artifact("nat-2026-01", index_paths.RELEASES_ROOT)


def test_publish_requires_registered_release(temp_registry):
    with pytest.raises(release_publish.ReleaseNotFoundError):
        release_publish.publish_release("national-base", "no-such", actor="13985000001")


def test_publish_rejects_broken_artifacts(temp_registry):
    _seed_published_national(temp_registry)
    (index_paths.release_dir("nat-2026-01") / "bm25_index.json").write_bytes(b"broken")
    with pytest.raises(release_publish.ReleaseArtifactUnavailable):
        release_publish.publish_release("national-base", "nat-2026-01", actor="13985000001")


def test_publish_rejects_hex_hash_mismatch(temp_registry):
    _seed_published_national(temp_registry)
    registry._get_conn().execute(
        "UPDATE kb_releases SET content_hash = ? WHERE id = ?",
        ("f" * 64, "nat-2026-01"),
    )
    registry._get_conn().commit()
    with pytest.raises(release_publish.ReleaseHashMismatch):
        release_publish.publish_release("national-base", "nat-2026-01", actor="13985000001")


def test_publish_moves_current_release_and_legacy_hash_passes(temp_registry):
    _seed_published_national(temp_registry)
    updated = release_publish.publish_release("national-base", "nat-2026-01", actor="13985000001")
    assert updated["current_release_id"] == "nat-2026-01"
    assert updated["status"] == "active"


def test_rollback_rejects_current_release(temp_registry):
    _seed_published_national(temp_registry)
    release_publish.publish_release("national-base", "nat-2026-01", actor="13985000001")
    with pytest.raises(ValueError, match="当前发布版本"):
        release_publish.rollback_release("national-base", "nat-2026-01", actor="13985000001")
