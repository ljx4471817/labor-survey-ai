"""POST /api/chat 端点。"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from loguru import logger

from app.core.config import settings
from app.models.schemas import ChatRequest, ChatResponse, SourceItem
from app.rag.llm import chat
from app.rag.prompts import SYSTEM_PROMPT, USER_TEMPLATE, format_kb_results
from app.rag.retriever import is_ambiguous, is_in_scope, retrieve

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


@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest) -> ChatResponse:
    msg = req.message.strip()
    if not msg:
        raise HTTPException(400, "message 不能为空")

    # 第一层：越界判断
    if not is_in_scope(msg):
        return ChatResponse(answer=OUT_OF_SCOPE_REPLY, mode="out_of_scope")

    # 第二层：模糊判断
    if is_ambiguous(msg):
        return ChatResponse(answer=AMBIGUOUS_REPLY, mode="ambiguous")

    # 第三层：检索
    try:
        sources = retrieve(msg, top_k=req.top_k)
    except Exception as e:
        logger.exception("检索失败")
        raise HTTPException(500, f"检索失败: {e}")

    top_score = sources[0]["score"] if sources else 0.0

    # 第五层：LLM 生成
    kb_block = format_kb_results(sources)
    try:
        answer = chat(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": USER_TEMPLATE.format(
                    kb_results=kb_block, user_message=msg
                )},
            ]
        )
    except Exception as e:
        logger.exception("LLM 调用失败")
        raise HTTPException(500, f"LLM 调用失败: {e}")

    # 第六层：检测 LLM 是否触发了"未找到"模板。
    # 必须避免误判：LLM 用"不在 X 范围内"陈述事实时（如"流动人口不在派出所登记范围
    # 内"），不应被识别为拒答。所以严格匹配 prompt 中的标准措辞。
    refusal_patterns = (
        r"抱歉.*?知识库.*?(未|没有).*?(找到|收录|涵盖|涉及)",
        r"知识库中未(找到|收录|涉及|涵盖)",
        r"知识库未(找到|收录|涉及|涵盖)",
        r"未(找到|收录).*?相关(内容|信息|答案)",
    )
    if any(re.search(p, answer) for p in refusal_patterns):
        return ChatResponse(
            answer=answer,
            sources=[
                SourceItem(
                    qa_id=s["id"],
                    question=s["metadata"].get("question", ""),
                    source=s["metadata"].get("source", ""),
                    category=s["metadata"].get("category", ""),
                    score=s["score"],
                )
                for s in sources
            ],
            mode="out_of_kb",
            retrieval_score=top_score,
        )

    return ChatResponse(
        answer=answer,
        sources=[
            SourceItem(
                qa_id=s["id"],
                question=s["metadata"].get("question", ""),
                source=s["metadata"].get("source", ""),
                category=s["metadata"].get("category", ""),
                score=s["score"],
            )
            for s in sources
        ],
        mode="rag",
        retrieval_score=top_score,
    )
