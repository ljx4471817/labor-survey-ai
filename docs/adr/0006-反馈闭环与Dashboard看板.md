# ADR 0006：反馈闭环与 Dashboard 看板

## 状态

**已采用（2026-06-24）**

## 背景

调查员可对 AI 回答打 👍/👎 反馈，写入 `backend/data/feedback.jsonl`。但：

- 无管理界面浏览 / 处理积压反馈
- 哪些 "👎" 应该回流到 KB 改造？人工翻 jsonl 不现实
- 反馈数据本身没有 "已处理" 状态，无法判断闭环效果

## 决策

**新增 Dashboard 看板 + admin API + resolved event log，三件一起做。**

### 数据流

```
[调查员] 👍/👎 反馈
  ↓
[POST /api/feedback] → feedback.jsonl（append-only，原始事件）

[管理员] 打开 /dashboard（白名单鉴权）
  ↓
[GET /api/admin/stats] → 聚合统计（按 category / 时间 / 👍率）
  ↓
[管理员] 选某条 👎，标注处理方案（如 "KB 应补 corner case"）
  ↓
[POST /api/admin/resolve {feedback_id, action, kb_patch}] → feedback_resolved.jsonl（append-only 解决事件）
  ↓
[GET /api/admin/stats] 同步反映 "已解决 / 待解决" 比例
```

### 关键设计

| 项 | 设计 | 理由 |
|----|------|------|
| **两条 jsonl 分离** | `feedback.jsonl`（原始）+ `feedback_resolved.jsonl`（解决事件）| 原始数据不可变；解决事件可追溯 |
| **首页不暴露入口** | `/dashboard` 需手输 URL | 内部使用，不放主导航 |
| **聚合在 admin.py** | `GET /stats` 一次返回所有看板数据 | 减少前端轮询 |
| **resolve 端点** | `POST /resolve` 接受 `action` 描述 + 可选 `kb_patch` 备注 | 解决方案可结构化 |
| **鉴权** | 复用 ADR 0005 的 `Depends(require_user)` | 看板也走白名单门禁 |

### 前端：`backend/static/dashboard.html`

- 顶部统计卡：总反馈数 / 👍率 / 待解决数 / 24h 新增
- "候选 KB 改进" 列表：所有 👎 按 category 聚合，便于发现 KB 缺口
- 时间分布：最近 7/30 天反馈量柱状图（纯 CSS，无图表库）
- 明细分页：每页 20 条，支持按 status（待解决/已解决）筛选
- 共享 `common.js` 的 `$()` + `escapeHtml()` + `authHeader()` + `handle401()`

### 后端：`backend/app/api/admin.py`

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/admin/stats` | GET | 聚合统计 + 分页明细（query: `status`, `page`, `page_size`） |
| `/api/admin/resolve` | POST | 标记某条 feedback 已解决，写 `feedback_resolved.jsonl` |

`ResolveRequest` schema：`feedback_id: int, action: str (≤100 字), kb_patch: str | None`

## 考虑过的备选

| 方案 | 否决理由 |
|------|----------|
| 直接改 feedback.jsonl 加 status 字段 | 破坏 append-only 语义；jsonl 不是数据库 |
| 引入 SQLite 存 resolved 状态 | 单人项目，单文件 jsonl 已够；引 DB 需迁移 + 备份策略 |
| 用 Chroma / 现有向量库存 | 杀鸡用牛刀；解决事件无检索需求 |
| 公开看板链接 | 仅内部用，走白名单门禁（ADR 0005） |
| WebSocket 实时推送反馈 | 当前 1 人开发，无需实时；轮询足够 |

## 影响

- **运营**：解决事件可结构化为"KB 改造任务清单"，下次 KB 维护时按清单改 `faq.json`
- **可观测性**：通过 👍率趋势可观察 KB 质量是否随迭代提升
- **数据**：feedback_resolved.jsonl 追加写入，与 feedback.jsonl 同目录，加 `.gitignore` 不进 git（与 whitelist.json 同样处置）
- **回归**：`dashboard.html` 内 fetch 全部走 ADR 0005 鉴权；登出态进不去

## 关联

- 鉴权：ADR 0005（共用 `Depends(require_user)`）
- KB 改造闭环：docs/04-知识库规范.md 第四节「录入流程」+ corner case 处理流程
- 实现：`backend/app/api/admin.py` + `backend/static/dashboard.html`