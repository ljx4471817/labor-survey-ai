# 劳动力调查 AI 助手 · 项目约定

> 本文件是项目级 CLAUDE.md，**优先级高于全局 CLAUDE.md**，冲突时以本文件为准（按全局 CLAUDE.md 的指令优先级规则）。
> 全局约定见 `C:\Users\Administrator\.claude\CLAUDE.md`。

## 项目身份

- **名称**：劳动力调查 AI 助手（labor-survey-ai）
- **目标**：为辅助调查员提供基于 RAG 的即时填报指导
- **用户**：国家统计局贵阳调查队系统的辅助调查员（处室自用起步）
- **载体**：微信小程序 + FastAPI 后端

## 用户身份

我是项目开发者，使用 Claude Code 协作。沟通风格遵循全局 CLAUDE.md：中文、结论先行、不谄媚。

## 当前阶段

**迭代 1 / Step 1.1：建项目骨架**（进行中）

参见 `docs/02-可行性审核.md` 第四节「已确认的决策」和 `D:\code\labor-survey-ai\..\plans\c-users-administrator-desktop-ai-md-1-2-velvety-feather.md` 第四节「推荐实施路线」。

## 目录约定

| 目录 | 用途 | 谁能改 |
|------|------|--------|
| `docs/` | 方案、审核、架构、ADR 等静态文档 | 自由修改 |
| `docs/adr/` | 架构决策记录（一旦写定不轻易改） | 增量追加，不改旧 ADR |
| `knowledge-base/raw/` | 原始 PDF/Word 制度文档 | **不直接修改**，只读 |
| `knowledge-base/qa/` | 结构化 QA JSON | 自由修改 |
| `knowledge-base/scripts/` | 知识库构建脚本 | 自由修改 |
| `backend/app/` | FastAPI 应用代码 | 自由修改 |
| `backend/tests/` | 后端测试 | 自由修改 |
| `miniprogram/pages/` | 小程序页面 | 自由修改 |
| `miniprogram/utils/` | 小程序工具函数 | 自由修改 |
| `deploy/` | 部署配置 | 谨慎修改，影响线上 |
| `scripts/` | 跨子项目运维脚本 | 自由修改 |

## 关键命令

> 所有命令在项目根目录 `D:\code\labor-survey-ai\` 下执行。

```bash
# 初始化 codegraph 索引（首次必跑）
codegraph init -i

# 知识库：构建向量索引
python scripts/build_kb.py

# 知识库：本地预览/检索测试
python scripts/preview_kb.py

# 后端：本地启动（开发模式）
cd backend && uvicorn app.main:app --reload --port 8000

# 后端：测试
cd backend && pytest

# 后端：依赖安装
cd backend && pip install -r requirements.txt

# 小程序：本地预览（需微信开发者工具打开 miniprogram/）
# 无 CLI 命令，开发者工具扫码即可
```

## 代码风格

**通用**：遵循全局 CLAUDE.md 的"匹配已有代码风格"原则。

**Python（后端）**：
- 类型注解必加（公共函数）
- 异步优先（FastAPI 是 async 框架）
- 配置从环境变量读，不硬编码
- 公共函数加 docstring（一行说明 WHY）

**JavaScript（小程序）**：
- 微信小程序原生语法，不引入框架（Taro / uni-app）除非必要
- 工具函数放 `utils/`，页面逻辑放 `pages/<page>/` 内
- 不使用 ES6+ 不支持的语法（参考小程序基础库兼容表）

## 合规红线（来自全局 CLAUDE.md）

- 不收集居民个人信息（小程序不接触调查数据）
- 不把 API Key、token 写进代码或 commit
- 修改 `.env`、CI/CD 配置、部署脚本前先问我
- 单位主体备案流程启动前先确认

## 知识库质量标准

知识库是回答质量的决定因素，比代码更重要：

- 每条 QA 必须标注 `source`（制度依据）
- 每条 QA 必须有 `category`（分类用于离线浏览）
- `question` 和 `answer` 用正式书面语，不口语化
- 关键词数组用于离线检索，至少 3 个
- 不确定的答案宁可不录，不要编造

## 待办

参见 `D:\code\labor-survey-ai\..\plans\c-users-administrator-desktop-ai-md-1-2-velvety-feather.md`。