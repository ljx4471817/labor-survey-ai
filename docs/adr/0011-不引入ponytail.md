# ADR 0011 — 不引入 ponytail

## 状态
已确认（2026-07-30）· 待领导/同事 review

## 背景
2026-07-30 调研 [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail)（91.9k Stars，MIT，JavaScript）：一个 AI 编程 Agent 的行为指令包，主张"能不写就不写、能用原生 API 就不用库"，自报在 FastAPI + React 仓库上平均 -54% LOC / -22% token / -20% 成本 / -27% 时间，安全性 100%。提供 `/ponytail-review`、`/ponytail-audit`、`/ponytail-debt` 等命令，兼容 20+ Agent（Claude Code / Codex / Cursor / Windsurf / Cline / Devin CLI 等）。

需要回答：要不要把这个项目作为开发期 Agent 引入到本项目？

## 评估结论
**不引入** ponytail 作为本项目的全局 Agent 行为插件。

### 理由

1. **核心哲学与项目现行规范相反**  
   ponytail 的核心指令是"合并、能不写就不写"。本项目 `AGENTS.md` 明确要求：
   - 路由 / 业务逻辑 / 检索算法 / 数据模型 / 持久化 / 基础设施 **分层强制**
   - **不改旧文件（除非 bug fix），新增功能新建文件**
   - **纯函数必须有单测**，80% 覆盖门禁
   
   两个 Agent 指令同时生效会让模型在"该拆 vs 该合并"之间反复，**增加 reviewer 负担而非减少**。

2. **代码量不是本项目当前痛点**  
   迭代 3 已完成架构重构 Phase 1-9，主动把代码按业务域拆细（`backend/app/api/` 13 个路由文件、`services/`、`persistence/`、`infra/`、`analytics/` 分层）。40 个单测全绿，RAG eval 102/102 回归通过。  
   当前真实痛点是 **KB 质量、DeepSeek 提额、采购落地**，都和"代码多少"无关。

3. **token 节省对本项目经济模型影响微弱**  
   主要 LLM 成本来自生产端 **DeepSeek 对话** + **DashScope Embedding**，不是开发 Agent 的输入。开发 Agent 多加载一组 ponytail 规则带来的 token 节省，相对生产侧成本可忽略。

4. **跨 Agent 兼容是优势但非必需**  
   ponytail 的卖点之一是兼容 20+ Agent。本项目目前只用 **Codex**，项目级 `AGENTS.md` 已能精确控制行为，无需借助通用插件补齐。

## 决策
- **不把 ponytail 装为全局 Codex 插件**
- **不写 ponytail 二次封装为项目级 Skill**（除非未来出现真实痛点，详见下方启用条件）
- **本评估结论作为决策留痕**，未来若有人提议引入，先看本 ADR

## 可选使用场景（保留）
若未来出现真实场景，下列使用方式不会被本 ADR 阻止：

| 场景 | 怎么用 | 价值 |
|------|--------|------|
| **代码审查阶段** | 单独跑 `/ponytail-review` 看 diff 的过度设计风险 | 给 code-reviewer 一个对照视角 |
| **新人 onboarding** | 作为"少即是多"的工程文化补充阅读 | 需明确告知项目分层规则优先 |

## 启用条件（何时重新评估）
满足**全部**条件时，重新评估本 ADR 并考虑引入 ponytail 作为项目级 Skill（带项目级 override 规则）：
1. 项目代码量成为实际痛点（例如 LOC 在某次重构后激增 50%+）
2. 出现至少 3 次 reviewer 标注"代码过度设计"的合并
3. 当前 `AGENTS.md` 的分层规则**本身**被验证为不够用

否则继续保留本 ADR 决策。

## 影响
- 无：未引入任何插件、未修改任何代码、未影响生产环境
- 仅在 `docs/adr/` 增加一份决策记录
- 若同事提议引入 ponytail，先看本 ADR 并附三条理由（见上）

## 关联
- `AGENTS.md`（项目级规则集，分层强制）
- `docs/adr/0009-voice-disabled.md`（同类决策：停用讯飞语音，理由一致——重复造轮子+运维成本）
- `docs/adr/` 历史索引
