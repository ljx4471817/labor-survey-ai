# 劳动力调查 AI 助手 · 项目约定

> 本文件是项目级 Codex 约定；项目内工作以这里的目录、验证和合规规则为准。

## 项目身份

- **名称**：劳动力调查 AI 助手（labor-survey-ai）
- **目标**：为辅助调查员提供基于 RAG 的即时填报指导
- **用户**：国家统计局贵阳调查队系统的辅助调查员（处室自用起步）
- **载体**：H5 单页应用 + FastAPI 后端 + Cloudflare Tunnel 暴露公网（前端选型见 ADR 0001）

## 执行约定（红线之上）

### 不间断执行原则（最高优先级）
- **一次任务开始后，必须连续执行直到完成，不得中途停下来等用户确认或催促。**
- **用户说继续/不要停/一次性做完后，后续所有操作必须一口气完成，中间不输出等待性文字，直接执行下一个工具调用。**
- 如果遇到shell不可用、网络错误等阻塞，最多重试3次；如果仍然失败，在最终消息里说明阻塞原因和手动恢复步骤，不要无限循环。
- 如果任务包含多个Phase，按顺序一口气跑完，每个Phase内部不暂停。
- 只有以下情况才允许中断：
  1. 需要用户提供密钥、密码等敏感信息
  2. 需要用户做不可逆操作决策（删除、push、采购等，见合规红线）
  3. 遇到物理阻塞（shell连续不可用、磁盘满等）
- **预告必须当场执行** —— 如果一个句子语法上说了"马上做什么"（"立刻启动"、"我接下来要 X"、"I'll X"、"let me X"），同一个回合内必须有对应的工具调用执行它。预告变成空话 = 已违反红线。
- 用户没问后续计划时，不用长篇复述未来步骤；工具调用之间只报必要进展。

- **工具报错不是停止信号** —— 换写法重试一次；还失败就在最终结果里说明，绝不把工具错误当作“任务已完结”。
- **一整条链路一口气跑完** —— 有终点的任务（起隧道 / commit / eval）完成后直接接下一步，不在中间输出长 preamble 等推动。
- **一次授权做透** —— 用户明确授权的动作，做完整条链路，不在中间又举手确认。**

## 用户身份

我是项目开发者，使用 Codex 协作。沟通使用中文，结论先行，不谄媚。

## 新功能开发规范

> **新增功能时必须遵守以下分层规范，不得往已有文件里堆逻辑。**

### 文件放置规则
- **路由层**：`backend/app/api/` —— 每个业务域一个 `xxx.py`（如 `feedback_admin.py`）
- **业务逻辑**：`backend/app/services/` —— 纯函数或 IO 编排，按领域命名
- **检索算法**：`backend/app/rag/pure.py`（纯函数）或 `rag/retriever.py`（IO 层）
- **数据模型**：`backend/app/models/schemas/` —— 按用途放 `chat.py` / `admin.py` / `common.py`
- **持久化**：`backend/app/persistence/` —— 每个实体一个文件
- **基础设施**：`backend/app/infra/` —— 鉴权、配置等横切关注点
- **枚举常量**：`backend/app/core/constants.py` —— 不放魔法字符串

### 开发红线
- **不改旧文件**（除非是 bug fix），新增功能新建文件
- **纯函数必须有单测**：放 `backend/tests/test_xxx.py`，用 `pytest tests/ -q` 验证
- **RAG 规则冲突裁决**：当检索结果命中两条以上冲突条款时，按 system prompt 硬规则 7 执行（发放周期优先 → 住户配合度 → 兜底流程不替代判断）。装修工/建筑工/零工等灵活就业人员，先问清楚发放周期再给结论。详见 docs/adr/0013-rag-规则冲突裁决.md。
- **改完后必须跑全量测试**：`pytest tests/ -q` + `python scripts/run_eval.py --phone 13985000001` 全绿才算完
- **关键词/配置外移**：不把可配置项写死代码，放 `data/*.json` 或 `.env`
- **CONTEXT.md 同步**：新增领域词汇时同步更新 `docs/CONTEXT.md`
- **Codex skill 编码**：`.codex/skills/*/SKILL.md` 必须 UTF-8 **无 BOM**（带 BOM 的技能会被加载器跳过、无法主动触发；2026-08-11 排查发现并修复 kb-update-workflow / regulations-migrate）

## 当前阶段

**迭代 1 已完成 ✅**：H5 + Cloudflare Tunnel + 视觉升级 + 吉祥物接入 + 推送 GitHub。
**迭代 2 已完成 ✅**（2026-06-22 ~ 2026-06-26）：KB 质量优化（schema v1 indicators 字段 + 制度对齐机制）+ 反馈闭环 + 采购可行性预算 + 手机号白名单门禁 + Dashboard 看板 + 区域下钻 + KB 5 阶段入库流程。
**迭代 3 进行中**：架构重构已完成（Phase 1-9，eval 102/100% 回归通过）+ KB schema v2 评估（`scenario` 字段）+ 采购落地（材料已就绪，待领导决策后启动域名备案）。

参见 `docs/02-可行性审核.md` 第四节「已确认的决策」和 ADR 索引。

## ADR 索引

| 编号 | 标题 | 主题 |
|------|------|------|
| `0001-前端选型.md` | 2026-06-21 反转：原"微信小程序"改为 H5 | 前端载体 |
| `0002-向量库选型.md` | Chroma + BM25 Hybrid 选型 | 检索 |
| `0003-embedding-部署方式.md` | Embedding API 选型与成本 | 检索依赖 |
| `0004-内网穿透方案.md` | Cloudflare Tunnel quick 模式 | 部署 |
| `0005-手机号白名单门禁.md` | HMAC token + whitelist.json | 鉴权 |
| `0006-反馈闭环与Dashboard看板.md` | admin API + dashboard.html + resolved event log | 反馈运营 |
| `0007-多轮对话上下文.md` | history 字段 + merge_query_with_history + history_context | 对话 UX |
| `0008-制度对齐机制.md` | indicators 字段 + indicator_catalog.json + migration_map.json + regulations-migrate skill | KB 质量基础设施 |
| `0009-voice-disabled.md` | 2026-06-21 语音功能停用（输入法自带语音转写够用） | 功能开关 |
| `0010-embedding-v4.md` | DashScope text-embedding-v4 + 共享 collection 重建边界 | 检索依赖 |
| `0011-不引入ponytail.md` | 2026-07-30 评估后决定不引入（与项目分层规则冲突，无实际痛点驱动） | 开发规范 |
| `0012-月度测验系统.md` | 6 表 SQLite + LLM 要点提取 + 4 选 1 选择题 + 完成率看板 | 测验系统 |
| 013-rag-规则冲突裁决.md | system prompt 硬规则 + FAQ scope + eval 三层冲突裁决 | 检索治理 |
| `0014-llm-主备切换.md` | 三级路由：MiniMax 主用 -> qwen-flash 备用 -> DeepSeek 兜底，5h/7d 用量超阈值切换 | LLM 路由 |
| `0015-权限系统双维度.md` | admin_level × sys_role 双维度 + 审计表 + 分级网页维护 | 权限治理 |

## 目录约定

| 目录 | 用途 | 谁能改 |
|------|------|--------|
| `docs/` | 方案、审核、架构、ADR 等静态文档 | 自由修改 |
| `docs/adr/` | 架构决策记录（一旦写定不轻易改） | 增量追加，不改旧 ADR |
| `knowledge-base/raw/` | 原始 PDF/Word 制度文档 | **不直接修改**，只读 |
| `knowledge-base/qa/` | 结构化 QA JSON | 自由修改 |
| `knowledge-base/chunks.jsonl` | **构建产物**（markdown→chunk），不入 git | `build_chunks.py` 管理 |
| `knowledge-base/indicator_catalog.json` | **制度指标目录**（按模块组织的 F/H 编号 + 名称），schema v1 核心 | 制度变更时改 |
| `knowledge-base/migration_map.json` | **迁移映射**（renamed/removed/added），每次制度变更生成一份 | 自由修改（按需） |
| `backend/app/` | FastAPI 应用代码 | 自由修改 |
| `backend/app/api/` | 各业务模块路由（chat / feedback / feedback_admin / usage_admin / whitelist_admin / gaps_admin / auth / voice / **quiz / quiz_admin** / llm_admin） | 自由修改 |
| `backend/app/core/` | 配置 + 枚举常量（config.py / constants.py） | 自由修改 |
| `backend/app/models/schemas/` | Pydantic 请求/响应模型子包（common / chat / admin） | 自由修改 |
| `backend/app/rag/` | 检索算法（pure.py = 纯函数 / retriever.py = IO 层 / bm25 / llm / prompts） | 自由修改 |
| `backend/app/infra/` | 基础设施（auth.py = HMAC 签名 + 白名单校验） | 自由修改 |
| `backend/app/persistence/` | SQLite 持久化（whitelist_db / query_log / **quiz_db**） | 自由修改 |
| `backend/app/analytics/` | 使用侧分析（gaps.py = KB 闭环检测） | 自由修改 |
| `backend/app/services/` | 业务服务（feedback_analytics / jsonl_utils / **quiz_extract / quiz_generator / quiz_llm / aliyun_balance**） | 自由修改 |
| `backend/app/api/_xunfei_auth.py` | DISABLED（讯飞语音鉴权，代码完整保留） | 不修改 |
| `backend/data/` | 运行时数据（SQLite / JSONL / scope_keywords.json / **llm_route.json / quiz_llm_config.json**） | 自由修改 |
| `backend/tests/` | 后端单元测试（221 tests） | 自由修改 |
| `scripts/watchdog*.ps1` | 本地 API 可用性监控与自动重启 | 自由修改 |
| `backend/static/kb-images/` | ????????PPT ?????? `page_XX/` ?? | ???? |
| `backend/static/` | H5 前端（单页应用）+ 测验 3 页面（quiz.html / quiz_admin.html / quiz-stats.html） | 自由修改 |
| `scripts/` | 跨子项目运维脚本 | 自由修改 |
| `deploy/` | 部署配置（含 ssl/ / systemd/ 占位） | 谨慎修改，影响线上 |
| `.codex/skills/` | **项目级 Codex skill**（已 git 入仓），含 `regulations-migrate` / `kb-update-workflow` / `kb-optimize` / `kb-cleanup` / `whitelist-sync` / `pptx-structured-ocr` | 自由修改 |

> `miniprogram/` 保留为决策反转前的历史骨架（已 git 追踪 `.gitkeep` 占位），不再修改其内容。

## 关键命令

> 在项目根目录下执行。

```bash
# 初始化 codegraph 索引（首次必跑，之后不用）
codegraph init -i

# 知识库：一站式重建（推荐日常使用）
# 从 faq.json + 4 个 markdown 源文件重建 Chroma + BM25 索引，避免漏跑
python scripts/rebuild_all.py              # 全量重建
python scripts/rebuild_all.py --incremental  # 增量更新（仅更新变动条目）

# 知识库：独立脚本（仅在需要单独使用时使用）
# python scripts/rebuild_all.py 已自动调用以下三者，无需手动分跑：
# python scripts/build_kb.py        # QA 入库 Chroma
# python scripts/build_chunks.py --input knowledge-base/raw/markdown/xxx.md --full  # chunk 入库 Chroma
# python scripts/build_bm25.py --full  # 构建 BM25 索引
# 知识库：QA 字段完整性校验（改 faq.json 后必跑，含 indicators 合法性）
python scripts/validate_faq.py
# ????PPT ?????????? PPT ???? + OCR?
python scripts/extract_pptx.py <source.pptx> <out_dir>  # ??????+??
python scripts/ocr_images.py <out_dir>                  # ?? OCR ????
# 知识库：制度对齐（首次/制度变更后必跑）
python scripts/backfill_indicators.py         # 从 source/question/answer 自动提取 indicators
python scripts/backfill_indicators.py --write  # 写入
python scripts/smart_backfill_indicators.py --write  # 语义回填 review 条目
python scripts/migrate_indicators.py migration_map.json         # 制度变更 dry-run
python scripts/migrate_indicators.py migration_map.json --write # 制度变更执行
# 知识库：从 docx 提取题 + 构造 eval 集
python scripts/eval_from_docx.py
python scripts/build_eval_set.py
# 知识库：检索评估（门禁启用后必传 --phone 拿 token，否则 401）
python scripts/run_eval.py --phone 13985000001  # 白名单真实号码，从 backend/data/whitelist.db 取
# 知识库：检索评估 + 弹系统弹窗（Windows；自动启后端+跑 eval+弹 MessageBox）
scripts\run_eval_notify.bat  # 推荐日常用，等同跑完弹窗通知
# 知识库：检索方式对比
python scripts/compare_retrieval.py
# 知识库：根据 eval 结果回写 KB / 改写源 docx
python scripts/patch_faq_from_eval.py
python scripts/patch2_faq_retrieval.py
python scripts/rewrite_docx.py
# 知识库：新题库入库流程（4 阶段；详见 kb-update-workflow skill）
python scripts/ingest_source.py knowledge-base/raw/<new>.docx
python scripts/extract_qa_pairs.py knowledge-base/raw/markdown/<stem>.md --mode llm
python scripts/detect_gaps.py --candidates reports/extracted-qa-<stem>.json
python scripts/add_faq_entries.py reports/approved-<stem>.json

# 报告：成本预算（采购可行性 / 领导汇报）
python scripts/generate_cost_report.py
# 报告：项目介绍（向上级汇报 Word）
python scripts/generate_project_intro.py

# 部署：从 cloudflared 日志抽取 trycloudflare URL（替代手抄）
python scripts/extract_cf_url.py

# 白名单：whitelist.db 实时唯一事实源（PRD 权限系统改造后）；网页 /whitelist-admin 分级维护
python scripts/migrate_whitelist_rbac.py --dry-run  # 上线前迁移 dry-run（输出 sys_role diff）
python scripts/migrate_whitelist_rbac.py --apply    # 真实迁移（自动备份 backend/data/backups/）
# 注意：sync_whitelist_xlsx.py 已 DEPRECATED（仅初始导入/恢复），日常禁止再跑，否则旧 xlsx 会覆盖线上名单
# LLM：模型 A/B 评测 + 阿里云余额监控
python scripts/compare_models.py --models minimax,qwen-flash --limit 25  # 模型 A/B（同检索同 prompt 同评分；全量 104 题加 --out 落盘）
python scripts/check_qwen_balance.py             # 阿里云账户余额（qwen-flash 按量扣此）
python scripts/check_qwen_balance.py --bill     # 本月百炼消费明细
# 测验：本地测试（QUIZ_MOCK_LLM=1 跳过真实 LLM 调用）
set QUIZ_MOCK_LLM=1 && python -m pytest backend/tests/test_quiz_api.py -q
# 测验：手动 curl 测试（先登录拿 token，再调管理端 API）
# python scripts/quiz_stress.py  # 压力测试（并发答题）


# 后端：本地启动（开发模式，不需要公网）
cd backend && uvicorn app.main:app --reload --port 8001

# 后端 + 公网穿透：一键启动（H5 + Cloudflare Tunnel）
scripts\start_tunnel.bat
# 或：跑 start_tunnel.bat → 抓取 URL 用 extract_cf_url.py

# 后端：测试
cd backend && pytest tests/ -q

# 后端：依赖安装
cd backend && pip install -r requirements.txt
```

## 代码风格

**通用**：匹配代码库已有风格，避免无关重构。

**Python（后端）**：
- 类型注解必加（公共函数）
- 端点用普通 `def`（FastAPI 自动跑线程池；混 `async` 反而需要避免阻塞调用）
- 配置从环境变量读，不硬编码
- 公共函数加 docstring（一行说明 WHY）

**H5 前端（`backend/static/`）**：
- `index.html`：调查员对话主页面
- `login.html`：手机号白名单登录页（门禁启用后所有页面必经）
- `dashboard.html`：统一后台入口——系统管理员全量（KB 优化 / 使用监测 / 使用侧发现）；业务管理员默认进入「白名单管理」模块；顶部有「测验管理」「退出登录」
- `whitelist.html`：白名单管理页（角色化 CRUD / 批量停用 / 启用 / 导出 / 审计 / CSV 导入；支持 `?embed=1` 作为 dashboard 模块嵌入）
- 共享工具函数放 `common.js`（`$()`、`escapeHtml()`、token 管理）
- 工具函数 / API 调用就近写，不强求模块化
- 浏览器原生 API 优先，不引入框架
- 后台 UI 约定：表格用「核心列 + 详情展开 + 窄屏（≤768px）卡片化」，弹窗限高 `calc(100vh - 32px)` + body 内滚动

## 合规红线

> 本节列出本项目的高频合规边界。

- 不收集居民个人信息（H5 不接触调查数据）
- 不把 API Key、token 写进代码或 commit
- 任何会话**不打印、不复述 `.env` 真实值**；如出现在日志里，事后必须轮换所有相关 Key
- 修改 `.env`、CI/CD 配置、部署脚本前先问我
- 单位主体备案流程启动前先确认
- **删除文件/目录/git 历史前先问我**
- **数据库 schema 变更/数据迁移前先问我**
- 未经明确要求不 push 到 main / 默认分支

**当前 API Key 配置**：MiniMax（对话 Token Plan，在 .env）、DeepSeek（对话，系统环境变量）、DashScope（向量 Embedding，在 .env）；讯飞 2026-06-21 起停用，环境变量保留供未来恢复。

## 知识库质量标准

知识库是回答质量的决定因素，比代码更重要：

- 每条 QA 必须标注 `source`（制度依据）
- 每条 QA 必须有 `category`（分类用于离线浏览）
- `question` 和 `answer` 用正式书面语，不口语化
- 关键词数组用于离线检索，至少 3 个
- 不确定的答案宁可不录，不要编造
- **每条 QA 必须有 `indicators` 字段**（关联 `indicator_catalog.json` 中的 F/H 编号）；程序/抽样/入户技巧等非指标类条目用 `_indicators_topic` 标注（详见 ADR 0008）
- 制度变更后走 `migration_map.json` 同步，**不要手工改 indicators**（详见 `知识库更新与制度更新方法.md`）
- **实操约定**：凡贵阳调查队自定义的填报规范（在实际工作中总结、制度原文未明文的规则），一律在 `source` 字段标注"贵阳调查队填报规范指引・XXX（制度依据：YYY 结合 实际判断）"，用于区分制度原文条目和自建指引。示例见 `knowledge-base/qa/faq.json` id=355（大学生家庭登记规则）。

**Corner case 处理流程**：遇到「KB 命中但答得不准」或「fallback 兜底」时：
1. 查 `knowledge-base/raw/markdown/` 对应章节原文
2. 按场景拆成独立条目（每条聚焦一个 corner case）
3. 跑 `python scripts/validate_faq.py`（字段完整性）+ `python scripts/build_bm25.py --full`（索引重建）
4. 在 `eval_set.json` 加 eval 锁定（`must_contain_any` 列表任一命中 → 硬指标；`should_not_contain` 拦截典型错误措辞；多轮场景可配 `history` 字段）

**每年 12 月初**：用 `git diff` 对比新旧《劳动力调查制度》文档，列出可能受影响的 KB 条目，业务人员 + 开发人员 review。**优先走 `regulations-migrate` skill**（`.codex/skills/regulations-migrate/`），整条链路标准化。

## 月度测验业务约定

- 题目审核即下发选择：管理端「确认」/「编辑保存」题目 -> 自动 selected=1（拟下发）；「打回」 -> selected=0（退出下发）。没有独立的「下发此题」勾选框，发布时下发所有 selected=1 且 approved 的题目。
- 管理端 quiz_admin.html 为「侧边栏测验列表 + 工作台」两栏，顶部步骤条（导入→提取→要点→生成勾选→下发）按数据状态自动打勾；完成率内嵌在「完成率」 tab（/api/admin/quiz/stats），独立 /quiz-stats 页保留。
- 用户端 quiz.html「已完成·过期」项显示得分（/api/quiz/my done 项 score = 答对数）。

- 测验模块 LLM **独立配置**（`backend/data/quiz_llm_config.json`，默认 qwen-flash），与对话三级路由完全隔离：切换测验模型不影响文档程序模型。切换仅系统管理员（`POST /api/admin/quiz/llm-config`，切换前探测可用性）；业务管理员零感知（看不到切换入口与当前模型）。配置带 updated_at/updated_by 留痕。

## LLM 三级路由约定（2026-08-14 起）

- 优先级链：MiniMax M2.7-highspeed（主）-> qwen-flash（DashScope，额度用尽后优先）-> DeepSeek flash（deepseek-v4-flash，最后兜底）。
- 切换阈值：MiniMax 5h >=85% 或 7d >=90% -> 切 qwen-flash；5h <70% 且 7d <85% 且冷却 >=30 分钟 -> 切回 MiniMax（qwen-flash / DeepSeek 同样按此回主）。
- qwen-flash 按量付费无配额上限（.env: LLM_PROVIDER=minimax 保持主模型，DASHSCOPE_LLM_MODEL=qwen-flash）。
- 状态文件 backend/data/llm_route.json；用量查询用 MINIMAX_API_KEY（Bearer），不需要 _token。
- 用量检查连续失败 3 次沿链切下一级（MiniMax -> qwen-flash -> DeepSeek）。
- 手动切换：POST /api/admin/llm/route {provider: minimax|dashscope|deepseek|auto}。
- 查看当前模型/用量：GET /api/admin/llm/route（dashboard 使用监测 tab）。

## 待办

- ~~**迭代 3 / Stage 1：评估 KB schema v2**~~ —— 已完成 KB schema v1 全量落地（ADR 0008），v2 评估待领导决策后启动
- **迭代 3 / Stage 2：成本预算省级档采购落地**——¥87/月档（阿里云 ECS 2核4G + 域名 + 备案），材料已就绪（`reports/cost-budget-20260622.md` + docx/pdf + 项目介绍），待领导决策后启动域名备案 15-20 工作日
- **DeepSeek 提额申请结果待回**——用户已提交，参考 https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit ；批下来第一时间重跑 `scripts/load_test.py --all` 验证新 QPS 上限。**未批前不做 kb_direct 等 LLM 优化**（优先级低于 DeepSeek 提额）
- **新增测试覆盖**：chat.py 端到端（需 mock embedding + LLM）、auth.py HMAC 校验、bm25.py search 函数
- **miniprogram/ 目录**：加 README.md 说明"ADR 0001 反转后的历史骨架" 

# 数据维护：白名单（whitelist.db 实时唯一事实源；xlsx 仅初始导入/恢复，sync 脚本已 DEPRECATED）
python scripts/migrate_whitelist_rbac.py --dry-run  # 迁移 dry-run 输出 diff
python scripts/migrate_whitelist_rbac.py --apply    # 迁移（先备份）
# 数据维护：知识库一站式重建（推荐日常使用）
# 从 faq.json + 4 个 markdown 源文件重建 Chroma + BM25 索引，避免漏跑
python scripts/rebuild_all.py                    # 全量重建
python scripts/rebuild_all.py --incremental      # 增量更新

