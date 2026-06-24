"""POST /api/chat 端点。"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.config import settings
from app.models.schemas import ChatRequest, ChatResponse, SourceItem
from app.rag.llm import chat
from app.rag.prompts import SYSTEM_PROMPT, USER_TEMPLATE, format_kb_results
from app.rag.retriever import is_ambiguous, is_in_scope, merge_query_with_history, retrieve

router = APIRouter()

OUT_OF_SCOPE_REPLY = (
    "该问题不在调查员 AI 助手服务范围内。本助手仅提供劳动力调查填报指导。"
    "（行职业编码由办公室人员负责，居民个人信息不在处理范围内。）"
)

AMBIGUOUS_REPLY = (
    "您的问题需要更具体。可以补充：\n"
    "1. 是哪一项指标？（如 F10 就业状态、F27 劳动报酬）\n"
    "2. 涉及哪类人群？（如退休人员、学生、外出务工）\n"
    "3. 大概的场景是什么？"
)

OUT_OF_KB_REPLY = (
    "抱歉，知识库中未找到相关内容。建议：\n"
    "1. 咨询业务主管\n"
    "2. 参考最新《劳动力调查制度》\n"
    "3. 换个更具体的问题再问"
)

# LLM 拒答识别：与 retriever.py 配合决定走 rag 还是 out_of_kb。
# 必须避免误判"不在 X 范围内"这类事实陈述（如流动人口登记）。
REFUSAL_PATTERNS = (
    r"抱歉.*?知识库.*?(未|没有).*?(找到|收录|涵盖|涉及)",
    r"知识库中未(找到|收录|涉及|涵盖)",
    r"知识库未(找到|收录|涉及|涵盖)",
    r"未(找到|收录).*?相关(内容|信息|答案)",
)


def _to_source_items(sources: list[dict]) -> list[SourceItem]:
    return [
        SourceItem(
            qa_id=s["id"],
            question=s["metadata"].get("question", ""),
            source=s["metadata"].get("source", ""),
            category=s["metadata"].get("category", ""),
            score=s["score"],
        )
        for s in sources
    ]


@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest) -> ChatResponse:
    msg = req.message.strip()
    if not msg:
        raise HTTPException(400, "message 不能为空")

    history = req.history[-8:]  # 上限 4 轮（每轮 user + assistant 各 1 条）

    # 第一层：越界判断（用单条消息判断，history 不参与越界检测）
    if not is_in_scope(msg):
        return ChatResponse(answer=OUT_OF_SCOPE_REPLY, mode="out_of_scope")

    # 第二层：模糊判断 —— 多轮场景：history 非空时跳过（上下文已能消歧）
    if not history and is_ambiguous(msg):
        return ChatResponse(answer=AMBIGUOUS_REPLY, mode="ambiguous")

    # 检索（merged_query 让历史 user 消息也参与 KB 召回）
    merged_query = merge_query_with_history(msg, history)
    try:
        sources = retrieve(merged_query, top_k=req.top_k)
    except Exception as e:
        logger.exception("检索失败")
        raise HTTPException(500, f"检索失败: {e}")

    top_score = sources[0]["score"] if sources else 0.0

    # 第五层：LLM 生成
    kb_block = format_kb_results(sources)
    history_context = (
        "\n".join(f"[{m.role}] {m.content}" for m in history)
        if history else "（无）"
    )
    try:
        answer = chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(
                    kb_results=kb_block,
                    history_context=history_context,
                    user_message=msg,
                )},
            ]
        )
    except Exception as e:
        logger.exception("LLM 调用失败")
        raise HTTPException(500, f"LLM 调用失败: {e}")

    # 第六层：检测 LLM 是否触发了"未找到"模板，决定走 rag 还是 out_of_kb
    refused = any(re.search(p, answer) for p in REFUSAL_PATTERNS)
    return ChatResponse(
        answer=answer,
        sources=_to_source_items(sources),
        mode="out_of_kb" if refused else "rag",
        retrieval_score=top_score,
    )
