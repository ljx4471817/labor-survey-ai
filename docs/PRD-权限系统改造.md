# PRD：权限系统改造（业务层级 × 系统职能 双维度 + 分级网页维护）

- 版本：v1.0 ｜ 日期：2026-08-13 ｜ 状态：待开发 ｜ 目标读者：开发 agent
- 关联文档：ADR 0005（白名单门禁）、PRD-月度测验系统_v3.md、`docs/权限表.xlsx` / `backend/data/whitelist.db`

## 1. 背景与问题

1. `docs/权限表.xlsx` 是唯一事实源，每月调查员轮换需开发者手工改 Excel 再跑同步脚本，无法支撑全省铺开。
2. `admin_level`（省级/市级/区县/调查员）一个字段同时承担「业务层级」与「功能权限」，概念混淆；「系统管理员」没有独立建模。
3. 后台安全缺口：除测验和 LLM 切换外，`/api/admin/*`（白名单、反馈看板、用量、KB 缺口）只校验登录（`require_user`），任何白名单调查员知道 URL 即可调用。
4. 离职账号 token 在 24h TTL 内仍可用：`require_user` 只验签名不查 active。
5. 无审计，无法追责；区县无管理入口。

## 2. 目标与非目标

**目标**
- 建立双维度权限模型：`admin_level`（业务层级=管辖范围）＋ `sys_role`（系统职能=功能权限）。
- `whitelist.db` 成为实时唯一事实源；xlsx 降级为初始导入模板 + 导出存档物。
- 区县/市级业务管理员在网页自行维护本区域白名单，并查看本区域只读统计（使用情况 + 测验完成率）。
- 系统管理员（仅 1 人）拥有全部后台；市级及以上业务管理员可出题，但下发/统计限定本区域。
- 停用即时生效；所有写操作可审计。

**非目标**
- 不做开放注册、短信/验证码登录（沿用 ADR 0005 手机号白名单 + HMAC token）。
- 不做整县名单覆盖导入（每月按「零星增删 + 批量停用」处理）。
- 省级业务管理员仅模型预留，本期不做独立管理界面。
- 不改调查员对话与答题功能本身。

## 3. 权限模型（核心）

### 3.1 双维度定义

| 维度 | 取值 | 含义 |
|---|---|---|
| `admin_level`（现有字段） | 省级 / 市级 / 区县 / 调查员 | 业务管辖范围：管全省 / 本市 / 本县 / 不管理 |
| `sys_role`（新增字段） | 系统管理员 / 业务管理员 / 普通用户 | 系统职能：全后台 / 分区后台 / 无后台 |

合法组合：`sys_role=系统管理员` 仅 1 人（你本人，`.env` 配置引导）；`sys_role=业务管理员` 可配 省级/市级/区县；`sys_role=普通用户` 通常配 调查员（其余业务层级也可配，视为普通用户）。

### 3.2 功能权限矩阵

| 功能 | 系统管理员（你） | 省级业务管理员 | 市级业务管理员 | 区县业务管理员 | 普通用户 |
|---|---|---|---|---|---|
| 对话 / 答题 | ✓ | ✓ | ✓ | ✓ | ✓ |
| 白名单管理范围 | 全局 | 全省 | 本市 | 本县 | ✗ |
| 白名单可设 `admin_level` | 省级/市级/区县/调查员 | 市级/区县/调查员 | 区县/调查员 | 仅调查员 | — |
| 使用情况统计（只读） | 全局 | 全省 | 本市 | 本县 | ✗ |
| 测验完成率（只读） | 全局 | 全省 | 本市 | 本县 | ✗ |
| 出题/编辑/审核题目 | ✓ | ✓ | ✓ | ✗ | ✗ |
| 下发/追加/移除目标 | 不限区域 | 全省 | 仅本市 | ✗ | ✗ |
| 反馈看板 / KB 缺口 / 标记处理 | ✓ | ✗ | ✗ | ✗ | ✗ |
| LLM 路由查看/切换 | ✓ | ✗ | ✗ | ✗ | ✗ |
| 审计查询 | ✓ | ✗ | ✗ | ✗ | ✗ |
| CSV 批量导入 | ✓ | ✗ | ✗ | ✗ | ✗ |
| 导出 | 全局 xlsx | 全省 xlsx | 本市 xlsx | 本县 CSV | ✗ |

### 3.3 区域范围规则

- `scope(actor)` = 从用户记录读取 `(province, city, county)`；系统管理员无限制。
- 匹配：省级 = 同 province；市级 = 同 province+city；区县 = 同 province+city+county。
- 任何业务管理员操作目标为 `sys_role=系统管理员` 的记录 → 403（系统管理员账号不可被业务管理员停用/编辑）。
- 保护测试号 `13985000001-4`：仅系统管理员可停用/删除，其余角色 403。
- 仅系统管理员可设置/修改 `sys_role`；业务管理员的表单/API 不出现该字段。
- 区县业务管理员不能把用户移出本县（目标区域必须等于本县）；市级不能动省级/市级同级。

## 4. 数据模型（一次 schema 变更，本 PRD 即确认）

```sql
ALTER TABLE whitelist ADD COLUMN sys_role TEXT NOT NULL DEFAULT '普通用户';

CREATE TABLE IF NOT EXISTS whitelist_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_phone  TEXT NOT NULL,
    actor_name   TEXT,
    action       TEXT NOT NULL,   -- create/update/disable/enable/batch_disable/sys_role_change/import
    target_phone TEXT NOT NULL,
    before_json  TEXT,
    after_json   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_target ON whitelist_audit(target_phone);
CREATE INDEX IF NOT EXISTS idx_audit_created ON whitelist_audit(created_at);
```

- `constants.py` 新增 `SysRole` 枚举：`SYSTEM_ADMIN="系统管理员"`、`BUSINESS_ADMIN="业务管理员"`、`USER="普通用户"`。
- 迁移逻辑（沿用 `whitelist_db._MIGRATIONS` 模式）：
  1. 检测缺 `sys_role` 列 → 加列；
  2. 回填：`admin_level IN (省级,市级,区县)` → 业务管理员；`调查员` → 普通用户；
  3. 读取 `.env` 的 `LSX_SYSTEM_ADMIN_PHONE` → 该号码 `sys_role=系统管理员` 且 `active=1`（缺失则启动 warning，系统管理员专属功能 403）；
  4. 建审计表与索引。
- 审计保留 12 个月，清理沿用项目现有清理风格。
- **不做整县覆盖导入**，`sync_whitelist_xlsx.py` 标注 deprecated（仅一次性初始导入/恢复），避免旧 xlsx 覆盖线上。

## 5. 鉴权依赖（`backend/app/infra/auth.py`）

| 依赖 | 放行条件 | 用途 |
|---|---|---|
| `require_user` | 登录 + **每次请求查 `load_whitelist()` 确认 active**（修复离职 24h 延迟） | 对话/答题 |
| `require_whitelist_admin` | `sys_role ∈ {系统管理员,业务管理员}`，返回完整 user（含区域） | 白名单/统计 |
| `require_quiz_admin` | `sys_role ∈ {系统管理员,业务管理员}` 且 `admin_level ∈ {省级,市级}` | 测验管理 |
| `require_system_admin` | `sys_role=系统管理员` | 反馈/KB/LLM/审计/CSV 导入 |

新增纯函数（`infra/auth.py`，必须有单测）：`region_scope(user)`、`in_scope(actor, target)`、`allowed_admin_levels(actor)`、`is_protected_phone(phone)`。

## 6. API 设计

统一规则：写操作成功即写审计；错误码 401（未登录/停用/失效）、403（越权）、404、409（状态冲突）、422（校验失败）。

| 方法与路径 | 权限 | 说明 |
|---|---|---|
| `GET /api/admin/whitelist/whoami` | require_whitelist_admin | 返回 `{phone,name,admin_level,sys_role,province,city,county}`，前端角色探测 |
| `GET /api/admin/whitelist` | require_whitelist_admin | 列表按 scope 过滤（系统管理员含全部角色） |
| `POST /api/admin/whitelist` | require_whitelist_admin | 新增；校验 scope + admin_level 上限；业务管理员忽略 body 中 sys_role（强制按自身身份） |
| `PUT /api/admin/whitelist/{phone}` | require_whitelist_admin | 更新；**不改变 active**（停用后编辑不会复活） |
| `PATCH /api/admin/whitelist/{phone}/enable` | require_whitelist_admin | 重新启用（原 UI 缺恢复入口） |
| `DELETE /api/admin/whitelist/{phone}` | require_whitelist_admin | 软删除；保护号/系统管理员账号校验 |
| `POST /api/admin/whitelist/batch-disable` | require_whitelist_admin | body `{phones:[...]}`；逐条校验 scope/保护号；返回 `{disabled, skipped:[{phone,reason}]}` |
| `GET /api/admin/whitelist/export` | require_whitelist_admin | 区县 → CSV；市级/省级/系统管理员 → xlsx 双 sheet（调查员/管理人员，列头沿用原权限表：省/市/县/调查小区/姓名/联系电话/管理员层级/备注） |
| `GET /api/admin/whitelist/audit?limit=100&target_phone=` | require_system_admin | 最近审计记录，倒序 |
| `POST /api/admin/whitelist/import-csv` | require_system_admin | 收紧为系统管理员（原任何登录用户可用） |
| `GET /api/admin/usage/search` | require_whitelist_admin | 查询条件与 actor scope 取交集（业务管理员不可查区外） |
| `GET /api/admin/feedback/stats`、`POST /api/admin/feedback/resolve`、`GET /api/admin/usage/gaps`、`POST /api/admin/usage/gaps/mark` | require_system_admin | 收紧 |
| `GET/POST /api/admin/llm/route` | require_system_admin | GET 从 require_user 收紧 |
| `GET /api/admin/quiz/targets` | require_quiz_admin | 非系统管理员只返回本范围（市级→本市分组，省级→全省） |
| `POST /api/admin/quiz/publish` | require_quiz_admin | `req.targets` 逐号校验在 actor 范围内（系统管理员不限），否则 422 返回越权号码列表 |
| 测验统计（`_stats_rows`/相关接口） | require_quiz_admin | 非系统管理员按 scope 过滤行与汇总 |

示例（batch-disable 响应）：`{"ok":true,"disabled":2,"skipped":[{"phone":"139****0001","reason":"protected"},{"phone":"139****0002","reason":"out_of_scope"}]}`

## 7. 前端改动

- `backend/static/whitelist.html`（角色化改造）：
  - 首屏调 `whoami`，非管理员提示无权限并跳首页；
  - 区县：仅见本县数据，`admin_level` 下拉锁定「调查员」，无审计 tab，导出出 CSV；
  - 市级：见本市（区县/调查员），可设「区县/调查员」；系统管理员：全量 + `sys_role` 下拉 + 审计 tab；
  - 新增：多选 checkbox + 「批量停用」、行内「启用」按钮（inactive 行）、「导出」按钮；
  - CSV 导入按钮仅系统管理员可见。
- `backend/static/dashboard.html`：按角色渲染——业务管理员只见「本区域 使用情况 + 测验完成率」只读 tab（隐藏 KB/反馈/LLM/审计），系统管理员全量；顶部新增「白名单管理」入口（当前无入口）。
- `backend/static/login.html`：登录后路由——系统管理员/市级以上 → `/quiz-admin`；区县 → `/whitelist-admin`；普通用户 → `/`。
- `backend/static/quiz_admin.html` / `quiz-stats.html`：市级业务管理员的目标选择与统计只显示本市；系统管理员/省级不限（前端隐藏区外分组，后端强校验兜底）。
- `common.js` 无需改动（`authHeader()`/`handle401()` 沿用）。

## 8. 安全与合规

- 修复 `require_user` 不查 active 的漏洞（停用即时生效）。
- 所有 `/api/admin/*` 端点有角色校验，调查员 403。
- 业务管理员不可触系统管理员账号、保护号、区外数据、`sys_role` 字段。
- 手机号日志沿用 `phone[:3]****` 脱敏，不打印明文。
- 不引入新依赖（`openpyxl` 已有）；`sys_role` 只有三种取值，不扩展。
- 合规红线：本次 schema 变更（加列 + 审计表）经本 PRD 确认；不删 `docs/权限表.xlsx`；不 push 默认分支（按项目红线）。

## 9. 测试计划

**单测（新增 `backend/tests/test_whitelist_rbac.py`、`test_whitelist_audit.py` 等）**
- `region_scope` / `in_scope` / `allowed_admin_levels` / `is_protected_phone` 纯函数全组合；
- `require_user` active 校验（停用后 401）、`require_whitelist_admin` / `require_quiz_admin` / `require_system_admin` 权限矩阵（mock 用户）；
- 白名单 CRUD 越权：区县跨县 403、区县设「区县」级别 403、业务管理员改 `sys_role` 403、保护号被非系统管理员停用 403；
- `batch-disable` 部分跳过语义；`enable` 语义（PUT 不复活）；
- 审计写入字段完整；导出 xlsx 双 sheet 列头 / CSV 格式；
- quiz publish 范围校验（mock quiz_db，返回越权号码）。

**集成/手工验收（用测试号 13985000001-4 分别扮演角色）**
- 调查员调任何 `/api/admin/*` → 403；停用后原 token 立即失效；
- 区县业务管理员：本县增删改成功、跨县 403；
- 市级业务管理员：本市下发成功、targets 含外市号码时 publish 422；
- 系统管理员：全局白名单、审计、LLM、KB 反馈全部可用。

**回归**
- `cd backend && pytest tests/ -q` 全绿；
- `python scripts/run_eval.py --phone 13985000001` 通过。

**DoD**
- 全量测试 + eval 绿；`.env` 配好 `LSX_SYSTEM_ADMIN_PHONE`；迁移 dry-run 脚本输出 diff；ADR 0015 与 CONTEXT.md 更新；手工验收场景表全过。

## 10. 里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| M1 数据层+鉴权 | `sys_role` 迁移、审计表、`infra/auth.py` 四依赖 + 纯函数 | 单测绿 |
| M2 白名单 API+前端 | scoped CRUD、enable、batch-disable、export、audit、whoami；`whitelist.html` 角色化 | 各区角色手工验收 |
| M3 范围化 | `usage/search` 范围、dashboard 角色 tab、login 路由、quiz targets/publish/stats 范围 | 市级下发边界验收 |
| M4 加固+回归 | feedback/gaps/llm/import-csv 权限收紧、全量测试 + eval | 全绿 |
| M5 文档+上线 | ADR 0015、CONTEXT.md、sync 脚本 deprecated 标注、`.env` 配置、生产迁移 | 验收场景表全过 |

## 11. 白名单地界（禁止越界）

- 不改对话/答题核心逻辑；不开放注册、不加短信/验证码；不删 `docs/权限表.xlsx`。
- `sys_role` 仅三种取值；不实现整县覆盖导入；除本 PRD 声明的加列+审计表外不做其它 schema 变更。
- 省级业务管理员只做模型预留，不新增独立页面。

## 12. 风险与对策

| 风险 | 对策 |
|---|---|
| 市级业务管理员误停用区县管理员 | 审计 + 系统管理员用 enable 恢复 |
| 区县管理员滥用 | 范围锁定 + 仅调查员 + 审计可追责 |
| 迁移出错 | 先备份 `whitelist.db`，迁移 dry-run 输出 diff，确认后执行 |
| 系统管理员手机号仅靠门禁 | ADR 0005 已知权衡；严格保管 URL 与 `LSX_AUTH_SECRET`；后续可选短信二次验证（不阻塞本期） |