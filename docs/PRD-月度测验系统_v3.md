# PRD: 劳动力调查月度测验系统（v3 · /goal 可执行版）

> 版本：v3（2026-08-06）｜前置：v2（存在 7 处自相矛盾 + 4 处与代码库不符，见附录 A）
> 本版目标：交给开发后，可直接粘贴「附录 B 任务书」进 /goal 一次性出成果；所有数据模型、API 契约、业务规则、验收标准均为最终口径，开发不得自行发明。

---

## 0. 开发前置确认项（启动 /goal 前需用户点头，避免踩合规红线）

| # | 事项 | 说明 | 是否需确认 |
|---|------|------|-----------|
| C1 | 新建 `backend/data/quiz.db`（全新 SQLite 文件，不迁移/不触碰 whitelist.db 与 query_log.db） | 新增库文件，属新 schema | ✅ 已确认（2026-08-06） |
| C2 | `backend/requirements.txt` 新增 `python-docx` | 已加入 requirements（本机已装 1.2.0） | ✅ 已确认（2026-08-06） |
| C3 | 测验管理权限 = 白名单 `admin_level ∈ {市级, 省级}`（不改 whitelist schema，不加 role 字段） | 现有数据：市级 7 人 / 区县 25 人 / 调查员 181 人 | ✅ 已确认（2026-08-06） |
| C4 | 过期规则统一为「过期锁定，历史可查」（v2 自相矛盾，见 A1） | 与 v2 Out of Scope #2「过期锁定」一致 | ✅ 已确认（2026-08-06） |

> 四项已于 2026-08-06 由用户确认，/goal 可直接跑，不再需要中途确认。

---

## 1. 产品概述

### 1.1 背景
劳动力调查 AI 知识库（labor-survey-ai）已解决「填报时遇到问题可检索解答」。但有两类信息尚未有效同步到基层调查员：
1. **新上岗调查员**需要掌握的基础知识（已有线下培训 + KB，本期不做）
2. **月度工作提示**中的动态信息（审核要点、季节性关注点、填报口径微调）

### 1.2 核心定位
**不是**培训系统，**不是**知识库，而是「信息内化器」——把月度工作提示中需要调查员「记住并应用」的内容，通过 quiz 形式内化到头脑里。

### 1.3 目标（可测量）
- 管理员可随时创建测验（当前使用场景为一月一次，但系统支持同月多套题）
- 每套测验 ≤ 7 道题，目标人群在有效期内完成（默认 7 天）
- 通过「做题 → 看解析 → 关联 KB」实现内化
- 管理员可追踪完成率（分母 = 选中人数）

---

## 2. 用户角色与权限落点

| 角色 | 描述 | 系统权限 | 落点（复用现有白名单字段） |
|------|------|---------|--------------------------|
| **管理员** | 你自己（市级） | 可访问 `/quiz-admin` 全部管理功能 | `admin_level ∈ {市级, 省级}`；其余返回 403 |
| **调查员** | 社区工作人员（兼职） | 可答题、看解析、查历史 | `admin_level = 调查员` 且手机号在目标名单内 |
| **区县管理者** | 中间层 | **不进系统管理端**，仅口头督促 | `admin_level = 区县`：视为普通用户，不显示管理入口、无管理权限 |

- 现有 `/api/admin/*` 其它模块（feedback/usage/gaps/whitelist）**维持现状**（仅要求登录），本次只给 quiz 管理路由加 `require_admin`，不扩大改动面。

---

## 3. 功能需求

### 3.1 管理端（quiz_admin.html）

#### 3.1.1 导入月度通知
- 入口：管理页「新建测验」→ 选择月份（必填，格式 `YYYY-MM`）→ 上传 `.doc/.docx/.wps`（≤10MB）
- 系统登记导入记录（month/filename/size），docx 暂存 `backend/data/quizzes/tmp/{import_id}.docx`（提取成功后删除）
- 规则：该月**已有 published 测验** → 拒绝（409「该月已下发，不可重复导入」）；该月处于 draft/reviewing → 允许覆盖（先清空旧 draft 要点与题目再重建）
- 输出：`import_id` + 提示「请执行提取」

#### 3.1.2 LLM 提取要点（异步）
- 后台线程：python-docx 读文本 → 按段落切分、识别章节（审核要点/问卷要点/填报口径微调/其它）→ 调 LLM Prompt1 提取要点 → 逐要点做 KB 关联
- 输出：结构化要点列表，每点含 `section / content / common_error / source_quote（通知原文段落） / suggest_quiz / kb_ref / kb_match_status`
- 前端轮询任务状态（每 3s，最长 120s）

#### 3.1.3 审核要点
- 管理员查看要点列表（按 section 分组），操作：确认 / 拒绝 / 编辑（含改 content、common_error、source_quote、suggest_quiz、手动补/改 KB 关联）
- KB 匹配失败（cosine < 0.6）：显示「找不到对应 KB 条目」，管理员可手动从 faq 搜索框选关联，或跳过

#### 3.1.4 生成与审核题目（异步）
- 管理员勾选要点（建议出题）→ 调 LLM Prompt2 逐要点生成 4 选 1 题（题干 + 4 选项 + 答案 + 解析）
- 生成数量上限 = 每套 7 题；超过时只生成前 7 个要点对应题目，前端提示「已达 7 题上限」
- 管理员预览、编辑（题干/选项/答案/解析）、确认或打回；题目 `approved` 后才可下发

#### 3.1.5 选择下发对象并下发
- 展示白名单中 `admin_level = 调查员` 的活跃用户，按 市 → 区县 分组（county 为空归「未分区」），支持：
  - 按区县全选/取消、单人勾选/取消、搜索（姓名/手机号）、已选人数计数（如「已选 12/181 人」）、默认全不选
- 设置有效期（默认 `now + 7 天`，可改）
- 点击「确认下发」：写目标名单 + `valid_from=now` + `valid_until=指定值`，quiz 状态 → published
- 下发后可操作：
  - **追加**：新增目标（union，已答用户自然保留）
  - **移除**：仅允许移除**未作答**用户；已答用户不可移除（保护记录）
- **过期规则（本版最终口径）**：超过 `valid_until` → quiz 自动进入 expired，**不可再作答**（锁定），历史记录保留可查。首页不再展示该测验。

#### 3.1.6 完成率看板
- 指标（口径固定）：
  - `total_users` = 选中人数（含追加，分母）
  - `started` = 至少答 1 题的人数
  - `completed` = 有效期内全部答完的人数（分子）
  - `completion_rate` = completed / total_users
  - `score` = 有效期内答对题数
- 支持按 市/区县 筛选；个人明细表：姓名/手机号/区县/状态（未开始/进行中/已完成）/得分/完成时间/最后答题时间

### 3.2 调查员端（H5）

> **可见性规则（硬约束）**：`GET /api/quiz/current` 只返回「该用户是目标 且 quiz 为 published 且未过期」的测验；否则 `items: []`。首页入口据此显隐。**过期测验永远不在首页出现**。

#### 3.2.1 首页双入口（index.html）
- 现有「📚 AI 知识库」卡片下方新增「📝 本月测验」卡片
- 卡片内容：标题 + 徽标（`已答 X/总数`）+ 副标题（`剩余 N 天` / `已完成` / `进行中`）
- 非目标用户 / 无测验：不渲染该卡片

#### 3.2.2 测验页（quiz.html）
- 题目列表（顺序 = seq 固定，不做随机），显示进度 `已答/总数`
- 单题：题干 + 4 选项 → 提交 → 即时反馈
  - 反馈：✓正确 / ✗错误、正确答案、解析（引用通知原文 source_quote + 「相关知识点：KB 第 X 条」链接）、`已答 X/总数`、全部答完显示「本月 X 题，你掌握了 Y 题」
- **单题提交即锁定，不可修改**（防改答案刷分）
- 已答题重进页面：显示上次选择 + 对错 + 解析（复习用）

#### 3.2.3 个人记录
- 历史测验列表（按月/套），每题明细（我的选择/正确答案/解析/KB 关联），过期测验仍可查看

---

## 4. 用户故事

### 4.1 管理员
1. 作为管理员，我每月收到上级下发的月度工作提示 docx 后，希望能选择月份并上传，让 LLM 自动提取可出题要点，这样我不用手动阅读整篇通知逐条提炼。
2. 作为管理员，我希望看到 LLM 提取的要点列表（含来源段落、常见错误、KB 关联），并能逐条确认/拒绝/编辑，这样我可以纠正 LLM 误提取。
3. 作为管理员，我希望系统自动为每个要点关联 KB 条目（阈值 0.6），匹配失败时我能手动搜索补关联或跳过，这样解析里能给出制度依据。
4. 作为管理员，我希望确认要点后 LLM 能自动生成 4 选 1 选择题（题干+4 选项+答案+解析，≤7 题/套），这样我不用逐题手写。
5. 作为管理员，我希望预览并编辑 LLM 生成的题目（题干/选项/答案/解析），确认后题目才进入可下发状态，这样我能兜底题目质量。
6. 作为管理员，我希望一键下发测验、设置有效期（默认 7 天）、按 市/区县/单人 勾选目标，这样我能按区县差异化下发。
7. 作为管理员，我希望下发后能追加目标、移除未作答用户，这样我能临时补人且不破坏已答记录。
8. 作为管理员，我希望看到完成率看板（总人数/已做/已完成/完成率，可按地区筛选，含个人明细），这样我能针对性督促未完成者。
9. 作为管理员，我希望过期的测验自动锁定、历史保留，这样「限时完成」才有约束力。
10. 作为管理员，我希望同一月可以针对不同群体出多套题（互不干扰），这样区县差异化的月度提示能分开下发。

### 4.2 调查员
11. 作为调查员，我打开 H5 首页后，若我被选中，希望看到「本月测验」入口（含剩余天数和完成状态），这样我知道有任务要做。
12. 作为调查员，我希望在碎片时间逐题作答，每题提交后立即看到对错和解析，这样我不用等全部做完才知道结果。
13. 作为调查员，答错时我希望解析引用通知原文，并提示「相关知识点：KB 第 X 条」可点开看制度依据，这样我错了知道去哪查。
14. 作为调查员，全部做完后我希望看到总结「本月 X 题，你掌握了 Y 题」，这样我知道掌握程度。
15. 作为调查员，我希望查看历史测验记录（按月/套，每题我的选择/正确答案/解析），这样我可以复习。
16. 作为调查员，即使测验已过期，我仍可查看历史答题记录。

---

## 5. 数据模型（SQLite：新建 backend/data/quiz.db）

> v2 的「JSON 文件按月存储」废弃：与 5.7 联合索引矛盾、100 并发下无锁不可靠、统计聚合低效；改用项目既有 SQLite persistence 模式（参照 `persistence/whitelist_db.py` / `query_log.py`：`_SCHEMA` + `_MIGRATIONS` + 懒连接）。

### 5.1 DDL（开发按此建表，可加索引但不可改语义）

```sql
CREATE TABLE IF NOT EXISTS quizzes (
    id          TEXT PRIMARY KEY,                -- 'Q20260801'（月+序号，同一月多套）
    month       TEXT NOT NULL,                   -- '2026-08'
    title       TEXT NOT NULL,                   -- 如 '2026年8月劳动力调查月度测验'
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft/reviewing/published/expired/archived
    valid_from  TEXT,                            -- ISO8601 UTC+8
    valid_until TEXT,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quizzes_month ON quizzes(month);

CREATE TABLE IF NOT EXISTS imports (
    id              TEXT PRIMARY KEY,            -- 'IMP20260801'
    month           TEXT NOT NULL,
    filename        TEXT NOT NULL,
    file_size       INTEGER NOT NULL,
    status          TEXT NOT NULL DEFAULT 'extracted',  -- imported/extracted/error
    raw_text_length INTEGER,
    extracted_by    TEXT NOT NULL,
    extracted_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS keypoints (
    id              TEXT PRIMARY KEY,            -- 'KP20260801'
    quiz_id         TEXT NOT NULL,
    section         TEXT NOT NULL,               -- 审核要点/问卷要点/填报口径微调/其它
    content         TEXT NOT NULL,
    common_error    TEXT DEFAULT '',
    source_quote    TEXT DEFAULT '',             -- 通知原文段落（Prompt2 出题依据 + 解析引用）
    suggest_quiz    INTEGER NOT NULL DEFAULT 1,
    kb_faq_id       TEXT,
    kb_question     TEXT DEFAULT '',
    kb_score        REAL,
    kb_match_status TEXT NOT NULL DEFAULT 'unmatched', -- matched/unmatched/manual
    status          TEXT NOT NULL DEFAULT 'draft',     -- draft/approved/rejected
    reviewed_by     TEXT,
    reviewed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_keypoints_quiz ON keypoints(quiz_id);

CREATE TABLE IF NOT EXISTS questions (
    id          TEXT PRIMARY KEY,                -- 'Q20260801Q1'
    quiz_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,                -- 1..7，固定顺序
    question    TEXT NOT NULL,
    options     TEXT NOT NULL,                   -- JSON {"A":..,"B":..,"C":..,"D":..}
    answer      TEXT NOT NULL,                   -- A/B/C/D
    explanation TEXT NOT NULL,
    source_quote TEXT DEFAULT '',
    kb_faq_id   TEXT,
    kb_question TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',   -- draft/reviewing/approved
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_quiz ON questions(quiz_id);

CREATE TABLE IF NOT EXISTS targets (
    quiz_id  TEXT NOT NULL,
    phone    TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (quiz_id, phone)
);

CREATE TABLE IF NOT EXISTS answers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id      TEXT NOT NULL,
    phone        TEXT NOT NULL,
    q_id         TEXT NOT NULL,
    selected     TEXT NOT NULL,
    correct      INTEGER NOT NULL,
    ts           TEXT NOT NULL,                  -- 答题时间（用于「最后答题时间」）
    UNIQUE(quiz_id, phone, q_id)                 -- 单题唯一：提交即锁定
);
CREATE INDEX IF NOT EXISTS idx_answers_user ON answers(quiz_id, phone);
CREATE INDEX IF NOT EXISTS idx_answers_phone ON answers(phone);
```

### 5.2 关键语义
- **quiz（套）** 是下发与统计的最小单位；`month` 只用于「本月」筛选，不唯一。
- **completed** = 有效期内该 quiz 全部 questions 均有 answers 记录；score = 其中 correct=1 的数量。
- **expired 推导**：读取时按 `now > valid_until` 计算（无需定时任务）；`archived` 由清理任务落库。
- **清理（12 个月）**：启动时 + 每次管理端打开统计页时懒执行：删除 `valid_until < now - 12个月` 的 quiz 的 answers/targets/questions/keypoints/imports 记录，quiz 置 archived。
- **timezone**：一律 UTC+8（ISO8601，参照现有 `_now()` 写法）。

---

## 6. 技术方案

### 6.1 文件布局（新增文件，不改旧文件；沿用 AGENTS.md 分层）

```
backend/
├── app/
│   ├── api/
│   │   ├── quiz.py              # 调查员端：current/submit/history/faq
│   │   └── quiz_admin.py        # 管理端：import/extract/keypoints/generate/questions/publish/targets/stats
│   ├── services/
│   │   ├── quiz_generator.py    # 出题编排 + 纯函数（JSON 解析、schema 校验、判分、状态推导）
│   │   └── quiz_extract.py      # docx 文本提取 + 章节识别 + Prompt1 编排 + 纯函数
│   ├── persistence/
│   │   └── quiz_db.py           # quiz.db 持久化（参照 whitelist_db.py 模式）
│   ├── models/schemas/
│   │   ├── quiz.py              # 调查员端请求/响应模型
│   │   └── admin.py             # 追加 quiz 管理端模型（或新增 quiz_admin.py）
│   ├── infra/auth.py            # 追加 require_admin（get_user().admin_level ∈ {市级,省级}）
│   └── core/constants.py        # 追加 quiz 状态/章节枚举常量（不放魔法字符串）
├── static/
│   ├── quiz.html                # 调查员测验页
│   ├── quiz_admin.html          # 管理端
│   └── index.html               # 首页加「本月测验」卡片（改动此文件）
└── data/
    └── quizzes/tmp/             # docx 暂存（提取后删除）
```

### 6.2 API 契约

> **响应风格**：沿用代码库现状（plain dict / Pydantic response_model + HTTPException），**不引入** `{success,data,error,pagination}` 信封（与现有 chat/feedback/whitelist 保持一致）。

#### 调查员端（`require_user`，token 解析 phone）
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/quiz/current` | GET | 当前可见测验列表（target 且 published 且未过期）；无则 `{"items": []}` |
| `/api/quiz/submit` | POST | 提交单题答案（见下） |
| `/api/quiz/history` | GET | 个人历史测验列表（分页） |
| `/api/quiz/history/{quiz_id}` | GET | 单套测验逐题明细（含解析/KB） |
| `/api/faq/{faq_id}` | GET | 读 `knowledge-base/qa/faq.json` 单条（question/answer/source/category/keywords） |

**`GET /api/quiz/current` 响应**（每题已答才带 answer/explanation/kb_ref，未答不泄露答案）：
```json
{"items": [{
  "quiz_id": "Q20260801", "month": "2026-08", "title": "...",
  "status": "published", "valid_until": "2026-08-15T23:59:59+08:00",
  "remaining_days": 3, "total": 7, "answered": 3, "completed": false,
  "questions": [{
    "id": "Q20260801Q1", "seq": 1,
    "question": "...", "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
    "answered": "C", "correct": true,
    "answer": "C", "explanation": "...",
    "source_quote": "...", "kb_ref": {"faq_id": "023", "question": "..."}
  }]
}]}
```

**`POST /api/quiz/submit`** 请求：`{"quiz_id": "Q20260801", "q_id": "Q20260801Q1", "selected": "C"}`
校验顺序（任一不过抛错）：401 未登录 → 404 quiz 不存在 → 403 非目标用户 → 409 已过期/已锁定（`valid_until` 已过 或 该题已答）→ 422 selected 不在选项键内。
响应：`{"correct": true, "answer": "C", "explanation": "...", "source_quote": "...", "kb_ref": {...}, "answered": 4, "total": 7, "completed": false}`

**`GET /api/quiz/history`** 响应：`{"items": [{"quiz_id","month","title","total","answered","score","completed","status","submitted_at"}], "total": N, "page": 1, "page_size": 10}`
（`submitted_at` = 最后一道题答题时间；未完成的答题不计 score 完成态）

**错误码（最终口径）**：400 参数错误 / 401 未登录 / 403 无权限或非目标 / 404 不存在 / 409 冲突（已过期、已锁定、该月已下发）/ 422 请求体校验失败（Pydantic 默认，勿用于业务错误）/ 500 内部。**删除 v2 的「422 业务逻辑错误」用法。**

#### 管理端（`require_user` + `require_admin`）
| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/admin/quiz/import` | POST | multipart：`month` + `file`(docx≤10MB) → `{"import_id": "IMP20260801"}`；该月已 published → 409 |
| `/api/admin/quiz/extract` | POST | `{"import_id": "..."}` → `{"task_id": "TASK001", "status": "processing"}`（后台线程执行） |
| `/api/admin/quiz/extract/status/{task_id}` | GET | `{"status": "processing|done|error", "quiz_id": "Q20260801", "keypoints": 12, "error": null}` |
| `/api/admin/quiz/keypoints` | GET | `?quiz_id=` → 按 section 分组的要点列表 |
| `/api/admin/quiz/keypoint/review` | POST | `{"keypoint_id", "action": "approve|reject|edit", "edits": {...}}` |
| `/api/admin/quiz/generate` | POST | `{"quiz_id", "keypoint_ids": [...]}` → `{"task_id"}`（后台生成，≤7 题） |
| `/api/admin/quiz/generate/status/{task_id}` | GET | 同上模式 |
| `/api/admin/quiz/questions` | GET | `?quiz_id=` → 题目列表 |
| `/api/admin/quiz/question/review` | POST | `{"question_id", "action": "approve|reject|edit", "edits": {...}}` |
| `/api/admin/quiz/publish` | POST | `{"quiz_id", "valid_until", "targets": [phones], "action": "publish|append|remove"}`；publish 要求题目全部 approved 且 ≥1；remove 不允许移除已答用户 |
| `/api/admin/quiz/targets` | GET | `?q=` 可选搜索 → 调查员分组列表（市→区县，county 空归「未分区」） |
| `/api/admin/quiz/stats` | GET | `?quiz_id=&region=` → 统计（见 3.1.6 口径） |

### 6.3 权限模型
- `require_admin`（新增到 `infra/auth.py`）：`require_user` 拿 phone → `get_current_user(phone)` → `admin_level ∈ {"市级","省级"}` 放行，否则 403「无管理员权限」；用户被软删（get_user 返回 None）→ 401。
- 调查员隔离：submit/history 强制用 token 中的 phone，不接受 body 传 phone。

### 6.4 文件上传与存储
- 仅 `.doc/.docx/.wps`，≤10MB；`.docx` 用 python-docx，`.doc/.wps` 用本机 Word/WPS COM 转文本（缺 Word/WPS 时提示转存 .docx）。
- 上传 → 存 `backend/data/quizzes/tmp/{import_id}.docx` → extract 读取 → 成功后删除。**v2「纯内存不落盘」不可行**（import 与 extract 是两次请求，内存不跨请求）。
- 异步任务：进程内 dict `{task_id: {status, result, error}}` + `threading.Thread`；单进程 uvicorn 假设（现有部署即单进程 + Cloudflare Tunnel）。任务表不做持久化，重启丢失可接受（管理员重新触发）。
- 超时：extract 总时长 >120s → status=error「提取超时，请重试」。

### 6.5 LLM 调用与 JSON 解析（复用 `rag/llm.chat`）
- Prompt1 / Prompt2 模板沿用 v2（见附录 C），唯一补充：Prompt 末尾强制「只输出 JSON，不要 markdown 代码块」。
- **JSON 解析（纯函数，必须单测）**：去 ```` ```json ... ``` ```` 围栏 → `json.loads` → schema 校验（字段齐全、options 恰 4 键 A-D、answer ∈ 键）→ 失败用「仅输出合法 JSON」提示重试（最多 2 次）→ 仍失败 task status=error「LLM 输出非法 JSON」。
- 每次调用 `llm.chat(messages, temperature=0.3, max_tokens=2000)`，单次 timeout 60s（现有实现）；extract 后台线程整体 120s 上限。
- **防串题/质量约束**：生成题目时逐要点独立调用，题干必须情境化、干扰项必须来自 common_error、解析必须引用 source_quote 原文、答案唯一；`approved` 前一律不进下发。

### 6.6 KB 关联机制（复用现有检索，修正 v2 阈值语义）
- 用 `rag/retriever.py` 的**向量 cosine 通道**（`_exact_vector_search` 或 direct-hit 通道），**不是 RRF 融合分数**（rrf_score 数值很小，0.6 阈值不成立）。
- 规则：要点 content 作 query → top-1 的 `doc_type == 'qa'` 且 cosine ≥ 0.6 → `kb_match_status=matched`，写入 `kb_faq_id/kb_question/kb_score`；否则 `unmatched`。
- `faq_id` 与 Chroma doc id 一致（`str(id).zfill(3)`，如 `023`），可直接用于「KB 第 X 条」链接。
- 匹配失败：管理端显示「找不到对应 KB 条目」+ 手动搜索 faq.json 补关联（`/api/admin/quiz/kb/search?q=` 新增，登录+admin）或跳过该要点。

### 6.7 前端
- `main.py` 新增路由：`GET /quiz` → quiz.html；`GET /quiz-admin` → quiz_admin.html（参照现有 `_serve_static_page`）。
- `index.html`：AI 知识库卡片下加测验卡片；进入页面调 `/api/quiz/current`，`items` 非空才渲染；徽标状态机：未答完→`已答 X/总数 · 剩余 N 天`；答完→`已完成`。
- `quiz.html`：答题/反馈/历史三视图；「相关知识点：KB 第 X 条」渲染为链接，点击调 `/api/faq/{faq_id}` 弹层展示 question/answer/source（faq_id 为空不渲染链接）。
- `quiz_admin.html`：五步向导（导入 → 提取[轮询] → 要点审核 → 生成[轮询] → 题目审核 → 下发 → 统计看板）；`dashboard.html` 的 tab-bar 加「测验管理」tab 链接到 `/quiz-admin`。
- 全部沿用 `common.js` 的 token 管理（`authHeader()` / `requireLoginOrRedirect()` / `handle401()`）；管理端页面顶部额外探测 `admin_level`（调 `/api/admin/quiz/targets` 或专用 `GET /api/admin/quiz/whoami` 返回 role），非管理员提示无权限并跳首页。

---

## 7. 非功能需求

| 维度 | 要求 |
|------|------|
| 性能 | 答题提交 P95 < 500ms（本地压测）；LLM 提取/出题总时长 ≤120s（后台任务） |
| 兼容 | iOS 12+ / Android 7+；微信内置浏览器优先（沿用现有 H5 约束） |
| 安全 | 手机号登录（现有）；quiz 管理端 `require_admin`（admin_level 市级/省级）；答题数据仅本人可查 |
| 数据 | 答题记录保留 12 个月，到期自动清理（懒清理：启动 + 打开统计页时） |
| 并发 | 支持 100 人同时答题（SQLite WAL + 单题 upsert；压测验证无 500） |
| 可用 | 测验有效期内可用性 >99%（沿用现有部署，无新增基础设施） |

---

## 8. 里程碑（按此顺序，逐段验收）

| 阶段 | 内容 | 产出/验收 |
|------|------|----------|
| **M1** | 数据层 + 权限：`persistence/quiz_db.py`（建表/CRUD/清理）、`infra/auth.py` 加 `require_admin`、`core/constants.py` 枚举 | `pytest` 新单测绿：quiz_db CRUD、清理、require_admin（mock 用户） |
| **M2** | 纯函数 + 服务：`quiz_extract.py`（docx 文本提取/章节识别/Prompt1/JSON 解析）、`quiz_generator.py`（Prompt2/判分/状态推导/KB 匹配阈值） | 单测绿：JSON 解析、判分、状态机、KB 阈值（mock LLM + mock 检索） |
| **M3** | 调查员端 API + 页面：`quiz.py`、`/api/faq/{id}`、`quiz.html`、`index.html` 卡片 | 链路：登录 → 见卡片 → 答题 → 反馈 → 历史；非目标用户 items=[] |
| **M4** | 管理端 API + 页面：`quiz_admin.py`、`quiz_admin.html`、dashboard 入口 | 链路：导入 8 月 docx → 提取 → 要点审核 → 生成 → 题目审核 → 下发 → 统计（mock LLM） |
| **M5** | 联调 + 实测：真 LLM 跑 8 月通知全流程 + 100 并发压测 + eval 回归 | `pytest tests/ -q` 全绿 + `python scripts/run_eval.py --phone 13985000001` 全绿 + 压测报告 |

---

## 9. 风险与对策

| 风险 | 对策 |
|------|------|
| LLM 提取要点不准 | 人工审核环节兜底（要点/题目双审核） |
| 题目质量不稳定 | 前 3 个月人工逐题审核；生成数量 ≤7/套；解析必须引用原文 |
| LLM 输出非法 JSON | 围栏剥离 + 重试 2 次 + 任务报错可重跑 |
| LLM 调用超时 | 后台线程 + 120s 上限 + 前端轮询 + 可重试 |
| KB 匹配失败 | 显示「找不到对应 KB 条目」+ 手动搜索补关联 + 可跳过 |
| 调查员参与度低 | 区县管理者口头督促 + 完成率看板（地区下钻） |
| 并发写冲突 | SQLite WAL + 单题幂等 upsert（UNIQUE 约束） |
| 误改既有白名单 | 本功能**不改** whitelist schema（C3），只读 admin_level |

---

## 10. 测试策略

### 10.1 单测（新增 `backend/tests/test_quiz_*.py`，参照现有纯函数测试风格 `test_rrf_fuse.py` / `test_merge_query.py`）
- `quiz_generator.py`：Prompt 构建、JSON 围栏剥离与解析、schema 校验、重试逻辑（mock `llm.chat` 返回合法/非法/带围栏 JSON）、判分（correct 累计）、状态推导（expired 边界）。
- `quiz_extract.py`：docx 文本提取（固定测试 docx）、章节识别（审核要点/问卷要点等关键词）、要点结构化（mock LLM）。
- `quiz_db.py`：建表/upsert/查询/统计/12 个月清理（tmp 库，fixture 参照 `conftest.py`）。
- `infra/auth.py`：`require_admin` 权限矩阵（市级放行 / 区县 403 / 调查员 403 / 软删 401）。
- API：`quiz.py` / `quiz_admin.py`（mock 数据层）：可见性规则（非目标 items=[]）、提交锁定、409 过期、publish 校验、append/remove 规则、统计口径。

### 10.2 集成/E2E
- 出题链路：上传固定测试 docx → 提取 → 要点审核 → 生成 → 题目审核 → 下发 → 统计（mock LLM）。
- 答题链路：获取测验 → 提交 → 反馈 → 历史。
- 真 LLM 冒烟：用 8 月通知实测一轮（M5）。

### 10.3 测试数据
- 固定内容测试 docx（含完整问卷要点/审核要点/填报口径/常见错误）。
- 边界 docx（空内容/超长/无结构标题）。
- mock LLM 返回固定 JSON；mock 检索返回固定 cosine。

### 10.4 测试数量基线
- 新增单测 ≥ 20 个；`pytest tests/ -q` 全绿；`python scripts/run_eval.py --phone 13985000001` 全绿。

---

## 11. Out of Scope（本期不做）
1. 系统推送通知（区县管理者口头督促）
2. **错题重练 / 过期补做**：限时模式，过期锁定，历史可查（v2 表述矛盾，本版统一，见 A1）
3. 多人管理员权限分级（单管理员，admin_level 市级/省级）
4. 题目随机顺序（固定 seq）
5. 单题倒计时（只限月度总有效期）
6. 成绩排名
7. 新人培训
8. 多语言（仅中文）
9. 离线答题（必须在线）
10. 文件版本管理（同月 draft 覆盖重建；已 published 拒绝）

---

## 12. 验收标准（防作弊、可判过）

1. `cd backend && pytest tests/ -q` 全绿，新增 quiz 单测 ≥20。
2. `python scripts/run_eval.py --phone 13985000001` 全绿（既有 KB 检索不回归）。
3. 手工链路（真 LLM，8 月通知）：导入 → 提取 → 审核 → 生成（≤7 题）→ 审核 → 下发 45 人 → 调查员答题 → 看板 completion_rate 正确（分母=45）。
4. 非目标用户 `GET /api/quiz/current` → `{"items": []}`；已答用户 remove 被拒（409/400）。
5. 过期后 submit → 409；历史仍可查。
6. 100 并发 submit 压测无 500、P95 < 500ms。
7. 不修改 `whitelist.db` schema、不触碰 `query_log.db`；`requirements.txt` 仅新增 `python-docx`（.doc/.wps 的 COM 转换依赖本机 Word/WPS + pywin32，可选）。

---

## 附录 A：v2 → v3 决策变更记录（审查发现的问题与修正）

| # | v2 问题（严重度） | v3 修正 |
|---|------------------|--------|
| A1 | 过期规则三处矛盾：3.1.4「过期不计分」/ Out of Scope#2「过期锁定」/ 决策表「可答但不提示」/ 用户故事10「可答」（P0） | 统一为**过期锁定、历史可查**（见 C4，需确认） |
| A2 | 存储自相矛盾：5.6 JSON 文件 vs 5.7 联合索引；100 并发 + 碎片化逐题写入不可靠（P0） | 新建 `backend/data/quiz.db`（SQLite，参照现有 persistence 模式） |
| A3 | 权限模型冲突：v2「whitelist.db 增加 role 字段」= 既有库 schema 迁移（踩合规红线）；且已有 admin_level（区县25/市级7/调查员181）（P0） | 复用 `admin_level ∈ {市级,省级}` 作 quiz 管理权限，不改 schema |
| A4 | 多套题并存 vs 按月唯一存储冲突（P0） | 引入 quiz（套）实体，month 仅筛选 |
| A5 | 统一 envelope 与代码库现状不符（现有全部 plain dict/Pydantic）（P0） | 沿用现有响应风格，删信封 |
| A6 | KB 阈值 0.6 套在 RRF 融合分数上不成立（P0） | 明确用向量 cosine 通道判定 |
| A7 | keypoint 模型缺 source_quote，Prompt2 无出处（P0） | keypoints 表加 source_quote |
| A8 | LLM JSON 解析/重试未定义；现有 llm.chat 无 json_mode（P0） | 定义围栏剥离 + 校验 + 重试 2 次 + 报错 |
| A9 | 异步提取只有 POST 无状态查询端点；「内存处理不落盘」跨请求不成立（P0） | 补 status 端点 + docx 暂存 tmp 目录 |
| A10 | python-docx 不在 requirements.txt（P0） | 新增依赖（C2） |
| A11 | 「KB 第 X 条」可点击无落点（无 FAQ 单条接口/详情页）（P1） | 新增 `GET /api/faq/{faq_id}` + quiz.html 弹层 |
| A12 | 碎片化答题进度持久化与「提交即锁定」未定义（P1） | answers 逐题 upsert + UNIQUE 锁定 |
| A13 | 统计口径未定义（进行中算不算已做、完成率分子）（P1） | 未开始/进行中/已完成三态；完成率=completed/选中 |
| A14 | 同月重复上传/已 published 再导入规则未定义（P1） | draft 覆盖、published 409 |
| A15 | 下发后追加/移除 API 语义未定义（P1） | publish action=publish/append/remove；已答不可移除 |
| A16 | 文件格式矛盾：3.1.1「Word/docx」vs 6.4「.docx only」（P1） | 统一 .docx only |
| A17 | 422 业务错误码与 FastAPI 校验语义冲突（P1） | 业务冲突用 400/404/409；422 留给 Pydantic |
| A18 | 管理端入口未定义（P1） | main.py 路由 + dashboard tab 入口 |
| A19 | 下发对象范围未明确（P1） | 仅 `admin_level=调查员` 可选 |
| A20 | 完成率按地区筛选粒度未明确（P1） | 市→区县两级 |
| A21 | 12 个月自动删除机制未定义（P1） | 启动 + 打开统计页懒清理 |
| A22 | 测试策略缺数据层/权限/状态机测试与数量基线（P1） | 补 10.1/10.4 |
| A23 | 区县管理者角色无权限落点（P2） | 明确不进管理端（见 2） |
| A24 | 章节识别限定三类 vs 通知实际章节名（P2） | 允许 section 原样输出，前端按已知类分组 |

## 附录 B：/goal 任务书（≤4000 字，直接粘贴）

```
# 目标：实现「劳动力调查月度测验系统」（labor-survey-ai）
## 范围
在 D:\code_codex\labor-survey-ai 实现 PRD v3（docs/PRD-月度测验系统_v3.md）。只新增文件 + 修改
index.html / dashboard.html / requirements.txt / main.py / infra/auth.py / core/constants.py，
不改旧业务文件；不 push；不提交 .env。

## 实测基准（先读代码，禁止臆造）
- 白名单 backend/data/whitelist.db：字段 admin_level（区县25/市级7/调查员181，无省级），
  区域 city→county，county 可能为空。测验目标=admin_level='调查员' 且 active=1。
- 现有 API 风格：plain dict / Pydantic response_model + HTTPException（中文 detail），无信封。
- 现有检索 rag/retriever.py：retrieve() 的 score 语义=cosine(direct-hit) 或 rrf_score（不可用于 0.6 阈值）；
  KB 匹配必须走向量 cosine≥0.6；faq id = str(id).zfill(3)（如 '023'）。
- LLM：app/rag/llm.py chat()（timeout 60s，无 json_mode）→ JSON 解析必须围栏剥离+校验+重试2次。
- requirements.txt 无 python-docx → 已确认新增（本机已装 python-docx 1.2.0），直接使用。

## 白名单地界（禁止越界）
- 不改 whitelist.db / query_log.db 的 schema 与数据；quiz 数据放新建 backend/data/quiz.db。
- 管理权限=admin_level∈{市级,省级}，不加 role 字段。
- 同月已 published 拒绝重复导入（409）；draft 可覆盖。
- 过期=锁定不可作答（submit 409），历史可查；完成率=有效期内全部答完/选中人数。
- 单题提交即锁定（answers UNIQUE(quiz_id,phone,q_id)），不可改答案。

## 交付物（全部完成后才算完）
1. backend/app/persistence/quiz_db.py + api/quiz.py + api/quiz_admin.py + services/quiz_generator.py
   + services/quiz_extract.py + models/schemas/quiz.py(+admin 追加) + infra/auth.py 加 require_admin
   + core/constants.py 枚举；static/quiz.html + static/quiz_admin.html + index.html 测验卡片
   + dashboard.html 入口 + main.py 路由（/quiz、/quiz-admin）。
2. 后端单测新增 ≥20 个（backend/tests/test_quiz_*.py）：JSON 解析、判分、状态机、KB 阈值、
   quiz_db CRUD/清理、require_admin 权限矩阵、API 可见性与锁定/409/统计口径（mock LLM+检索+数据层）。
3. 验收全绿：cd backend; pytest tests/ -q；python scripts/run_eval.py --phone 13985000001。
4. 手工冒烟（真 LLM 或 mock）：导入 8 月文档(.doc/.docx/.wps)→提取→要点审核→生成≤7题→题目审核→下发→答题→看板
   completion_rate（分母=选中人数）；非目标用户 current 返回 {"items":[]}。

## 断点续跑
每个 Phase 完成即 git commit（feat: 月度测验 xxx）；失败保留现场并输出失败命令与日志，不要回滚已提交代码。
## 顺序
M1 数据层+权限 → M2 纯函数+服务 → M3 调查员端 → M4 管理端 → M5 联调实测。
```

## 附录 C：LLM Prompt 模板（沿用 v2，末尾追加 JSON 约束）

### Prompt 1：要点提取
```
你是劳动力调查专家。请从以下月度工作提示中提取可出题的要点。
## 输入
{notice_text}
## 输出格式
返回 JSON 数组，每项包含：
- section: 章节名称（"问卷要点"/"审核要点"/"填报口径微调"/其它原文章节名）
- content: 要点内容（一句话概括）
- common_error: 常见错误（如果有）
- suggest_quiz: 是否建议出题（true/false）
## 规则
1. 只提取需要调查员"记住并应用"的内容
2. 时间安排、通知对象等信息不提取
3. 每个要点聚焦一个知识点
4. 常见错误来自实际填报中的典型误判
5. 只输出 JSON，不要 markdown 代码块，不要任何解释
```

### Prompt 2：题目生成
```
你是劳动力调查出题专家。请根据以下要点生成 4 选 1 选择题。
## 输入
要点：{keypoint_content}
常见错误：{common_error}
来源段落：{source_quote}
## 输出格式
返回 JSON：
{"question": "题干（情境化，基于实际案例）",
 "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
 "answer": "正确答案（A/B/C/D）",
 "explanation": "解析（引用来源段落，说明为什么对/错）"}
## 规则
1. 题干基于实际填报场景，情境化出题
2. 干扰项来自常见错误，有迷惑性但明确错误
3. 解析必须引用来源段落原文
4. 答案唯一且确定
5. 只输出 JSON，不要 markdown 代码块，不要任何解释
```