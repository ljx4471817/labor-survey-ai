# 劳动力调查 AI 助手

为辅助调查员提供基于 RAG 的即时填报指导，载体是 H5 单页应用 + FastAPI 后端，通过 Cloudflare Tunnel 暴露公网。

> 2026-06-21 反转：原计划"微信小程序"已改为 H5。详见 `docs/adr/0001-前端选型.md`。
>
> 2026-06-21 补充：语音识别（讯飞 ASR）已停用，依赖现代手机输入法自带语音转写。后端代码完整保留，未来可恢复（步骤见 `.env.example`）。

## 当前状态

**迭代 3 进行中 · 内测服务可用**

- ✅ 后端：FastAPI + chat / feedback / auth / admin 分域 API（voice 2026-06-21 已停用）
- ✅ 前端：H5 四页面（对话 / 登录 / 反馈看板 / 白名单管理）
- ✅ 知识库：Vector + BM25 hybrid 双轨检索，409 条（354 QA + 55 制度 chunk）
- ✅ 鉴权：手机号白名单 + HMAC token（`whitelist.db`，五级区划字段）
- ✅ 运营：反馈看板（KB 优化 + 使用监测双 tab）+ 区域 5 级下钻
- ✅ 内网穿透：Cloudflare Tunnel quick 模式
- ✅ 质量门禁：40 项单元测试 + 102 项 RAG 全量评测
- ⏳ 迭代 3 材料已就绪，待领导决策后启动（域名备案 15-20 工作日）

## 快速开始

```bash
# 1. 安装后端依赖
cd backend
pip install -r requirements.txt
cd ..

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY、DASHSCOPE_API_KEY（XUNFEI_* 2026-06-21 起停用）

# 3. 构建知识库（首次必跑；QA + 制度原文双轨）
python scripts/build_kb.py
python scripts/build_chunks.py --input "knowledge-base/raw/markdown/劳动力调查制度（2026年定期报表）-定稿.md" --full
python scripts/build_bm25.py --full

# 4. 启动后端 + 内网穿透（一键）
scripts\start_tunnel.bat
# 把输出的 https://xxx.trycloudflare.com 链接发给同事即可
```

仅开发后端（不需要公网访问）：

```bash
cd backend
uvicorn app.main:app --reload --port 8001
# 访问 http://localhost:8001/
```

## 项目结构

```
labor-survey-ai/
├── docs/                       # 文档（方案、审核、架构、ADR）
├── knowledge-base/             # 知识库（原始素材 + QA + 构建脚本）
├── backend/
│   ├── app/                    # FastAPI 应用代码
│   │   ├── api/                # chat / feedback / auth / admin 子路由
│   │   ├── core/               # config
│   │   ├── models/schemas/     # 按领域拆分的 Pydantic schemas
│   │   └── rag/                # bm25 / llm / prompts / retriever
│   ├── data/                   # chroma 持久化 + bm25 索引（不入仓）
│   ├── static/                 # H5 前端（单页应用）
│   └── tests/                  # 单元测试（40 tests）
├── scripts/
│   ├── build_kb.py             # 向量库构建
│   ├── build_bm25.py           # BM25 索引构建
│   ├── start_tunnel.bat        # 一键启动后端 + Cloudflare Tunnel
│   └── ...                     # 评估 / 验证 / 修补脚本
└── reports/                    # 评估报告
```

> `miniprogram/` 目录保留为空骨架（决策反转前已建），后续如确认不再用 H5 改回小程序，可重新启用。

## 技术栈

| 组件 | 选型 |
|------|------|
| 前端 | H5 单页应用（原生 HTML/CSS/JS） |
| 后端 | Python FastAPI |
| 向量库 | Chroma |
| 全文检索 | BM25（rank_bm25） |
| LLM | DeepSeek |
| Embedding | DashScope text-embedding-v4 |
| ASR | 讯飞实时语音转写大模型（**2026-06-21 起停用**） |
| 公网暴露 | Cloudflare Tunnel（quick 模式） |

详见 `docs/03-架构设计.md` 和 `docs/adr/`。

## 文档索引

- `docs/01-技术方案.md` — 原始技术方案
- `docs/02-可行性审核.md` — 可行性审核报告
- `docs/03-架构设计.md` — 详细架构、接口、数据流
- `docs/04-知识库规范.md` — QA 录入模板与分类法
- `docs/05-prompt-提取编排.md` — 知识库提取 prompt 设计
- `docs/06-prompt-内容优化.md` — 知识库内容优化 prompt
- `docs/adr/0001-前端选型.md` — H5 替代小程序的决策
- `docs/adr/0002-向量库选型.md` — Chroma 选型
- `docs/adr/0003-embedding-部署方式.md` — Embedding API 选型
- `docs/adr/0004-内网穿透方案.md` — Cloudflare Tunnel 决策
- `docs/adr/0005-手机号白名单门禁.md` — HMAC token + whitelist 鉴权
- `docs/adr/0006-反馈闭环与Dashboard看板.md` — admin API + 看板设计
- `docs/adr/0007-多轮对话上下文.md` — history 字段 + 上下文注入
- `docs/adr/0008-制度对齐机制.md` — indicators 字段 + migration_map.json + regulations-migrate skill
- `docs/adr/0010-embedding-v4.md` — text-embedding-v4 + QA/chunk 共享 collection 重建边界

## 项目级 Codex Skills（`.codex/skills/`，已 git 入仓）

- `kb-update-workflow/` — 5 阶段 KB 入库流程（源文档 → markdown → Q&A 抽取 → 查重 → 审核入库）
- `regulations-migrate/` — 年度《劳动力调查制度》变更 7 步迁移（12 月初触发）
