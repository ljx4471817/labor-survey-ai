"""白名单缓存键时间桶单测（PG 模式 mtime 失灵兜底，见 runbook 20260929）。"""
from __future__ import annotations

from app.infra import auth


def test_cache_key_advances_with_time_bucket(monkeypatch) -> None:
    """mtime 恒定时（PG 模式），缓存键仍随 TTL 时间桶推进。"""
    monkeypatch.setattr(auth, "WHITELIST_CACHE_TTL", 10)
    clock = iter([100, 105, 110])  # 同桶 → 同键；跨桶 → 新键
    monkeypatch.setattr(auth.time, "time", lambda: next(clock))

    key_a = auth._whitelist_cache_key()
    key_b = auth._whitelist_cache_key()
    key_c = auth._whitelist_cache_key()

    assert key_a == key_b  # TTL 内不抖动
    assert key_c != key_a  # 跨桶后缓存失效，强制重读 DB


def test_cache_key_suffix_contains_time_bucket(monkeypatch) -> None:
    """缓存键末段必须是时间桶值，保证与旧 mtime 键不可碰撞。"""
    monkeypatch.setattr(auth, "WHITELIST_CACHE_TTL", 30)
    monkeypatch.setattr(auth.time, "time", lambda: 61)

    keys = auth._whitelist_cache_key()

    assert keys[-1] == 2  # 61 // 30
