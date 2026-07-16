"""POST /api/chat 端点。"""
from __future__ import annotations

import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from app.infra.auth import get_current_user, require_user
from app.models.schemas import ChatMessage, ChatRequest, ChatResponse, SourceItem
from app.persistence.query_log import insert as insert_query_log
from app.rag.llm import chat as llm_chat
from app.rag.prompts import SYSTEM_PROMPT, USER_TEMPLATE, format_kb_results
from app.rag.pure import is_ambiguous, is_in_scope, merge_query_with_history
from app.rag.retriever import retrieve

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


def _log_query(
    phone: str,
    msg: str,
    mode: str,
    score: float | None = None,
    *,
    request_id: str | None = None,
    hits: int | None = None,
    latency_ms: int | None = None,
) -> None:
    user = get_current_user(phone)
    if not user:
        return
    try:
        insert_query_log({
            "phone": phone,
            "name": user["name"],
            "province": user["province"],
            "city": user["city"],
            "county": user["county"],
            "township": user.get("township", ""),
            "community": user["community"],
            "query": msg,
            "mode": mode,
            "retrieval_score": score,
            "request_id": request_id,
            "hits": hits,
            "latency_ms": latency_ms,
        })
    except Exception:
        logger.exception("query_log 写入失败")


def _build_history_context(history: list[ChatMessage]) -> str:
    """把历史消息拼成 [role] content 文本块。"""
    if not history:
        return "（无）"
    return "\n".join(f"[{m.role}] {m.content}" for m in history)


def _detect_refusal(answer: str) -> bool:
    """检测 LLM 是否触发了'未找到'模板。"""
    return any(re.search(p, answer) for p in REFUSAL_PATTERNS)


def _handle_out_of_scope(
    msg: str, phone: str, request_id: str, t_start: float
) -> ChatResponse:
    latency_ms = int((time.perf_counter() - t_start) * 1000)
    _log_query(phone, msg, "out_of_scope", request_id=request_id, latency_ms=latency_ms)
    return ChatResponse(answer=OUT_OF_SCOPE_REPLY, mode="out_of_scope", request_id=request_id)


def _handle_ambiguous(
    msg: str, phone: str, request_id: str, t_start: float
) -> ChatResponse:
    latency_ms = int((time.perf_counter() - t_start) * 1000)
    _log_query(phone, msg, "ambiguous", request_id=request_id, latency_ms=latency_ms)
    return ChatResponse(answer=AMBIGUOUS_REPLY, mode="ambiguous", request_id=request_id)


@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(
    req: ChatRequest,
    phone: str = Depends(require_user),
) -> ChatResponse:
    msg = req.message.strip()
    if not msg:
        raise HTTPException(400, "message 不能为空")

    history = req.history[-8:]  # 上限 4 轮（每轮 user + assistant 各 1 条）
    request_id = uuid.uuid4().hex[:12]
    t_start = time.perf_counter()

    # 第一层：越界判断（用单条消息判断，history 不参与越界检测）
    if not is_in_scope(msg):
        return _handle_out_of_scope(msg, phone, request_id, t_start)

    # 第二层：模糊判断 —— 多轮场景：history 非空时跳过（上下文已能消歧）
    if not history and is_ambiguous(msg):
        return _handle_ambiguous(msg, phone, request_id, t_start)

    # 检索（merged_query 让历史 user 消息也参与 KB 召回）
    merged_query = merge_query_with_history(msg, history)
    try:
        sources = retrieve(merged_query, top_k=req.top_k)
    except Exception as e:
        logger.exception("检索失败")
        raise HTTPException(500, f"检索失败: {e}")

    top_score = sources[0]["score"] if sources else 0.0
    hits = len(sources)

    # LLM 生成
    # log top-1 for observability
    top1_id = sources[0]["id"] if sources else "none"
    top1_score = sources[0]["score"] if sources else 0.0
    logger.info(f"chat: q={msg[:30]!r} top1={top1_id} score={top1_score:.3f} n_sources={len(sources)}")
    kb_block = format_kb_results(sources)
    history_context = _build_history_context(history)
    try:
        answer = llm_chat(
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

    # 检测 LLM 是否触发了"未找到"模板，决定走 rag 还是 out_of_kb
    mode = "out_of_kb" if _detect_refusal(answer) else "rag"
    latency_ms = int((time.perf_counter() - t_start) * 1000)
    _log_query(
        phone, msg, mode, top_score,
        request_id=request_id, hits=hits, latency_ms=latency_ms,
    )

    return ChatResponse(
        answer=answer,
        sources=_to_source_items(sources),
        mode=mode,
        retrieval_score=top_score,
        request_id=request_id,
    )