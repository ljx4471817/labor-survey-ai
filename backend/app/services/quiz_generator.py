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

from app.core.constants import (
    QUIZ_EXPLANATION_MAX_LEN,
    QUIZ_KB_MATCH_THRESHOLD,
    QUIZ_MAX_QUESTIONS,
    QUIZ_OPTION_MAX_LEN,
    QUIZ_QUESTION_MAX_LEN,
)
from app.services.quiz_extract import _llm_json, parse_llm_json

PROMPT2_SYSTEM = "你是劳动力调查出题专家。根据要点生成 4 选 1 选择题，只输出 JSON。"

PROMPT2_USER = """根据以下要点生成 4 选 1 选择题，面向阅读能力有限的调查员，务必简短易懂。

## 输入
要点：{keypoint_content}
常见错误：{common_error}
来源段落：{source_quote}

## 输出格式
返回 JSON：
{{"question": "题干（1句话情境，≤45字）",
 "options": {{"A": "选项A（≤15字）", "B": "选项B（≤15字）", "C": "选项C（≤15字）", "D": "选项D（≤15字）"}},
 "answer": "正确答案（A/B/C/D）",
 "explanation": "解析（结论+依据，≤80字）"}}

## 规则
1. 题干：1 句话情境化，只保留 1 个关键条件；用「调查员」泛指，不出现具体人名和多余铺垫；只考 1 个考点；≤45 字
2. 选项：短语化，每选项只表达 1 个判断；共同部分移入题干，选项只留差异点；每选项 ≤15 字
3. 干扰项：必须具体可信（来自常见错误），不能因简短而写成明显错误的空话
4. 术语：单题只出现 1 个指标编号；专业词首次出现用大白话括注（如「住本户时间（在这户住了多久）」）
5. 问法：只准正向提问（「以下做法正确的是？」），禁止双重否定或嵌套逻辑句式
6. 解析：先给结论（「选 X：…」），再引来源段落关键半句作依据；不逐个选项解释为什么错；≤80 字
7. 答案唯一且确定
8. 只输出 JSON，不要 markdown 代码块，不要任何解释"""


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


def length_check(question: str, options: dict, explanation: str) -> list[str]:
    """检查题干/选项/解析是否超长，返回超限字段列表（空 = 全部达标）。

    阈值：题干 ≤45 字、每选项 ≤15 字、解析 ≤80 字（按字符数计）。
    软校验：超长只标记（over_limit），不拒绝题目，避免触发 repair 重试导致生成失败。
    """
    over: list[str] = []
    if len(question) > QUIZ_QUESTION_MAX_LEN:
        over.append("question")
    for key in ("A", "B", "C", "D"):
        if len((options.get(key) or "").strip()) > QUIZ_OPTION_MAX_LEN:
            over.append("option_" + key)
    if len(explanation) > QUIZ_EXPLANATION_MAX_LEN:
        over.append("explanation")
    return over


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
        "over_limit": length_check(question, norm_options, explanation),
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
    count: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """按要点生成题目；count 指定生成题数（每要点最多 1 题，实际取 min(count, 要点数)）。

    count=None 表示全部要点；单个要点失败（连续非法 JSON）→ 跳过并记录 errors。
    返回 (questions, errors)，顺序与 keypoints 一致。
    """
    from concurrent.futures import ThreadPoolExecutor

    take = keypoints[: max(0, min(count, len(keypoints)))] if count is not None else keypoints
    questions: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_generate_one, kp, llm_chat_fn) for kp in take]
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
