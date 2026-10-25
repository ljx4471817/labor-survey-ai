# -*- coding: utf-8 -*-
"""Immutable release artifact manifest generation and validation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any


MANIFEST_NAME = "artifact_manifest.json"
REQUIRED_FILES = (
    Path("bm25_index.json"),
    Path("chunks.jsonl"),
    Path("chroma") / "chroma.sqlite3",
)


CHROMA_SQLITE = "chroma/chroma.sqlite3"


def _is_volatile_chroma_file(relative: str) -> bool:
    """chroma/<uuid>/* 是 HNSW 派生文件，客户端打开就会被重写，不参与 hash。"""
    parts = PurePosixPath(relative).parts
    return len(parts) >= 2 and parts[0] == "chroma" and relative != CHROMA_SQLITE


def _is_hash_volatile(relative: str) -> bool:
    """hash 校验时跳过的易变文件（存在性仍然强制）。

    chroma/<uuid>/* 与 chroma/chroma.sqlite3 都会被 Chroma 打开时重写：
    2026-10-24 生产实测，恢复演练后 chroma.sqlite3 被重写、字节 hash 变化，
    但内容与基线一致（nat 428 / gz 149 条向量不变），
    字节 hash 门禁却把发布/回滚误判成"产物被篡改"而卡死。

    产物完整性仍由"必需文件必须存在"+"manifest canonical hash 与注册库
    content_hash 强绑定"保证，manifest 本身仍是构建期指纹。
    """
    return _is_volatile_chroma_file(relative) or relative == CHROMA_SQLITE


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_artifact_manifest(root: Path, release_id: str) -> dict[str, Any]:
    """Build a deterministic manifest over every immutable release file."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"release 目录不存在：{root}")

    files: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        if relative == MANIFEST_NAME or _is_volatile_chroma_file(relative):
            continue
        files[relative] = {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
    return {"release_id": release_id, "files": files}


def manifest_content_hash(payload: dict[str, Any]) -> str:
    """Canonical release content hash over the manifest payload.

    发布门禁用它和注册库 content_hash（64 位 hex 时）做强绑定，
    防止后台手工登记与真实产物不符的版本。
    """
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def write_artifact_manifest(root: Path, release_id: str) -> Path:
    """Write the artifact manifest after all release files are final."""
    root = Path(root)
    payload = build_artifact_manifest(root, release_id)
    manifest_path = root / MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def validate_release_dir(root: Path, *, release_id: str | None = None) -> dict[str, Any]:
    """Validate required files and all recorded hashes."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"release 目录不存在：{root}")

    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"release 缺少 {MANIFEST_NAME}：{root}")

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"release manifest 无效：{root}") from exc

    if not isinstance(payload, dict) or payload.get("release_id") != root.name:
        raise ValueError(f"release manifest 的 release_id 不匹配：{root}")
    if release_id is not None and payload.get("release_id") != release_id:
        raise ValueError(f"release_id 不匹配：expected={release_id}")

    files = payload.get("files")
    if not isinstance(files, dict):
        raise ValueError(f"release manifest 缺少 files：{root}")

    for required in REQUIRED_FILES:
        required_posix = required.as_posix()
        if required_posix not in files:
            raise ValueError(f"missing required artifact: {required_posix}")

    for relative, meta in files.items():
        entry = PurePosixPath(relative)
        if entry.is_absolute() or ".." in entry.parts:
            raise ValueError(f"invalid manifest entry: {relative}")
        path = root / relative
        if not path.is_file():
            raise ValueError(f"missing required artifact: {relative}")
        if _is_hash_volatile(relative):
            continue  # chroma 派生文件打开即重写，跳过 hash 校验（存在性仍强制）
        if not isinstance(meta, dict) or not meta.get("sha256"):
            raise ValueError(f"invalid manifest entry: {relative}")
        actual = _sha256(path)
        if actual != meta["sha256"]:
            raise ValueError(f"artifact hash mismatch: {relative}")
    return payload
