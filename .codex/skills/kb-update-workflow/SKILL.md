---
name: kb-update-workflow
description: >
  知识库（KB）5 阶段入库流程触发器。
  当用户消息包含「新题库」「新文档」「新讲义」「新制度」「新增 KB」
  「走入库」「加题库」「更新 KB」任一关键词时激活。
  覆盖：源文档 → markdown → Q&A 抽取 → 查重缺口检测 → 审核入库。
  不覆盖：单条 corner case 修复（仍走 patch_faq_from_eval.py 风格）。
---

# KB 更新流程（5 阶段）

## 触发条件

用户消息中包含以下任一关键词时激活：
- 新题库 / 新文档 / 新讲义 / 新制度 / 新增 KB
- 走入库 / 入库 / 加题库 / 更新 KB

**不要在以下场景误触发**：
- 「改这一条」「修一个 corner case」「补一条」 → 那是单条 patch，走 `scripts/patch_faq_from_eval.py` 风格
- 「删掉 id 305」「合并两条」 → 手动编辑 faq.json
- KB schema 变更（v2 评估）→ 迭代 3 / Stage 1 任务，不在本 skill 范围

## 流程

### 阶段 1：收集（用户手动）

1. 用户已把新文档放进 `knowledge-base/raw/`
2. **先扫一眼文件名/标题/章节，确认真的是新增而非重命名**（避免把同一文档重做一遍）
3. 跟用户确认：「确认是新的，开始走流程」
4. **不要触碰** `knowledge-base/raw/` 里已有的 .docx/.doc/.pptx（git 已追踪，只读）

### 阶段 2：转 markdown

```bash
python scripts/ingest_source.py knowledge-base/raw/<file>
```

- 自动识别 docx/doc/pdf/pptx
- 输出 `knowledge-base/raw/markdown/<同名>.md`
- 已存在默认跳过，`--force` 覆盖
- 转换器：python-docx / docx2txt / pdfplumber / python-pptx（已在环境中验证）

### 阶段 3：抽 Q&A

```bash
python scripts/extract_qa_pairs.py knowledge-base/raw/markdown/<stem>.md --mode {regex|llm}
```

**模式选择**（AI 自己判断，不需要问用户）：
- 源是**题库 docx**（有「题号 + A./B./C./D. + 答案」结构）→ `regex`
- 源是**制度 doc / 讲解 pptx**（无标准 Q&A 格式）→ `llm`（走 DeepSeek 提炼）
- 输出 `reports/extracted-qa-<stem>.json`

### 阶段 4：查重 + 缺口检测

```bash
python scripts/detect_gaps.py --candidates reports/extracted-qa-<stem>.json
```

- 复用 `scripts/build_kb.py:EmbeddingClient`（DashScope / BGE）
- 阈值默认：≥0.85 自动跳过、0.70-0.85 待审、<0.70 可加
- 输出 `reports/gap-report-<stem>.json`，结构：summary / skipped / review_needed / add_candidates

### 阶段 5：审核 + 入库（用户主导 + AI 执行）

**5a. 用户审稿**（人）：
- 打开 `reports/gap-report-<stem>.json` 的 `add_candidates` 段
- 删 LLM 瞎编的（制度里没明说的、对不上的、口语化过头的）
- 改 answer、补 keywords、调 category、调 source 引用
- 把审核通过的复制到 `reports/approved-<stem>.json`

**5b. AI 执行入库**（AI）：

```bash
python scripts/add_faq_entries.py reports/approved-<stem>.json
```

- 自动续号（接续当前 faq.json 最大 id）
- 字段硬校验（id 格式、answer 50-400 字、keywords ≥3、source 非空）
- 自动调 `validate_faq.py` 子进程验证

**5c. 落盘 + 重建**（AI）：

```bash
python scripts/validate_faq.py --strict
python scripts/build_kb.py --full
python scripts/build_bm25.py --full
python scripts/run_eval.py --phone 15519106778
```

**5d. （可选）补 eval 锁定**：新加的 corner case 在 `eval_set.json` 加 1-2 条 eval 防止回归。

### 阶段 5d 前必读：eval 关键词的连续子串陷阱

`scripts/run_eval.py:70` 的关键词命中是**连续子串检查**（`k in answer`），不是分词命中也不是同义词匹配。

**踩过的坑**（2026-06-26）：
- eval-048 关键词 `['PAD离线', '无网络', '离线采集', '数据上报']`，LLM 合成答案里这些词被拆开写成「PAD 的离线数据采集模式」「有网络的地方」「数据核改上报」——4 个关键词都拆了，0/4 = 0% 失败
- 同一次改 id 188 又引发 eval-045 回归 0/4（关键词 `['拒访', '统计法义务', '耐心解释', '上级协助']` 同样被拆散）

**选关键词的两条铁律**：

1. **从 canonical answer 原文里挑**：写完 answer 后，先用 `python -c "for k in kws: print(k, k in answer)"` 验证一遍，挑那些**已经**作为连续子串出现的术语当 `expected_keywords`。不要凭直觉造词。
2. **制度术语要保留原话**：像「统计法义务」「核改上报」「PAD 离线」这种制度里有的术语，answer 里要原样写、不要同义改写。否则 LLM 合成时也容易丢。

**写完 answer 后用这段快速验**：

```bash
python -c "
import json
faq = json.load(open('knowledge-base/qa/faq.json', encoding='utf-8'))
import sys
for e in faq:
    if e.get('id') == '<新 id>':
        for k in e.get('expected_keywords', []):
            print('OK' if k in e['answer'] else 'MISS', k)
        break
"
```

**改 answer 时同步重检 eval**：批量改 answer 时（如修 corner case、加指标条目），改完要跑 `run_eval.py` 看是否引入新失败——eval 是 RAG 检索漂移的最早信号。

## 关键命令位置

详见项目 `AGENTS.md`「关键命令」节「知识库：新题库入库流程」段。

## 边界

- **不在** `D:\code\labor-survey-ai` 项目根目录跑 → 先 `cd`
- 跑本流程前确认 `.env` 里有 `DASHSCOPE_API_KEY`（embedding）和 `DEEPSEEK_API_KEY`（LLM 模式）
- `reports/extracted-qa-*.json` 是 LLM 草稿，不入 git；`reports/approved-*.json` 也不入；最终只入 `faq.json`

## 验证门禁

- 阶段 4 的 `add_candidates` 数 ≥ 1（流程能产出真实新增）
- 阶段 5 的 `run_eval.py` 通过率 ≥ 现状（不引入回归）
- 阶段 5 的 `validate_faq.py --strict` 0 错误 0 警告

## 历史演练（参考）

2026-06-26 用 `劳动力调查及指标讲解.pptx` 跑通：
- 20110 字 markdown 输出
- LLM 抽 227 条 Q&A 候选
- 跳过 33 / 待审 130 / 可加 64
- 端到端无报错
