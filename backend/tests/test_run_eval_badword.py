# -*- coding: utf-8 -*-
"""run_eval 的禁词否定判定（2026-09-21 eval-103 误报回归）。"""
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


def test_prohibitive_words_are_not_flagged_as_violations():
    """「严禁/不准/不许/切忌/杜绝/拒绝」都是否定语境，不该被算作含禁词。"""
    module = load_run_eval()
    for negation in ("严禁", "不准", "不许", "切忌", "杜绝", "拒绝"):
        answer = f"按项目结算的按上月实际结算报酬填报。{negation}取中间值或折中估算。"
        assert module._bad_word_hit(answer, "取中间值") is False, negation
        assert module._bad_word_hit(answer, "估算") is False, negation


def test_positive_recommendation_in_another_sentence_is_still_flagged():
    """放松只按句生效：另一句正面推荐仍旧要抓。"""
    module = load_run_eval()
    answer = "严禁取中间值。住户坚持时，可以取中间值填报。"
    assert module._bad_word_hit(answer, "取中间值") is True


def test_eval_103_real_answer_is_not_a_violation():
    """真实失败样本回归：这版答案语义正确（明确禁止），修复前被判含禁词。"""
    module = load_run_eval()
    path = Path(__file__).resolve().parents[2] / "knowledge-base" / "qa" / "eval_set.json"
    item = next(x for x in json.loads(path.read_text(encoding="utf-8-sig")) if x["id"] == "eval-103")
    answer = (
        "算就业，收入填报**先问发放周期再定原则**：\n\n"
        "**按月固定发工资**的：直接按实际应得税前收入填报，浮动多少填多少，无需折算。\n\n"
        "**按项目/按年/按件结算**的：按上月实际结算到的报酬填报，或折算月平均劳动报酬。\n\n"
        "**严禁取中间值或折中估算**。追问话术：“您这个活是按月发还是按项目结？大概多久结一次？”"
        "金额填整数，精确到十位即可。\n\n"
        "适用要点：装修工、灵活就业、F27；适用场景：装修工、建筑工、零工；适用指标：F27。"
    )
    result = module.evaluate_item(item, {"mode": "rag", "answer": answer, "sources": []})
    assert result["passed"] is True, result["checks"]
