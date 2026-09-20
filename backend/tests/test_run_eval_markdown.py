# -*- coding: utf-8 -*-
"""run_eval 的去 markdown 比对（2026-09-20 eval-112 假失败回归）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def load_run_eval():
    path = Path(__file__).resolve().parents[2] / "scripts" / "run_eval.py"
    spec = importlib.util.spec_from_file_location("run_eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_strip_md_removes_emphasis_marks():
    module = load_run_eval()
    assert module._strip_md("应选**“自营者”**") == "应选“自营者”"
    assert module._strip_md("**加粗**与*斜体*与`代码`与__下划线__") == "加粗与斜体与代码与下划线"
    assert module._strip_md("无标记文本") == "无标记文本"


def test_norm_for_match_flattens_quote_and_md_styles():
    """全角 / 直引号 / 「」 / 加粗包裹都应归一化成同一种可比对形式。"""
    module = load_run_eval()
    for variant in ("不选“雇员”", '不选"雇员"', "不选「雇员」", "不选**“雇员”**", "不选'雇员'"):
        assert module._norm_for_match(variant) == "不选雇员"


def _load_eval_item(eval_id: str) -> dict:
    path = Path(__file__).resolve().parents[2] / "knowledge-base" / "qa" / "eval_set.json"
    items = json.loads(path.read_text(encoding="utf-8-sig"))
    return next(x for x in items if x.get("id") == eval_id)


def test_eval_111_accepts_real_verb_and_quote_variants():
    """2026-09-20 生产环境实测的三种真实写法都应通过 eval-111（此前逐条枚举会假失败）。"""
    module = load_run_eval()
    item = _load_eval_item("eval-111")
    answers = [
        "灵活就业人员的就业身份（F21）应选**“自营者”**，不选'雇员'，按填报规范登记。",
        "灵活就业人员一般**不能**选“雇员”，应选“自营者”；确有雇工才选“雇主”。",
        "灵活就业人员就业身份统一填「自营者」，不可以填「雇员」，也不要改填其它选项。",
    ]
    for answer in answers:
        result = module.evaluate_item(item, {"mode": "rag", "answer": answer, "sources": []})
        assert result["passed"] is True, (answer, result["checks"])


def _bold_style_item():
    """eval-112 的断言形状：硬指标要求「选/填 自营者」。"""
    return {
        "id": "eval-112",
        "type": "in_kb",
        "expected_keywords": ["灵活就业", "就业身份", "自营者"],
        "must_contain_any": ["就业身份选“自营者”", "选“自营者”"],
        "should_not_contain": ["F20（就业身份）", "F21选'雇员'"],
    }


def test_must_contain_any_matches_bold_wrapped_phrase():
    """模型输出 应选**“自营者”** 时，去标记后应命中硬指标（修复前是假 FAIL）。"""
    module = load_run_eval()
    answer = "灵活就业人员的就业身份应选**“自营者”**，按填报规范登记，必要时咨询业务负责人确认。"
    result = module.evaluate_item(
        _bold_style_item(), {"mode": "rag", "answer": answer, "sources": []}
    )
    assert result["passed"] is True, result["checks"]
    assert any("must_contain_any 命中" in c["reason"] for c in result["checks"])


def test_strip_md_does_not_mask_wrong_conclusion():
    """放松的只是正面匹配；错误结论仍被 should_not_contain 拦下。"""
    module = load_run_eval()
    answer = "灵活就业人员的就业身份应选**“自营者”**，若实际受雇于平台则按 F21选'雇员' 处理，详见说明。"
    result = module.evaluate_item(
        _bold_style_item(), {"mode": "rag", "answer": answer, "sources": []}
    )
    assert result["passed"] is False, result["checks"]
    assert any("含禁词" in c["reason"] for c in result["checks"])
