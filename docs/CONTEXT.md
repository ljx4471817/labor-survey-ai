# 项目领域词汇表（CONTEXT）

> 本文件是项目级领域词汇的单一真相来源。新开发者 / AI agent 进入项目时，先读本文件。
> 配合 `docs/adr/` 一起用：CONTEXT 定义概念，ADR 记录决策。

## 1. 用户与角色

| 概念 | 定义 | 在哪里 |
|------|------|------|
| 辅助调查员 | 国家统计局贵阳调查队系统内的调查员，本系统的主要用户 | `whitelist_db.user.name` + `phone` |
| 调查户 | 被调查的家庭户/住户，是制度文档的对象 | 仅在 KB 与制度文档中出现，后端无显式字段 |
| 制度 | 《劳动力调查制度》年度定稿，国家统计局发布 | `knowledge-base/raw/markdown/劳动力调查制度（YYYY年定期报表）-定稿.md` |
| 调查周期 | 通常是月度调查 | 仅在 KB `source` 字段和制度文档章节中出现 |
| 参考周 | 调查时点前一周，作为就业状态判定的时间窗口 | KB 与制度文档，后端无显式字段 |

## 2. 指标与编号

| 概念 | 定义 | 在哪里 |
|------|------|------|
| 指标 | 制度文档定义的统计指标（如 F27 = 劳动报酬） | `knowledge-base/indicator_catalog.json` |
| 指标编号 | `F` 前缀 = 制度正式指标；`H` 前缀 = 制度草稿/试行指标 | `indicator_catalog.json` 每个指标的 `id` 字段 |
| `indicators` 字段 | KB 中每条 QA 关联的指标编号列表 | `knowledge-base/qa/faq.json` 每条 QA |
| `_indicators_topic` | 非指标类条目标记（如程序、抽样、入户技巧） | `faq.json` 部分条目的特殊字段 |
| `migration_map.json` | 制度变更时指标 rename/remove/add 的映射 | `knowledge-base/migration_map.json` |
| `regulations-migrate` skill | 制度变更时的标准化 7 步流程 | `.codex/skills/regulations-migrate/` |

## 3. 区域五级

| 概念 | 定义 | 在哪里 |
|------|------|------|
| 区域五级 | 省 / 市 / 县 / 乡 / 社区 五个层级的行政区划 | `query_log._REGION_LEVELS`、`feedback_analytics.REGION_LEVELS` |
| 区域下钻 | Dashboard 从 province 逐级下钻到 community | `GET /api/admin/feedback/stats/region`（支持 cascading 过滤） |

> **注意**：`query_log` 和 `feedback_analytics` 各自维护一份区域层级元组，修改层级时要同步两处。

## 4. 知识库检索

| 概念 | 定义 | 在哪里 |
|------|------|------|
| QA 条目 | KB 中结构化的人工整理条目（question + answer + indicators），当前 354 条 | `knowledge-base/qa/faq.json` |
| Chunk 条目 | 制度文档 markdown 切片入库后的检索单元，当前 55 条 | `knowledge-base/chunks.jsonl`（构建产物） |
| 双轨检索 | QA 与 chunk 双源同时入库，BM25 + Chroma 都覆盖 | `build_bm25.py` / `bm25.py` 同时加载两源 |
| Hybrid 检索 | Chroma 向量检索 + BM25 关键词检索 + RRF 融合 | `rag/retriever.py` 的 `retrieve()` 函数 |
| RRF | Reciprocal Rank Fusion，Cormack 2009 提出的排名融合算法，c=60 | `rag/pure.py::rrf_fuse()` |
| `doc_type` | 元数据字段，区分 QA 条目和 chunk 条目 | `rag/prompts.py.format_kb_results` 据此分别渲染 |
| Embedding | 将 QA、chunk 和用户问题映射为语义向量；当前模型为 DashScope `text-embedding-v4` | `DASHSCOPE_MODEL` / `rag/retriever.py::embed_query` |
| 近一月热点问题 | 对话前台展示的滚动 30 天全局热门 QA；仅统计有 top1 QA 的成功 RAG 请求，最多展示 5 条标准问法 | `services/hot_questions.py` / `api/hot_questions.py` / `static/index.html` |
| `top_qa_id` | chat 命中 QA 时记录的 top1 QA 条目 ID；不回填历史数据 | `query_log.db` 的 `query_log` 表 / `api/chat.py` |
| Grounding 锚点 | RAG 回答缺失 top1 QA 关键词、场景词或指标编号时，追加的知识库内要点提示 | `rag/grounding.py` / `api/chat.py` |
| 历史会话 | 服务端按手机号永久保存的连续问答；用户可跨设备继续，管理端不可直接查看完整内容 | `persistence/conversations.py` / `api/conversations.py` |
| 会话轮次 | 一条用户消息 + 一条成功助手回复；失败轮次不进入会话 | `api/chat.py` / `persistence/conversations.py` |
| 来源快照 | 助手回答当时命中的 QA / 图片 / 来源 / 分数快照，用于历史回看还原依据 | `conversation_messages.sources_json` |

## 5. 检索模式枚举

> **使用位置**：`backend/app/core/constants.py::RetrievalMode`

| 模式 | 含义 | 触发条件 |
|------|------|--------|
| `rag` | 命中 KB 且 LLM 生成有效回答 | LLM 回答未触发 REFUSAL_PATTERNS |
| `out_of_kb` | LLM 主动拒答（"未找到相关内容"） | `REFUSAL_PATTERNS` 正则命中 |
| `out_of_scope` | 问题超出劳动力调查范围 | `is_in_scope()` 关键词过滤 |
| `ambiguous` | 多轮上下文中第 1 轮问题太模糊，需要追问 | `is_ambiguous()` 启发式 |

## 6. 反馈闭环

| 概念 | 定义 | 在哪里 |
|------|------|------|
| 反馈事件 | 调查员对 AI 回复的采纳/不采纳投票 | `POST /api/feedback` 写入 `backend/data/feedback.jsonl` |
| 反馈评级 | `up` = 采纳 / `down` = 不采纳 | `backend/app/core/constants.py::FeedbackRating` |
| 答案纠错反馈 | RAG 命中后选择“答案不正确，反馈”时必填的 `corrected_answer` + `evidence`；同一 `phone + request_id` 只能提交一次 | `POST /api/feedback` / `backend/data/feedback.jsonl` |
| 反馈复核 | 管理员对负面反馈标记 `accepted` / `rejected`，可改判，最新事件生效 | `POST /api/admin/feedback/resolve` / `backend/data/feedback_resolved.jsonl` |
| 复核状态 | `pending` / `accepted` / `rejected`；Dashboard 按状态分组展示 | `services/feedback_reviews.py` / `static/dashboard.html` |
| Query 日志 | 每次 chat 请求的元数据（不含答案内容） | `backend/data/query_log.db`（SQLite） |
| Dashboard | 数据看板：系统管理员全量（KB 复核队列 / 使用监测）；区县业务管理员登录直落「白名单管理」独立页，进入数据看板默认「使用监测」；顶部统一导航（数据看板 / 测验管理 / 白名单管理） | `backend/static/dashboard.html` |
| KB 改进候选 | 现在仅来自用户提交的负面反馈修正建议，不再从高频 query 自动生成 | `services/feedback_reviews.py::build_improvement_candidates` |

## 7. 鉴权与权限（PRD 权限系统改造后）

| 概念 | 定义 | 在哪里 |
|------|------|------|
| 手机号白名单 | 通过手机号 + 5 级区域预登记的可访问用户列表；**whitelist.db 是实时唯一事实源**（xlsx 仅初始导入/恢复模板） | `backend/data/whitelist.db`（SQLite） |
| `admin_level` | 业务管辖范围：省级 / 市级 / 区县 / 调查员 | `whitelist` 表 + `core/constants.py::AdminLevel` |
| `sys_role` | 系统职能：系统管理员 / 业务管理员 / 普通用户（仅三种取值） | `whitelist` 表 + `core/constants.py::SysRole` |
| 系统管理员 | `sys_role=系统管理员`，仅 1 人（`.env` 的 `LSX_SYSTEM_ADMIN_PHONE` 指定）；全后台：反馈/KB/LLM/审计/CSV 导入 | `.env` → `whitelist_db._migrate` 强制 active=1 |
| 业务管理员 | `sys_role=业务管理员`，按 `admin_level` 管辖本区域白名单 + 只读统计；市级及以上可管理测验 | `infra/auth.py` |
| `region_scope(actor)` | 管辖范围元组 (province, city, county)；系统管理员 = None（无限制） | `infra/auth.py` 纯函数 |
| `in_scope(actor, target)` | 目标记录是否在管辖范围内（省级=同省，市级=同市，区县=同县） | `infra/auth.py` 纯函数 |
| `allowed_admin_levels(actor)` | 业务管理员可为目标设置的管理员层级上限 | `infra/auth.py` 纯函数 |
| 保护测试号 | 13985000001-4：仅系统管理员可操作 | `infra/auth.py::is_protected_phone` |
| HMAC token | 登录后由服务端签发的 token，前端存在 localStorage | `infra/auth.py::sign_token` / `verify_token` |
| `LSX_AUTH_SECRET` | HMAC 签名的密钥（生产必须设置） | `.env` |
| `LSX_SYSTEM_ADMIN_PHONE` | 系统管理员手机号（未设置时系统管理员专属功能 403） | `.env` |
| `LSX_CONVERSATIONS_DB_PATH` | 会话历史 SQLite 路径；默认 `backend/data/conversations.db` | `.env` / `persistence/conversations.py` |
| `require_user` | 登录 + 每次请求查 whitelist 确认 active（停用即时生效） | `infra/auth.py` |
| `require_whitelist_admin` | `sys_role ∈ {系统管理员, 业务管理员}`，返回完整 user | `infra/auth.py` |
| `require_quiz_admin` | `sys_role ∈ {系统管理员, 业务管理员}` 且 `admin_level ∈ {省级, 市级}` | `infra/auth.py` |
| `require_quiz_stats` | `sys_role ∈ {系统管理员, 业务管理员}`（任意层级，完成率只读） | `infra/auth.py` |
| `require_system_admin` | `sys_role = 系统管理员` | `infra/auth.py` |
| 白名单审计 | 所有写操作留痕（actor/action/target/before/after），保留 12 个月 | `whitelist_audit` 表 + `whitelist_db.log_audit` |
| 手机号正则 | 11 位、`1[3-9]` 开头 | `models/schemas/admin.py::WhitelistEntry.phone` |
| 软删除 | 删除白名单不真删，标记 `active=0`；`PATCH .../enable` 恢复 | `persistence/whitelist_db.py::delete(soft=True)` / `enable` |
| 权限迁移脚本 | 上线前 dry-run 输出 diff，apply 前自动备份 | `scripts/migrate_whitelist_rbac.py` |

## 8. 部署与运行

| 概念 | 定义 | 在哪里 |
|------|------|------|
| Quick Tunnel | Cloudflare Tunnel 无域名模式，每次启动 URL 变 | `scripts/start_tunnel.bat` |
| Named Tunnel | Cloudflare Tunnel 绑定自有域名（生产模式，未启用） | 待 ADR 备案决策后启用 |
| DeepSeek 提额 | 单账号 ~45 并发连接瓶颈 | `reports/llm-bottleneck-analysis-20260629.md` |
| LLM 三级路由 | MiniMax M2.7-highspeed（主）→ qwen-flash（DashScope，额度用尽优先）→ DeepSeek flash（最后兜底）；5h>=85% 或 7d>=90% 切下一级，用量回落 <70% 且 7d<85% 且冷却 30min 回主；状态 backend/data/llm_route.json | `llm_router.py` / `minimax_quota.py` / `llm_switch_job.py` |

## 10. 月度测验系统（quiz）

| 概念 | 定义 | 在哪儿 |
|------|------|--------|
| 测验套 | 一次月度测验的完整数据单元（quiz_id = Q + YYYYMM + 序号） | `backend/data/quiz.db` → `quizzes` 表 |
| 要点 | 从月度通知中提取的可出题知识点 | `keypoints` 表 + `quiz_extract.py` |
| 题目 | 根据要点自动生成的 4 选 1 选择题 | `questions` 表 + `quiz_generator.py` |
| 下发 | 管理员选择目标用户并发布测验（action = publish/append/remove） | `quiz_admin.py` → `/quiz/publish` |
| 完成率 | 已完成人数 / 总目标人数 | `quiz-stats.html` + `/quiz/stats` |
| `_WRITE_LOCK` | 写串行化锁（SQLite 单写者，避免并发写 busy） | `quiz_db.py` 全局锁 |
| `require_quiz_admin` | 测验管理（系统管理员或省级/市级业务管理员）；`require_quiz_stats` 完成率只读（区县可看本县） | `infra/auth.py` |
| `QUIZ_MOCK_LLM` | 环境变量：=1 时跳过真实 LLM 调用（用于自动化测试） | `quiz_admin.py` → `_get_llm_chat()` |
| 测验 LLM 独立配置 | 测验模块独立于对话路由（默认 qwen-flash）；切换仅系统管理员、切换前探测、留痕 updated_by；业务管理员零感知 | `backend/data/quiz_llm_config.json` / `services/quiz_llm.py` / `POST /api/admin/quiz/llm-config` |
