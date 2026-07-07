"""JSONL 读取工具。"""
from __future__ import annotations

import json
from pathlib import Path

from loguru import logger


def read_jsonl(path: Path) -> list[dict]:
    """读 JSONL，每行 try/except 跳过坏行（不让一条坏记录拖垮看板）。"""
    if not path.exists():
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(json.loads(raw))
            except json.JSONDecodeError as e:
                logger.warning(f"跳过坏行 {path.name}:{lineno}: {e}")
    return out