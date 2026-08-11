# -*- coding: utf-8 -*-
"""quiz_generator 纯函数与编排测试（PRD v3 10.1）。"""
from __future__ import annotations

import pytest

from app.core.constants import QUIZ_KB_MATCH_THRESHOLD, QUIZ_MAX_QUESTIONS
from app.services import quiz_generator as qg


def _valid_qraw():
    return (
        '{"question": "家务劳动者无收入应判定为？", '
        '"options": {"A": "就业人口", "B": "失业人口", "C": "非劳动力", "D": "在职未就业"}, '
        '"answer": "C", "explanation": "根据审核要点，应判为非劳动力。"}'
    )


def test_parse_question_valid():
    q = qg.parse_question(_valid_qraw())
    assert q is not None
    assert sorted(q["options"].keys()) == ["A", "B", "C", "D"]
    assert q["answer"] == "C"


def test_parse_question_with_markdown_fence():
    q = qg.parse_question("```json\n" + _valid_qraw() + "\n```")
    assert q is not None


def test_parse_question_invalid_options():
    bad = '{"question": "q", "options": {"A": "1", "B": "2"}, "answer": "A", "explanation": "e"}'
    assert qg.parse_question(bad) is None


def test_parse_question_answer_not_in_options():
    bad = '{"question": "q", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "answer": "E", "explanation": "e"}'
    assert qg.parse_question(bad) is None


def test_generate_questions_respects_max():
    def fake_llm(messages):
        return _valid_qraw()

    keypoints = [{"content": f"要点{i}", "source_quote": "s"} for i in range(10)]
    qs, errs = qg.generate_questions(keypoints, fake_llm)
    assert len(qs) == QUIZ_MAX_QUESTIONS
    assert errs == []


def test_generate_questions_skips_failing_keypoint():
    calls = {"n": 0}

    def fake_llm(messages):
        calls["n"] += 1
        # 第一个要点：连续 3 次非法（初始 + 2 次重试）→ ValueError；之后都成功
        if calls["n"] <= 3:
            return "not json"
        return _valid_qraw()

    keypoints = [{"content": "坏要点", "source_quote": "s"}, {"content": "好要点", "source_quote": "s"}]
    qs, errs = qg.generate_questions(keypoints, fake_llm, max_questions=2)
    # 坏要点重试后仍失败 → 跳过并记录 error；好要点成功
    assert len(errs) == 1
    assert len(qs) == 1
    assert calls["n"] == 4


def test_match_kb_matched():
    def fake_search(query, top_k=3):
        return [{"id": "023", "metadata": {"doc_type": "qa", "question": "家务劳动者如何判定？"}, "score": 0.87}]

    m = qg.match_kb("家务劳动者", search_fn=fake_search)
    assert m == {"faq_id": "023", "question": "家务劳动者如何判定？", "score": 0.87}


def test_match_kb_low_score_none():
    def fake_search(query, top_k=3):
        return [{"id": "023", "metadata": {"doc_type": "qa", "question": "x"}, "score": 0.4}]

    assert qg.match_kb("家务劳动者", search_fn=fake_search) is None


def test_match_kb_ignores_chunk_docs():
    def fake_search(query, top_k=3):
        return [
            {"id": "chunk_1", "metadata": {"doc_type": "chunk"}, "score": 0.9},
            {"id": "023", "metadata": {"doc_type": "qa", "question": "q"}, "score": 0.7},
        ]

    m = qg.match_kb("家务劳动者", search_fn=fake_search)
    assert m is not None and m["faq_id"] == "023"


def test_is_expired():
    now = "2026-08-06T12:00:00+08:00"
    assert qg.is_expired("2026-08-01T00:00:00+08:00", now) is True
    assert qg.is_expired("2026-08-10T00:00:00+08:00", now) is False
    assert qg.is_expired(None, now) is False


def test_score_answers():
    assert qg.score_answers([{"correct": 1}, {"correct": 0}, {"correct": 1}]) == (2, 3)
    assert qg.score_answers([]) == (0, 0)


def test_validate_selected():
    opts = {"A": "1", "B": "2", "C": "3", "D": "4"}
    assert qg.validate_selected(opts, "C") is True
    assert qg.validate_selected(opts, "c") is True
    assert qg.validate_selected(opts, "E") is False
    assert qg.validate_selected(opts, "") is False


def test_options_to_json_roundtrip():
    import json

    opts = {"A": "1", "B": "2", "C": "3", "D": "4"}
    assert json.loads(qg.options_to_json(opts)) == opts


def test_length_check_within_limits():
    q = "大学生寒假回家暂住，应如何登记？"
    opts = {"A": "学校登记，家中不登记", "B": "家中登记，不扣寒暑假时间", "C": "家中登记，扣寒暑假时间", "D": "学校和家中都登记"}
    assert qg.length_check(q, opts, "选A。平时住校大学生寒暑假视同在校居住。") == []


def test_length_check_boundaries():
    assert qg.length_check("题" * 45, {"A": "选" * 15, "B": "b", "C": "c", "D": "d"}, "析" * 80) == []
    assert qg.length_check("题" * 46, {"A": "选" * 15, "B": "b", "C": "c", "D": "d"}, "析" * 80) == ["question"]
    assert qg.length_check("题", {"A": "选" * 16, "B": "b", "C": "c", "D": "d"}, "析" * 80) == ["option_A"]
    assert qg.length_check("题", {"A": "a", "B": "b", "C": "c", "D": "d"}, "析" * 81) == ["explanation"]


def test_length_check_multiple_fields():
    over = qg.length_check("题" * 46, {"A": "a", "B": "b", "C": "c", "D": "d"}, "析" * 81)
    assert "question" in over and "explanation" in over


def test_parse_question_includes_over_limit():
    raw = '{"question": "' + "题" * 46 + '", "options": {"A": "a", "B": "b", "C": "c", "D": "d"}, "answer": "A", "explanation": "e"}'
    q = qg.parse_question(raw)
    assert q is not None
    assert q["over_limit"] == ["question"]


def test_parse_question_over_limit_empty():
    q = qg.parse_question(_valid_qraw())
    assert q is not None
    assert q["over_limit"] == []
