# -*- coding: utf-8 -*-
"""月度测验：题目生成与判定服务。

分层（PRD v3 6.1 / 6.5 / 6.6）：
- 纯函数（可单测）：parse_question / validate_selected / is_expired / score_answers
- IO 编排（依赖注入 llm_chat_fn / search_fn，测试时 mock）：
  generate_questions / match_kb
"""
from __future__ import annotations

import json
from datetime import datetime

from app.core.constants import QUIZ_KB_MATCH_THRESHOLD, QUIZ_MAX_QUESTIONS, QUIZ_RETRY_TIMES
from app.services.quiz_extract import _llm_json, parse_llm_json

PROMPT2_SYSTEM = "你是劳动力调查出题专家。根据要点生成 4 选 1 选择题，只输出 JSON。"

PROMPT2_USER = """根据以下要点生成 4 选 1 选择题。

## 输入
要点：{keypoint_content}
常见错误：{common_error}
来源段落：{source_quote}

## 输出格式
返回 JSON：
{{"question": "题干（情境化，基于实际案例）",
 "options": {{"A": "选项A", "B": "选项B", "C": "选项C", "D": "选项D"}},
 "answer": "正确答案（A/B/C/D）",
 "explanation": "解析（引用来源段落，说明为什么对/错）"}}

## 规则
1. 题干基于实际填报场景，情境化出题
2. 干扰项来自常见错误，有迷惑性但明确错误
3. 解析必须引用来源段落原文
4. 答案唯一且确定
5. 只输出 JSON，不要 markdown 代码块，不要任何解释"""


def build_prompt2(keypoint: dict) -> list[dict]:
    """构造 Prompt2 消息序列。"""
    return [
        {"role": "system", "content": PROMPT2_SYSTEM},
        {
            "role": "user",
            "content": PROMPT2_USER.format(
                keypoint_content=keypoint.get("content", ""),
                common_error=keypoint.get("common_error", "") or "（无）",
                source_quote=keypoint.get("source_quote", "") or "（无）",
            ),
        },
    ]


def parse_question(raw: str) -> dict | None:
    """LLM 原始输出 → 合法题目 dict；非法返回 None。"""
    data = parse_llm_json(raw)
    if not isinstance(data, dict):
        return None
    question = (data.get("question") or "").strip()
    options = data.get("options")
    answer = (data.get("answer") or "").strip().upper()
    explanation = (data.get("explanation") or "").strip()
    if not question or not isinstance(options, dict) or len(options) != 4:
        return None
    norm_options = {str(k).strip().upper(): str(v) for k, v in options.items()}
    if sorted(norm_options.keys()) != ["A", "B", "C", "D"]:
        return None
    if answer not in norm_options:
        return None
    if not explanation:
        return None
    return {
        "question": question,
        "options": norm_options,
        "answer": answer,
        "explanation": explanation,
    }


def _generate_one(kp: dict, llm_chat_fn) -> tuple[dict | None, dict | None]:
    """单个要点出题；失败返回 (None, error)，不影响其它要点（网络超时/限流也按单点跳过）。"""
    try:
        q = _llm_json(build_prompt2(kp), llm_chat_fn, parse_question, "题目生成")
    except Exception as e:  # noqa: BLE001 - 单点失败不拖垮整批
        return None, {"keypoint": kp.get("content", "")[:50], "error": str(e)}
    q["source_quote"] = kp.get("source_quote", "")
    q["kb_faq_id"] = kp.get("kb_faq_id") or (kp.get("kb_ref") or {}).get("faq_id")
    q["kb_question"] = kp.get("kb_question") or (kp.get("kb_ref") or {}).get("question", "")
    return q, None


def generate_questions(
    keypoints: list[dict],
    llm_chat_fn,
    max_questions: int = QUIZ_MAX_QUESTIONS,
) -> tuple[list[dict], list[dict]]:
    """逐要点生成题目（≤max_questions），并行调用 LLM 缩短耗时。

    单个要点失败（连续非法 JSON）→ 跳过并记录 errors，不影响其它要点。
    返回 (questions, errors)，顺序与 keypoints 一致。
    """
    from concurrent.futures import ThreadPoolExecutor

    questions: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_generate_one, kp, llm_chat_fn) for kp in keypoints[:max_questions]]
        for fut in futures:
            q, err = fut.result()
            if err:
                errors.append(err)
            else:
                questions.append(q)
    return questions, errors


def match_kb(
    content: str,
    search_fn=None,
    threshold: float = QUIZ_KB_MATCH_THRESHOLD,
) -> dict | None:
    """要点 → KB 关联。

    使用向量 cosine 通道（PRD v3 6.6）：top-1 中 doc_type='qa' 且 cosine ≥ 0.6 才算命中。
    search_fn 默认取 retriever._exact_vector_search（懒加载，避免 import 拖慢纯测试）。
    """
    if search_fn is None:
        from app.rag.retriever import _exact_vector_search as search_fn
    try:
        items = search_fn(content, top_k=3)
    except Exception:
        return None
    for it in items or []:
        meta = it.get("metadata") or {}
        if meta.get("doc_type", "qa") != "qa":
            continue
        score = float(it.get("score") or 0.0)
        if score >= threshold:
            return {
                "faq_id": str(it.get("id", "")),
                "question": meta.get("question", ""),
                "score": round(score, 4),
            }
    return None


def is_expired(valid_until: str | None, now_iso: str) -> bool:
    """判断测验是否已过有效期。"""
    if not valid_until:
        return False
    try:
        return datetime.fromisoformat(now_iso) > datetime.fromisoformat(valid_until)
    except ValueError:
        return now_iso > valid_until


def score_answers(answers: list[dict]) -> tuple[int, int]:
    """返回 (答对数, 总数)。"""
    correct = sum(1 for a in answers if a.get("correct"))
    return correct, len(answers)


def validate_selected(options: dict, selected: str) -> bool:
    """校验选项键是否合法（A-D）。"""
    return bool(selected) and selected.strip().upper() in options


def options_to_json(options: dict) -> str:
    """题目 options dict → 存储用 JSON 串。"""
    return json.dumps(options, ensure_ascii=False)
