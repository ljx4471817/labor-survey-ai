# -*- coding: utf-8 -*-
"""query_log 多知识包字段迁移与写入（TDD）。"""
from __future__ import annotations

import pytest

from app.persistence import query_log


PACK_COLUMNS = (
    "province_code",
    "pack_mode",
    "kb_id",
    "release_id",
    "global_entry_key",
    "override_used",
    "fallback_reason",
    "national_package_id",
    "national_release_id",
    "provincial_package_id",
    "provincial_release_id",
)


@pytest.fixture
def temp_query_log(tmp_path, monkeypatch):
    monkeypatch.delenv("LSX_DB_QUERY_LOG", raising=False)
    monkeypatch.setattr(query_log, "DB_PATH", tmp_path / "query_log.db")
    monkeypatch.setattr(query_log, "_conn", None)
    yield query_log
    query_log._conn = None


def _required(entry):
    return {
        "phone": "13900000001",
        "name": "测试",
        "province": "贵州省",
        "city": "贵阳市",
        "county": "南明区",
        "township": "新华路街道",
        "community": "神奇路社区",
        "query": "每周工作15小时算就业吗？",
        "mode": "rag",
        **entry,
    }


def test_insert_supports_flat_entries_without_pack_fields(temp_query_log):
    temp_query_log.insert(_required({}))
    row = dict(
        temp_query_log._get_conn().execute(
            "SELECT * FROM query_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    )
    assert row["kb_id"] is None
    assert row["release_id"] is None
    assert row["override_used"] is None


def test_insert_persists_pack_selection_fields(temp_query_log):
    temp_query_log.insert(
        _required(
            {
                "province_code": "520000",
                "pack_mode": "packs",
                "kb_id": "guizhou",
                "release_id": "gz-01",
                "global_entry_key": "guizhou:gz-01:001",
                "override_used": 1,
                "fallback_reason": "incompatible_current_release",
                "national_package_id": "national-base",
                "national_release_id": "nat-01",
                "provincial_package_id": "guizhou",
                "provincial_release_id": "gz-01",
            }
        )
    )
    row = dict(
        temp_query_log._get_conn().execute(
            "SELECT * FROM query_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
    )
    for column in PACK_COLUMNS:
        assert row[column] is not None


def test_schema_has_nullable_pack_columns(temp_query_log):
    columns = {
        row["name"]
        for row in temp_query_log._get_conn().execute(
            "PRAGMA table_info(query_log)"
        ).fetchall()
    }
    assert set(PACK_COLUMNS) <= columns
