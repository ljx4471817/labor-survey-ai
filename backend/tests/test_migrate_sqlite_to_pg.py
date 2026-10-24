# -*- coding: utf-8 -*-
"""SQLite -> PostgreSQL 迁移脚本的五库映射测试。"""
from __future__ import annotations

import importlib.util
import json
import sys

import pytest
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[2] / "backend" / "scripts" / "migrate_sqlite_to_pg.py"
    spec = importlib.util.spec_from_file_location("migrate_sqlite_to_pg", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_migration_covers_knowledge_admin_registry():
    module = load_module()
    assert "knowledge_admin" in module.DB_KEYS
    assert module.SQLITE_FILES["knowledge_admin"] == "knowledge_admin.db"
    assert module.ENV_VARS["knowledge_admin"] == "LSX_DB_KB_ADMIN"
    assert module.PG_SCHEMAS["knowledge_admin"] == [module.knowledge_registry._SCHEMA_PG]


def test_resolve_dsns_requires_all_five_targets(monkeypatch):
    module = load_module()
    dsn = "postgresql://user:pass@localhost:5432/lsx_test"
    monkeypatch.delenv("LSX_DB_WHITELIST", raising=False)
    monkeypatch.delenv("LSX_DB_CONVERSATIONS", raising=False)
    monkeypatch.delenv("LSX_DB_QUERY_LOG", raising=False)
    monkeypatch.delenv("LSX_DB_QUIZ", raising=False)
    monkeypatch.delenv("LSX_DB_KB_ADMIN", raising=False)

    with pytest.raises(SystemExit, match="缺少 DSN"):
        module._resolve_dsns(type("Args", (), {"dsn_map": None}))

    resolved = module._resolve_dsns(type("Args", (), {"dsn_map": json.dumps({key: dsn for key in module.DB_KEYS})}))
    assert set(resolved) == set(module.DB_KEYS)
    assert all(value == dsn for value in resolved.values())
