"""反馈提交校验与唯一性测试。"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.api.feedback import FEEDBACK_PATH, submit_feedback
from app.models.schemas import FeedbackRequest


def _request(
    *,
    rating: str = "down",
    mode: str = "rag",
    request_id: str = "req000000001",
    corrected_answer: str = "按照发放周期填写该字段",
    evidence: str = "制度指标 F27 明确了口径",
    comment: str = "",
) -> FeedbackRequest:
    return FeedbackRequest(
        question="F27 怎么填？",
        answer="原答案",
        mode=mode,
        rating=rating,
        comment=comment,
        corrected_answer=corrected_answer,
        evidence=evidence,
        sources=[{"qa_id": "27"}],
        request_id=request_id,
    )


def test_negative_feedback_requires_long_answer_and_evidence():
    with pytest.raises(ValueError, match="正确答案"):
        _request(corrected_answer="太短")
    with pytest.raises(ValueError, match="依据"):
        _request(evidence="太短")


def test_positive_feedback_ignores_correction_fields_and_comment():
    req = _request(rating="up", corrected_answer="", evidence="", comment="不需要")
    assert req.corrected_answer == ""
    assert req.evidence == ""
    assert req.comment == ""


def test_non_rag_feedback_rejected():
    with pytest.raises(ValueError, match="RAG"):
        _request(mode="out_of_kb")


def test_missing_request_id_rejected():
    with pytest.raises(ValueError, match="request_id"):
        _request(request_id=" ")


def test_duplicate_request_rejected_and_only_one_record_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    path = tmp_path / "feedback.jsonl"
    path.touch()
    monkeypatch.setattr("app.api.feedback.FEEDBACK_PATH", path)
    monkeypatch.setattr(
        "app.api.feedback.get_current_user",
        lambda phone: {"name": "测试用户", "province": "贵州省"},
    )

    first = submit_feedback(_request(), phone="13985000001")
    with pytest.raises(HTTPException) as exc_info:
        submit_feedback(_request(), phone="13985000001")

    assert first.ok is True
    assert exc_info.value.status_code == 409
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["corrected_answer"] == "按照发放周期填写该字段"
    assert record["evidence"] == "制度指标 F27 明确了口径"
    assert record["comment"] == ""


def test_feedback_path_constant_points_to_runtime_file():
    assert FEEDBACK_PATH.name == "feedback.jsonl"
