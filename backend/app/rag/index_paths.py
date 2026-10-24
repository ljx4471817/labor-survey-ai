# -*- coding: utf-8 -*-
"""Pure path helpers for immutable knowledge-release artifacts."""
from __future__ import annotations

import os
import re
from pathlib import Path

from app.core.config import PROJECT_ROOT


DEFAULT_RELEASES_ROOT = PROJECT_ROOT / "backend" / "data" / "kb_releases"


def _resolve_releases_root(value: str | None) -> Path:
    """Resolve an optional root without making behavior depend on process cwd."""
    configured = Path(value) if value else DEFAULT_RELEASES_ROOT
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


RELEASES_ROOT = _resolve_releases_root(os.environ.get("KB_RELEASES_ROOT"))
_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


def validate_release_id(release_id: str) -> str:
    """Reject path traversal and return a normalized release id."""
    value = str(release_id or "").strip()
    if not value or not _RELEASE_ID_RE.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"非法知识包版本 ID：{release_id!r}")
    return value


def release_dir(release_id: str) -> Path:
    """Return the immutable artifact root for one release."""
    return RELEASES_ROOT / validate_release_id(release_id)


def chroma_dir(release_id: str) -> Path:
    """Return the release's isolated Chroma directory."""
    return release_dir(release_id) / "chroma"


def bm25_path(release_id: str) -> Path:
    """Return the release's isolated BM25 index path."""
    return release_dir(release_id) / "bm25_index.json"


def chunks_path(release_id: str) -> Path:
    """Return the release's generated chunks JSONL path."""
    return release_dir(release_id) / "chunks.jsonl"
