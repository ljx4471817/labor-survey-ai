from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api import usage_trend_admin
from app.persistence import query_log as query_log_module
from app.persistence import usage_trend


UTC8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC8)


@pytest.fixture
def query_log_db(tmp_path, monkeypatch):
    monkeypatch.setattr(query_log_module, "DB_PATH", tmp_path / "query_log.db")
    monkeypatch.setattr(query_log_module, "_conn", None)
    return query_log_module


def _logged_query(
    *,
    county: str = "南明区",
    mode: str = "rag",
    days_ago: int = 0,
) -> dict:
    return {
        "phone": "13900000001",
        "name": "调查员甲",
        "province": "贵州省",
        "city": "贵阳市",
        "county": county,
        "query": "F10 怎么填？",
        "mode": mode,
        "ts": (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"),
    }


def test_daily_usage_counts_all_modes_and_fills_zero_days(query_log_db):
    records = [
        _logged_query(days_ago=0),
        _logged_query(mode="out_of_scope", days_ago=0),
        _logged_query(mode="ambiguous", days_ago=1),
        _logged_query(mode="out_of_kb", days_ago=6),
        _logged_query(days_ago=7),
    ]
    for record in records:
        query_log_module.insert(record)

    result = usage_trend.daily_usage_counts(days=7, now=NOW)

    assert result["start_date"] == "2026-09-01"
    assert result["end_date"] == "2026-09-07"
    assert result["total"] == 4
    assert result["series"] == [
        {"date": "2026-09-01", "count": 1},
        {"date": "2026-09-02", "count": 0},
        {"date": "2026-09-03", "count": 0},
        {"date": "2026-09-04", "count": 0},
        {"date": "2026-09-05", "count": 0},
        {"date": "2026-09-06", "count": 1},
        {"date": "2026-09-07", "count": 2},
    ]


def test_daily_usage_counts_supports_thirty_days(query_log_db):
    for days_ago in (0, 28, 29, 30):
        query_log_module.insert(_logged_query(days_ago=days_ago))

    result = usage_trend.daily_usage_counts(days=30, now=NOW)

    assert len(result["series"]) == 30
    assert result["start_date"] == "2026-08-09"
    assert result["end_date"] == "2026-09-07"
    assert result["total"] == 3
    assert result["series"][0]["count"] == 1
    assert result["series"][1]["count"] == 1
    assert result["series"][29]["count"] == 1


def test_daily_usage_counts_filters_by_region_scope(query_log_db):
    query_log_module.insert(_logged_query(county="南明区"))
    query_log_module.insert(_logged_query(county="云岩区"))

    result = usage_trend.daily_usage_counts(
        days=7,
        now=NOW,
        scope={"province": "贵州省", "city": "贵阳市", "county": "南明区"},
    )

    assert result["total"] == 1
    assert result["series"][-1]["count"] == 1


def test_usage_trend_endpoint_applies_admin_scope(query_log_db, monkeypatch):
    query_log_module.insert(_logged_query(county="南明区"))
    query_log_module.insert(_logged_query(county="云岩区"))

    result = usage_trend_admin.usage_trend(
        days=7,
        user={
            "phone": "13900000000",
            "sys_role": "业务管理员",
            "admin_level": "区县",
            "province": "贵州省",
            "city": "贵阳市",
            "county": "南明区",
        },
    )

    assert result["days"] == 7
    assert result["total"] == 1
    assert result["scope"] == {
        "province": "贵州省",
        "city": "贵阳市",
        "county": "南明区",
    }


def test_usage_trend_endpoint_rejects_invalid_days(query_log_db):
    with pytest.raises(HTTPException) as exc_info:
        usage_trend_admin.usage_trend(days=8, user={"phone": "13900000000"})

    assert exc_info.value.status_code == 422
