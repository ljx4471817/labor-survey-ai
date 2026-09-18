# -*- coding: utf-8 -*-
"""gaps 分析模块测试（SQLite / PG 双后端参数化）。

背景：PG 不允许在 HAVING 里用 SELECT 列别名（SQLite 允许），该类问题此前
无任何测试覆盖、只在切库后暴露。本文件沿 test_db_adapter.py 的参数化约定：
不设 LSX_TEST_PG_DSN 时 PG 用例自动 skip，本地零配置可跑。
"""
from __future__ import annotations

import json
import os

import pytest

from app.analytics import gaps
from app.persistence import db, query_log as query_log_mod

PG_DSN = os.environ.get("LSX_TEST_PG_DSN")

BACKEND_PARAMS = [
    pytest.param("sqlite", id="sqlite"),
    pytest.param(
        "pg",
        id="pg",
        marks=pytest.mark.skipif(
            PG_DSN is None,
            reason="未设置 LSX_TEST_PG_DSN，跳过 PostgreSQL 参数化用例",
        ),
    ),
]


@pytest.fixture(params=BACKEND_PARAMS)
def gaps_env(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        target = str(tmp_path / "query_log.db")
    else:
        target = PG_DSN
        pg = db.connect(target)
        pg.executescript("DROP TABLE IF EXISTS query_log")
        pg.commit()
    monkeypatch.setenv("LSX_DB_QUERY_LOG", target)
    monkeypatch.setattr(query_log_mod, "_conn", None)

    feedback_path = tmp_path / "feedback.jsonl"
    monkeypatch.setattr(gaps, "FEEDBACK_PATH", feedback_path)
    yield feedback_path
    monkeypatch.setattr(query_log_mod, "_conn", None)


def _log_query(query, phone, mode, request_id=None, ts=None):
    query_log_mod.insert({
        "phone": phone,
        "name": "调查员" + phone[-2:],
        "province": "贵州省",
        "city": "贵阳市",
        "county": "南明区",
        "query": query,
        "mode": mode,
        "request_id": request_id,
        "ts": ts,
    })


def _write_feedback(path, records):
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_high_freq_out_of_scope(gaps_env):
    # 同一 query 三次、来自两个用户 -> freq=3 / user_count=2
    for i, phone in enumerate(["13900000001", "13900000002", "13900000001"]):
        _log_query("怎么开发票", phone, "out_of_scope")
    # 另一条低频 query，低于 min_freq 阈值
    _log_query("天气怎么样", "13900000003", "out_of_scope")
    # rag 模式不参与统计
    _log_query("F10 怎么填", "13900000001", "rag", request_id="r-ok")

    items = gaps.high_freq_out_of_scope(since_days=7, min_freq=3)
    assert len(items) == 1
    item = items[0]
    assert item["query"] == "怎么开发票"
    assert item["freq"] == 3
    assert item["user_count"] == 2
    assert item["last_seen"]
    assert item["action"] == "考虑新增 KB 条目"


def test_kb_hit_but_down(gaps_env):
    _log_query("F10 怎么填", "13900000001", "rag", request_id="r1")
    _log_query("F10 填否可以吗", "13900000002", "rag", request_id="r2")

    _write_feedback(gaps_env, [
        {"rating": "down", "mode": "rag", "request_id": "r1",
         "question": "F10 怎么填", "comment": "答非所问",
         "sources": [{"qa_id": "001"}]},
        {"rating": "down", "mode": "rag", "request_id": "r2",
         "question": "F10 填否可以吗", "comment": "口径不对",
         "sources": [{"qa_id": "001"}]},
        # 无对应 query_log 的 request_id 应被过滤
        {"rating": "down", "mode": "rag", "request_id": "r-missing",
         "question": "孤儿反馈", "sources": [{"qa_id": "001"}]},
        # 非差评不算
        {"rating": "up", "mode": "rag", "request_id": "r1",
         "question": "好评", "sources": [{"qa_id": "001"}]},
        # 非 rag 模式不算
        {"rating": "down", "mode": "out_of_scope", "request_id": "r1",
         "question": "越界", "sources": [{"qa_id": "001"}]},
    ])

    items = gaps.kb_hit_but_down(since_days=7, min_freq=2)
    assert len(items) == 1
    item = items[0]
    assert item["qa_id"] == "001"
    assert item["down_count"] == 2
    assert item["sample_questions"] == ["F10 怎么填", "F10 填否可以吗"]
    assert len(item["sample_comments"]) == 2
    assert item["action"] == "review KB 该条目，可能需要改写"