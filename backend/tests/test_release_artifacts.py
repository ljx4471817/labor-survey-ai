# -*- coding: utf-8 -*-
"""release 索引产物完整性校验（TDD）。"""
from __future__ import annotations

import hashlib
import json

import pytest

from app.services import release_artifacts


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _build_release(root, release_id="gz-2026-01"):
    root.mkdir(parents=True, exist_ok=True)
    _write(root / "bm25_index.json", b"bm25")
    _write(root / "chunks.jsonl", b"chunks")
    _write(root / "chroma" / "chroma.sqlite3", b"chroma")
    release_artifacts.write_artifact_manifest(root, release_id)


def test_artifact_manifest_lists_all_release_files(tmp_path):
    root = tmp_path / "gz-2026-01"
    _build_release(root)
    manifest_path = root / "artifact_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert payload["release_id"] == "gz-2026-01"
    assert set(payload["files"]) == {
        "bm25_index.json",
        "chunks.jsonl",
        "chroma/chroma.sqlite3",
    }
    bm25 = payload["files"]["bm25_index.json"]
    assert bm25["sha256"] == hashlib.sha256(b"bm25").hexdigest()


def test_validate_release_artifacts_passes_complete_release(tmp_path):
    root = tmp_path / "gz-2026-01"
    _build_release(root)
    release_artifacts.validate_release_dir(root)


def test_validate_release_artifacts_detects_modified_file(tmp_path):
    root = tmp_path / "gz-2026-01"
    _build_release(root)
    (root / "bm25_index.json").write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash mismatch"):
        release_artifacts.validate_release_dir(root)


def test_validate_release_artifacts_requires_required_files_and_manifest(tmp_path):
    root = tmp_path / "gz-2026-01"
    root.mkdir()
    with pytest.raises(ValueError, match="manifest"):
        release_artifacts.validate_release_dir(root)

    (root / "artifact_manifest.json").write_text(json.dumps({"release_id":"gz-2026-01","files":{}}), encoding="utf-8")
    with pytest.raises(ValueError, match="missing required artifact"):
        release_artifacts.validate_release_dir(root)


def test_validate_release_artifacts_rejects_release_id_mismatch(tmp_path):
    root = tmp_path / "gz-2026-01"
    _build_release(root, "other-release")
    with pytest.raises(ValueError, match="release_id"):
        release_artifacts.validate_release_dir(root)


def test_validate_release_artifacts_rejects_path_traversal_entry(tmp_path):
    root = tmp_path / "gz-2026-01"
    _build_release(root)
    manifest_path = root / "artifact_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["files"]["../outside.txt"] = {"size": 1, "sha256": "x"}
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid manifest entry"):
        release_artifacts.validate_release_dir(root)


def test_manifest_content_hash_is_deterministic_and_binds_files(tmp_path):
    root = tmp_path / "gz-2026-01"
    _build_release(root)
    payload = json.loads(
        (root / "artifact_manifest.json").read_text(encoding="utf-8")
    )
    h1 = release_artifacts.manifest_content_hash(payload)
    h2 = release_artifacts.manifest_content_hash(payload)
    assert h1 == h2
    assert len(h1) == 64

    (root / "bm25_index.json").write_bytes(b"changed")
    changed = release_artifacts.build_artifact_manifest(root, "gz-2026-01")
    assert release_artifacts.manifest_content_hash(changed) != h1


def test_volatile_chroma_segment_files_excluded_from_manifest(tmp_path):
    root = tmp_path / "gz-2026-01"
    _build_release(root)
    segment = root / "chroma" / "f1cf67e6-uuid" / "length.bin"
    _write(segment, b"segments")

    payload = json.loads((root / "artifact_manifest.json").read_text(encoding="utf-8"))
    assert not any(
        key.startswith("chroma/") and key != "chroma/chroma.sqlite3"
        for key in payload["files"]
    )
    release_artifacts.validate_release_dir(root)  # 不抛即通过


def test_volatile_chroma_file_change_does_not_fail_validation(tmp_path):
    root = tmp_path / "gz-2026-01"
    _build_release(root)
    segment = root / "chroma" / "f1cf67e6-uuid" / "length.bin"
    _write(segment, b"segments")
    release_artifacts.validate_release_dir(root)

    # 模拟 chroma 客户端打开时重写派生文件
    segment.write_bytes(b"rewritten-on-open")
    release_artifacts.validate_release_dir(root)  # 仍通过
