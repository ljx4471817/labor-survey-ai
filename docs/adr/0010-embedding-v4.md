# ADR 0010：DashScope text-embedding-v4

## 状态

已确认（2026-07-13）

## 背景

项目已实际采用 DashScope Embedding API，知识库同时包含结构化 QA 与制度原文
chunk。DashScope 发布 `text-embedding-v4` 后，需要确认向量维度兼容性，并避免
全量重建某一数据源时误删共享 Chroma collection 中的另一数据源。

## 决策

- 默认 Embedding 模型升级为 `text-embedding-v4`。
- v4 探针返回 1024 维，与现有 Chroma collection 兼容，不迁移 collection schema。
- 当前向量库和 BM25 均包含 409 条：354 条 QA + 55 条制度 chunk。
- QA 和 chunk 继续共用 `labor_survey_qa` collection，通过 `doc_type` 区分。
- `build_kb.py --full` 只删除并重建 QA，必须保留 chunk。
- `build_chunks.py --full` 只删除并重建 chunk，必须保留 QA。
- QA 元数据显式写入 `doc_type=qa`；读取时兼容早期仅含 `qa_id` 的记录。

## 操作约束

首次构建或模型升级时，两个数据源都必须用同一模型重建：

```bash
python scripts/build_kb.py --full
python scripts/build_chunks.py --input "knowledge-base/raw/markdown/劳动力调查制度（2026年定期报表）-定稿.md" --full
python scripts/build_bm25.py --full
python scripts/run_eval.py --url http://127.0.0.1:8001 --phone 13985000001
```

验收条件：

1. `/health` 返回 `chroma_count=409`。
2. BM25 索引为 409 条（354 QA + 55 chunk）。
3. 后端单元测试 40 项全绿。
4. RAG 全量评测 102/102 通过。

## 影响

- `.env.example`、后端运行时配置和构建脚本的默认模型统一为 v4。
- `text-embedding-v3` 仅保留在历史方案、旧预算和 ADR 0003 的历史上下文中。
- 新增回归测试，锁定“QA 全量重建不得删除 chunk”。

## 关联

- `docs/adr/0002-向量库选型.md`
- `docs/adr/0003-embedding-部署方式.md`
- `scripts/build_kb.py`
- `scripts/build_chunks.py`
- `backend/tests/test_build_kb.py`
