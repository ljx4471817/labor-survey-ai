# ADR 0019 — RAG grounding 锚点

> 状态：已采纳（2026-08-31）
> 触发：LLM 生成的 RAG 答案偶尔遗漏 KB 条目中的关键指标编号、场景词或 metadata 关键词，导致用户无法从答案中识别适用范围。
> 决策者：开发者

## 背景

RAG 管道：检索 top-K → 拼 prompt → LLM 生成 → 返回。LLM 偶尔在改写答案时丢弃 KB 条目中的指标编号（如 F16）、场景限定词（如装修工/零工）或 metadata 关键词。答案是流畅了，但用户无法确认"这条回答适用于我的情况"。

## 决策

新增 `backend/app/rag/grounding.py` 的 `ensure_kb_anchors(answer, top_source)`，在 LLM 输出后、返回前执行：

1. 仅当 top-1 source 是 QA 条目（`doc_type == "qa"`）时处理。
2. 扫描 KB 原文中的 F\d{2} 指标编号、场景词、逗号分隔 keywords，若 LLM 答案中遗漏则追加为锚点段落。
3. 锚点格式：`适用要点：...` / `适用场景：...` / `适用指标：...`，以"；"拼接追加在答案末尾。
4. 所有遗漏均不存在时不追加，答案不变。

## 影响

- 答案长度略增，但可追溯性提升
- 仅改写输出层，不影响检索、prompt 或 eval 评分逻辑
- eval 104/104 回归通过

## 关联

- 检索治理：ADR 0013（规则冲突裁决）
- 实现：`backend/app/rag/grounding.py` + `backend/app/api/chat.py` 调用点
