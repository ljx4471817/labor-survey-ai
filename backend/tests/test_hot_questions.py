from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.persistence import query_log as query_log_module
from app.services import hot_questions

UTC8 = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=UTC8)


@pytest.fixture
def query_log_db(tmp_path, monkeypatch):
    monkeypatch.setattr(query_log_module, "DB_PATH", tmp_path / "query_log.db")
    monkeypatch.setattr(query_log_module, "_conn", None)
    return query_log_module


def _logged_query(
    phone: str,
    query: str,
    *,
    mode: str = "rag",
    top_qa_id: str | None = None,
    days_ago: int = 0,
    province: str = "贵州省",
    province_code: str | None = None,
) -> dict:
    return {
        "phone": phone,
        "name": phone,
        "province": province,
        "province_code": province_code,
        "city": "贵阳市",
        "county": "南明区",
        "query": query,
        "mode": mode,
        "top_qa_id": top_qa_id,
        "ts": (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"),
    }


def test_query_log_stores_top_qa_id(query_log_db):
    query_log_module.insert(
        _logged_query("13900000001", "每周工作15小时算就业吗？", top_qa_id="001")
    )

    row = query_log_module._get_conn().execute(
        "SELECT top_qa_id FROM query_log WHERE query = ?",
        ("每周工作15小时算就业吗？",),
    ).fetchone()
    assert row["top_qa_id"] == "001"


def test_top_qa_stats_filters_and_ranks(query_log_db):
    records = [
        _logged_query("13900000001", "q1", top_qa_id="001", days_ago=29),
        _logged_query("13900000002", "q1 again", top_qa_id="001", days_ago=28),
        _logged_query("13900000001", "q2", top_qa_id="002", days_ago=3),
        _logged_query("13900000001", "q2 follow up", top_qa_id="002", days_ago=2),
        _logged_query("13900000001", "q2 third", top_qa_id="002", days_ago=1),
        _logged_query("13900000004", "q2", top_qa_id="002", days_ago=1),
        _logged_query("13900000001", "too old", top_qa_id="003", days_ago=31),
        _logged_query("13900000001", "single user", top_qa_id="004", days_ago=1),
        _logged_query("13900000001", "chunk answer", mode="rag", days_ago=1),
        _logged_query("13900000001", "refused", mode="out_of_kb", top_qa_id="005"),
        _logged_query("13900000001", "ambiguous", mode="ambiguous", top_qa_id="006"),
        _logged_query("13985000001", "eval", top_qa_id="007"),
        _logged_query("13985000002", "eval again", top_qa_id="007"),
    ]
    for record in records:
        query_log_module.insert(record)

    stats = query_log_module.top_qa_stats(now=NOW)

    expected_stats = {
        "001": {
            "top_qa_id": "001",
            "query_count": 2,
            "user_count": 2,
            "last_asked_at": (NOW - timedelta(days=28)).isoformat(timespec="seconds"),
        },
        "002": {
            "top_qa_id": "002",
            "query_count": 4,
            "user_count": 2,
            "last_asked_at": (NOW - timedelta(days=1)).isoformat(timespec="seconds"),
        },
        "004": {
            "top_qa_id": "004",
            "query_count": 1,
            "user_count": 1,
            "last_asked_at": (NOW - timedelta(days=1)).isoformat(timespec="seconds"),
        },
        "007": {
            "top_qa_id": "007",
            "query_count": 2,
            "user_count": 2,
            "last_asked_at": NOW.isoformat(timespec="seconds"),
        },
    }
    assert {row["top_qa_id"]: row for row in stats} == expected_stats


def test_top_qa_stats_filter_by_province_code_or_legacy_province(query_log_db):
    records = [
        _logged_query("13900000001", "gz old", top_qa_id="001", days_ago=1),
        _logged_query("13900000002", "gz new", top_qa_id="001", days_ago=1, province_code="520000"),
        _logged_query(
            "13900000003", "sc new", top_qa_id="002", days_ago=1,
            province="四川省", province_code="510000",
        ),
        _logged_query(
            "13900000004", "sc legacy", top_qa_id="002", days_ago=1,
            province="四川省",
        ),
    ]
    for record in records:
        query_log_module.insert(record)

    gz = query_log_module.top_qa_stats(now=NOW, province="贵州省", province_code="520000")
    sc = query_log_module.top_qa_stats(now=NOW, province="四川省", province_code="510000")
    assert {row["top_qa_id"] for row in gz} == {"001"}
    assert {row["top_qa_id"] for row in sc} == {"002"}


def test_hot_questions_cache_isolated_by_province(monkeypatch):
    hot_questions.reset_hot_questions_cache()
    calls: list[str] = []

    def stats_fn():
        calls.append("called")
        return [
            {
                "top_qa_id": "001",
                "query_count": 2,
                "user_count": 2,
                "last_asked_at": NOW.isoformat(),
            }
        ]

    def faq_loader():
        return {"001": "标准问法"}

    first = hot_questions.get_hot_questions(
        cache_key="520000", stats_fn=stats_fn, faq_loader=faq_loader,
        default_loader=lambda: [{"question": "默认"}], clock=lambda: 0,
    )
    second = hot_questions.get_hot_questions(
        cache_key="520000", stats_fn=stats_fn, faq_loader=faq_loader,
        default_loader=lambda: [{"question": "默认"}], clock=lambda: 60,
    )
    other = hot_questions.get_hot_questions(
        cache_key="510000", stats_fn=stats_fn, faq_loader=faq_loader,
        default_loader=lambda: [{"question": "默认"}], clock=lambda: 60,
    )

    assert first == second == other == {"items": [{"question": "标准问法"}]}
    assert calls == ["called", "called"]
    hot_questions.reset_hot_questions_cache()


def test_build_hot_questions_maps_and_limits():
    stats = [
        {"top_qa_id": "001", "query_count": 5, "user_count": 3, "last_asked_at": "3"},
        {"top_qa_id": "002", "query_count": 9, "user_count": 3, "last_asked_at": "4"},
        {"top_qa_id": "404", "query_count": 2, "user_count": 2, "last_asked_at": "5"},
        {"top_qa_id": "004", "query_count": 9, "user_count": 1, "last_asked_at": "6"},
    ]
    questions = {"001": "标准问法一", "002": "标准问法二"}

    items = hot_questions.build_hot_questions(stats, questions, max_items=2)

    assert items == [{"question": "标准问法二"}, {"question": "标准问法一"}]


def test_load_default_questions_handles_config_error(tmp_path):
    config_path = tmp_path / "default_questions.json"
    config_path.write_text('{"broken": true}', encoding="utf-8")

    assert hot_questions.load_default_questions(config_path) == [
        {"question": question} for question in hot_questions.DEFAULT_QUESTIONS
    ]


def test_get_hot_questions_uses_thirty_minute_cache(monkeypatch):
    hot_questions.reset_hot_questions_cache()
    calls: list[int] = []
    current_time = {"value": 0}

    def stats_fn(**_kwargs):
        calls.append(current_time["value"])
        return [
            {
                "top_qa_id": "001",
                "query_count": 2,
                "user_count": 2,
                "last_asked_at": NOW.isoformat(),
            }
        ]

    def faq_loader():
        return {"001": "标准问法"}

    first = hot_questions.get_hot_questions(
        stats_fn=stats_fn,
        faq_loader=faq_loader,
        default_loader=lambda: [{"question": "默认"}],
        clock=lambda: current_time["value"],
    )
    current_time["value"] = 60
    second = hot_questions.get_hot_questions(
        stats_fn=stats_fn,
        faq_loader=faq_loader,
        default_loader=lambda: [{"question": "默认"}],
        clock=lambda: current_time["value"],
    )
    current_time["value"] = 30 * 60 + 1
    third = hot_questions.get_hot_questions(
        stats_fn=stats_fn,
        faq_loader=faq_loader,
        default_loader=lambda: [{"question": "默认"}],
        clock=lambda: current_time["value"],
    )

    assert first == second == third == {"items": [{"question": "标准问法"}]}
    assert len(calls) == 2
    hot_questions.reset_hot_questions_cache()

def test_hot_questions_endpoint_filters_by_user_province(tmp_path, monkeypatch):
    from app.api import hot_questions as endpoint
    from app.persistence import query_log
    from app.services.kb_pack_resolver import PackCombination

    # 隔离真实 query_log.db：本地手测数据会让「该省无记录」断言失效
    monkeypatch.delenv("LSX_DB_QUERY_LOG", raising=False)
    monkeypatch.setattr(query_log, "DB_PATH", tmp_path / "query_log.db")
    monkeypatch.setattr(query_log, "_conn", None)

    combination = PackCombination(
        mode="packs", province_code="520000",
        national_package_id="national-base", national_release_id="nat-01",
        provincial_package_id="guizhou", provincial_release_id="gz-01",
        fallback_reason=None, warning=None,
    )
    captured: dict = {}

    def fake_get_hot_questions(**kwargs):
        captured.update(kwargs)
        return {"items": [{"question": "贵州热点"}]}

    monkeypatch.setattr(endpoint, "get_current_user", lambda phone: {"province": "贵州省"})
    monkeypatch.setattr(endpoint, "get_request_combination", lambda phone: combination)
    monkeypatch.setattr(endpoint, "get_hot_questions", fake_get_hot_questions)
    response = endpoint.hot_questions_endpoint(phone="13900000001")

    assert response == {"items": [{"question": "贵州热点"}]}
    assert captured["cache_key"] == "520000"
    stats = captured["stats_fn"]()
    assert stats == []
