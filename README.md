# 劳动力调查 AI 助手

为辅助调查员提供基于 RAG 的即时填报指导，载体是微信小程序。

## 当前状态

**迭代 1 / Step 1.1：项目骨架搭建中**

## 快速开始

```bash
# 1. 安装后端依赖
cd backend
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY、BGE_API_KEY 等

# 3. 启动后端
uvicorn app.main:app --reload --port 8000

# 4. 构建知识库
python ../scripts/build_kb.py

# 5. 用微信开发者工具打开 miniprogram/ 目录即可预览小程序
```

## 项目结构

```
labor-survey-ai/
├── docs/                       # 文档（方案、审核、架构、ADR）
├── knowledge-base/             # 知识库（原始素材 + QA + 构建脚本）
├── backend/                    # FastAPI 后端
├── miniprogram/                # 微信小程序前端
├── deploy/                     # 部署配置
└── scripts/                    # 跨子项目运维脚本
```

详细结构说明见 `CLAUDE.md`。

## 技术栈

| 组件 | 选型 |
|------|------|
| 前端 | 微信小程序原生 |
| 后端 | Python FastAPI |
| 向量库 | Chroma（MVP） |
| 大模型 | DeepSeek |
| Embedding | BGE API / DashScope API |
| ASR | 腾讯云 ASR |

详见 `docs/03-架构设计.md` 和 `docs/adr/`。

## 文档索引

- `docs/01-技术方案.md` — 原始技术方案归档
- `docs/02-可行性审核.md` — 可行性审核报告
- `docs/03-架构设计.md` — 详细架构、接口、数据流
- `docs/04-知识库规范.md` — QA 录入模板与分类法
- `docs/adr/` — 架构决策记录