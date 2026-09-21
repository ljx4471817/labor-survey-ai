# -*- coding: utf-8 -*-
"""run_eval 的 HTTP 错误口径（2026-09-21 静默排除回归）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_run_eval():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_record_error_counts_into_denominator_and_keeps_evidence():
    """HTTP 错误既要进分母，也要在报告里留证据（此前被静默丢弃）。"""
    module = load_run_eval()
    results: list[dict] = []
    errors: list[dict] = []
    type_stats = {"in_kb": {"total": 0, "pass": 0}}
    item = {"id": "eval-004", "type": "in_kb", "question": "某问题？"}

    module._record_error(results, type_stats, errors, item, RuntimeError("500 Server Error"))

    assert type_stats["in_kb"] == {"total": 1, "pass": 0}
    assert errors == [{"id": "eval-004", "type": "in_kb", "error": "500 Server Error"}]
    assert results[0]["passed"] is False
    assert results[0]["checks"][0]["reason"] == "HTTP 错误：500 Server Error"


def test_error_prevents_green_summary():
    """口径回归：有错误时 passed != total —— main() 正是用这个比值决定退出码 0/1。"""
    module = load_run_eval()
    results: list[dict] = []
    errors: list[dict] = []
    type_stats = {"in_kb": {"total": 1, "pass": 1}}  # 1 题已通过
    item = {"id": "eval-050", "type": "in_kb", "question": "q"}

    module._record_error(results, type_stats, errors, item, RuntimeError("boom"))

    total = sum(s["total"] for s in type_stats.values())
    passed = sum(s["pass"] for s in type_stats.values())
    assert (passed, total) == (1, 2)
    assert passed != total
