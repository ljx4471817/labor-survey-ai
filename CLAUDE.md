# 劳动力调查 AI 助手 · 项目约定

> 本文件是项目级 CLAUDE.md，**优先级高于全局 CLAUDE.md**，冲突时以本文件为准（按全局 CLAUDE.md 的指令优先级规则）。
> 全局约定见 `C:\Users\Administrator\.claude\CLAUDE.md`。

## 项目身份

- **名称**：劳动力调查 AI 助手（labor-survey-ai）
- **目标**：为辅助调查员提供基于 RAG 的即时填报指导
- **用户**：国家统计局贵阳调查队系统的辅助调查员（处室自用起步）
- **载体**：H5 单页应用 + FastAPI 后端 + Cloudflare Tunnel 暴露公网

> 2026-06-21 反转：原"微信小程序"已改为 H5，详见 `docs/adr/0001-前端选型.md`。

## 用户身份

我是项目开发者，使用 Claude Code 协作。沟通风格遵循全局 CLAUDE.md：中文、结论先行、不谄媚。

## 当前阶段

**迭代 1 已完成 ✅**：H5 + Cloudflare Tunnel + 视觉升级 + 吉祥物接入 + 推送 GitHub。
**迭代 2 进行中**（2026-06-22 起）：KB 质量优化、采购可行性预算。

参见 `docs/02-可行性审核.md` 第四节「已确认的决策」和 `docs/adr/0004-内网穿透方案.md`。

## 目录约定

| 目录 | 用途 | 谁能改 |
|------|------|--------|
| `docs/` | 方案、审核、架构、ADR 等静态文档 | 自由修改 |
| `docs/adr/` | 架构决策记录（一旦写定不轻易改） | 增量追加，不改旧 ADR |
| `knowledge-base/raw/` | 原始 PDF/Word 制度文档 | **不直接修改**，只读 |
| `knowledge-base/qa/` | 结构化 QA JSON | 自由修改 |
| `backend/app/` | FastAPI 应用代码 | 自由修改 |
| `backend/static/` | H5 前端（单页应用） | 自由修改 |
| `backend/tests/` | 后端测试（**未做**：当前以 `run_eval.py` 端到端验证替代单元测试） | 自由修改 |
| `scripts/` | 跨子项目运维脚本 | 自由修改 |
| `deploy/` | 部署配置（含 ssl/ / systemd/ 占位） | 谨慎修改，影响线上 |

> `miniprogram/` 保留为决策反转前的历史骨架（已 git 追踪 `.gitkeep` 占位），不再修改其内容。

## 关键命令

> 在项目根目录下执行。

```bash
# 初始化 codegraph 索引（首次必跑，之后不用）
codegraph init -i

# 知识库：构建向量索引
python scripts/build_kb.py
# 知识库：构建 BM25 索引（Hybrid 检索用，改 faq.json 后需重建）
python scripts/build_bm25.py --full
# 知识库：QA 字段完整性校验（改 faq.json 后必跑）
python scripts/validate_faq.py
# 知识库：检索评估（BM25 / Vector / Hybrid 对比）
python scripts/run_eval.py
# 知识库：检索方式对比
python scripts/compare_retrieval.py

# 成本预算报告生成（采购可行性 / 领导汇报用）
python scripts/generate_cost_report.py

# 后端：本地启动（开发模式，不需要公网）
cd backend && uvicorn app.main:app --reload --port 8000

# 后端 + 公网穿透：一键启动（H5 + Cloudflare Tunnel）
scripts\start_tunnel.bat
# 输出里找 https://xxx.trycloudflare.com 链接，发给同事

# 后端：测试
cd backend && pytest

# 后端：依赖安装
cd backend && pip install -r requirements.txt
```

## 代码风格

**通用**：遵循全局 CLAUDE.md 的"匹配已有代码风格"原则。

**Python（后端）**：
- 类型注解必加（公共函数）
- 端点用普通 `def`（FastAPI 自动跑线程池；混 `async` 反而需要避免阻塞调用）
- 配置从环境变量读，不硬编码
- 公共函数加 docstring（一行说明 WHY）

**H5 前端（`backend/static/`）**：
- 单文件优先，目前全部内联在 `index.html`
- 工具函数 / API 调用就近写，不强求模块化
- 浏览器原生 API 优先，不引入框架

## 合规红线（来自全局 CLAUDE.md）

- 不收集居民个人信息（H5 不接触调查数据）
- 不把 API Key、token 写进代码或 commit
- 修改 `.env`、CI/CD 配置、部署脚本前先问我
- 单位主体备案流程启动前先确认
- **删除文件/目录/git 历史前先问我**
- **数据库 schema 变更/数据迁移前先问我**

## 知识库质量标准

知识库是回答质量的决定因素，比代码更重要：

- 每条 QA 必须标注 `source`（制度依据）
- 每条 QA 必须有 `category`（分类用于离线浏览）
- `question` 和 `answer` 用正式书面语，不口语化
- 关键词数组用于离线检索，至少 3 个
- 不确定的答案宁可不录，不要编造

**Corner case 处理流程**：遇到「KB 命中但答得不准」或「fallback 兜底」时：
1. 查 `knowledge-base/raw/markdown/` 对应章节原文
2. 按场景拆成独立条目（每条聚焦一个 corner case）
3. 跑 `python scripts/validate_faq.py`（字段完整性）+ `python scripts/build_bm25.py --full`（索引重建）
4. 在 `eval_set.json` 加 eval 锁定（含 must_contain + should_not_contain「未找到」防回归）

**每年 12 月初**：用 `git diff` 对比新旧《劳动力调查制度》文档，列出可能受影响的 KB 条目，业务人员 + 开发人员 review。

## 安全注意

- `.env` 包含真实 API Key（DeepSeek / DashScope；讯飞 2026-06-21 起停用），已被 `.gitignore` 排除
- 任何会话**不打印、不复述 `.env` 真实值**
- 如 .env 内容出现在对话日志里，事后必须轮换所有相关 Key

## 变更日志（重要节点）

- **2026-06-21**：H5 替代微信小程序（ADR 0001）；Cloudflare Tunnel quick 模式落地（ADR 0004）
- **2026-06-21**：关闭讯飞语音识别（输入法自带，代码完整保留，未来可恢复）
- **2026-06-22**：H5 前端视觉升级（墨蓝 + 米白配色 + 消息动画）；接入单位吉祥物「筑小调」（空状态欢迎 + 每条 AI 回复头像）
- **2026-06-22**：项目首次推送到 GitHub：`https://github.com/ljx4471817/labor-survey-ai`
- **2026-06-22**：`/simplify` 性能/质量修复 —— Chroma collection 模块级单例、向量 + BM25 改 `ThreadPoolExecutor` 并发跑、`chat.py` 提取 `_to_source_items` + `REFUSAL_PATTERNS` 提到模块级、`config.py` 提取 `_resolve_path` helper
- **2026-06-22**：成本预算报告 v2（`reports/cost-budget-20260622.md` + docx/pdf）—— 三档用量 × 三档人数，按行政层级测算，含采购建议档 ¥87/月
- **2026-06-22**：F27 corner case KB 补全（commit `b46387b`）—— 5 条 corner case（id 298-302）+ 1 条 eval-101 锁定用户原问

## 待办

- **Stage 1：评估 KB schema v2**——是否在 corner case ≥3 的指标里加 `scenario` 字段分支，先做 1 个指标验证工时与收益
- **Stage 2：成本预算省级档采购准备**——¥87/月档（阿里云 ECS 2核4G + 域名 + 备案），启动域名备案 15-20 工作日
