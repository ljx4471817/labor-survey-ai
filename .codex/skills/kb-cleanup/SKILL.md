---
name: kb-cleanup
description: >
  单 query 检索结果优化（奥卡姆版）。当用户报告某个 query 答得不好 / 答得不一致 /
  答非所问 / 检索结果不对时触发；也响应用户说 "kb-cleanup"、"/kb-cleanup"、
  "这个 query 答得不好"、"答非所问"、"检索结果优化"、"KB 修复"。
  边界：单 query / 一类 query 的单点优化，不做批量、不做架构决策、不新增代码层。
  完整流程见 .codex/skills/kb-optimize/SKILL.md。
---

# kb-cleanup — 单 query 检索结果优化（奥卡姆版）

> 触发：用户说「这个 query 答得不好 / 答得不一致 / 答非所问」或主动调用 `/kb-cleanup`。
> 边界：**单 query / 一类 query 的单点优化**。不做批量、不做架构决策、不做新增代码层。
> 原则：奥卡姆剃刀 —— 能改 KB 就不改 prompt，能改 prompt 就不改 retriever，能不动代码就不动。

## 执行流程

1. **复现与诊断** —— 跑检索日志，定位问题 query 命中了什么、冲突在哪
2. **基线留存** —— 跑改前 DeepSeek 输出，留作对比基线
3. **给候选方案** —— KB / prompt / eval 三层各给候选
4. **风险偏好选择** —— 最简 / 最稳 / 自定义
5. **执行改动**
6. **改前 / 改后对比验证**
7. **输出修复报告**

## 完整流程

读取 `.codex/skills/kb-optimize/SKILL.md`，按其中详细流程执行（含基线留存 / 风险偏好 / 置信度 / 超奥卡姆回退提示）。