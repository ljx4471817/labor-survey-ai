# 贵州知识包影子对比报告（2026-10-19）

## 结论

- 现有 flat 知识库基线：**114/114 通过**。
- `nat-2026-01 + gz-2026-01` 知识包组合：**114/114 通过**。
- 两组均达到发布门禁；知识包组合未引入质量回归。
- 知识包组合总耗时 **633.899s**，flat 基线总耗时 **738.226s**；本轮差异主要受 LLM 响应波动影响，不作为稳定性能结论。
- 有 95 条回答文本不同，但 Eval 断言均通过；LLM 输出存在正常表达差异。
- `eval-082` 出现 `rag -> out_of_kb` 模式差异，但该用例允许两种模式且判定通过。
- 8 条 RAG 日志记录到省级 `rule_key` 覆盖，实际命中的都是贵州包条目。

## Release 信息

| 知识包 | Release | 构建 | Eval | 发布状态 |
|---|---|---|---|---|
| national-base | nat-2026-01 | success | passed | active |
| guizhou | gz-2026-01 | success | passed | active |
| 省份绑定 | 520000 -> guizhou | - | - | active |

## 日志验证

- 114 条影子 Eval 日志全部写入独立 shadow `query_log`。
- 99 条正常 RAG 日志记录为 `pack_mode=packs`。
- 15 条短路路径（out_of_scope / ambiguous / trap）包字段为空，符合设计。
- 8 条日志 `override_used=1`，覆盖条目来自 `guizhou:gz-2026-01`。
- 全部 RAG 日志使用 `national_release_id=nat-2026-01`、`provincial_release_id=gz-2026-01`。

## 产物

- Flat 基线：`reports/eval-flat-baseline-20261019.json`
- 知识包影子：`reports/eval-pack-shadow-20261019.json`
- 结构化对比：`reports/kb-pack-shadow-comparison-20261019.json`
