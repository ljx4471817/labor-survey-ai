---
name: regulations-migrate
description: >
  Annual knowledge-base sync when the labor survey system document changes.
  Takes a directory containing the new system markdown, extracts the new indicator
  list, compares against the current indicator_catalog.json, generates a
  migration_map.json draft, and runs the full migrate → validate → rebuild →
  eval chain. Trigger when the user says: "制度变更", "新一年制度", "对齐新制度",
  "制度同步", "migration_map", "更新指标目录", "年度对齐", or hands you a folder
  containing a new 劳动力调查制度 document.
---

# 制度变更迁移 — Annual Knowledge-Base Sync

> Skill for **D:\code\labor-survey-ai**. Project-specific. Project root must be
> that path. The full machinery (`migrate_indicators.py`, `validate_faq.py`,
> `build_bm25.py`, `run_eval.py`, `indicator_catalog.json`) already exists —
> this skill is the **operator** that drives it.

每年 12 月初，制度文档（《劳动力调查制度（YYYY年定期报表）-定稿》）会发布新版。新版相对旧版通常只动 3-10 个指标（增/删/位移），不会大改模块结构。本 skill 的目标：**把"拿到新版制度 PDF/Word/markdown → faq.json 与 eval_set.json 完成对齐 → BM25 索引重建 → 全量回归通过"这条链路标准化、可一键执行**。

## 输入

调用时需提供一个目录路径，里面放着新版制度的 markdown 文件（命名约定见下）。

文件命名约定（任选其一即可，skill 会按优先级匹配）：
- `劳动力调查制度（YYYY年定期报表）-定稿.md`
- `劳动力调查制度（YYYY）-*.md`
- 目录里**唯一**的 `.md` 文件（兜底）

如果目录里没有 markdown，明确报错退出，不要猜。

## 输出

完成后产出三份变更 + 两份报告：

| 产出物 | 来源 | 说明 |
|--------|------|------|
| `knowledge-base/indicator_catalog.json` | 新版制度 | 反映最新指标目录 |
| `knowledge-base/migration_map.json` | 比对结果 | renamed / removed / added |
| `knowledge-base/qa/faq.json` | 迁移脚本 | indicators + 正文编号已对齐 |
| `knowledge-base/qa/eval_set.json` | 迁移脚本 | 同步问题正文编号 |
| `reports/eval-latest.json` | 全量回归 | 验证答案质量未退化 |

## 执行流程

### 第一步：定位新制度文件 & 提取指标

1. 检查输入目录里的 markdown 文件，按命名约定匹配
2. 读 markdown，提取指标清单（模块名 + 指标代码 + 名称 + 类型）
3. 提取策略：
   - **首选**：识别 markdown 里"个人情况"/"住户信息"/"工作情况"/"无工作情况"等模块章节下的指标定义段落（一般以 `F10. xxx`、`F11.xxx`、`H1.xxx` 开头）
   - **备用**：识别表格行（`| F10 | xxx | 单选 |`）
   - **兜底**：正则匹配行首 `\s*([FH]\d+(?:\.\d+)?)\s*[.、:：]?\s*(.+)`，把所有疑似指标都抓出来，再让人工确认
4. 用 `INDICATOR_RE = re.compile(r'(?<![A-Za-z])[FH]\d+(?:\.\d+)?')` 辅助识别代码（项目原有约定，避免 `F10` 误匹配 `XF10`）
5. 把提取结果打印成表格让用户确认（**不要默默用，要让用户核对**）

### 第二步：与现有 catalog 对比 → 生成 diff

读取 `knowledge-base/indicator_catalog.json`，逐项比对：

| 变化类型 | 含义 | 落到 migration_map.json 的字段 |
|----------|------|-------------------------------|
| 新增 | 新版里有，旧版没有 | `added` |
| 删除 | 旧版里有，新版没有 | `removed` |
| 改名/位移 | 同名但代码变了 | `renamed` |
| 描述变更 | 代码同名，描述变了 | 不进 migration_map（说明只是措辞调整），但要提示用户 |

输出 diff 让用户确认：
```
=== 新旧 catalog diff ===
[新增] F42: 是否参与平台经济灵活就业
[删除] F22.1: 已签合同类型（旧版特有）
[改名] F26 → F25: 是否主要通过线上/线下中间商订单生产
[描述变更] F10: 措辞微调
```

### 第三步：生成 migration_map.json（草稿，让用户改）

按 `knowledge-base/migration_map.example.json` 的 schema 写出草稿，放到 `knowledge-base/migration_map.json`。**必须让用户审过再继续**——制度变更每年最多几次，人工核对几十秒的成本远低于脚本理解错。

给用户的修改指引：
- `target_version`: 新版年份字符串（如 `"2027"`）
- `date`: 变更日期
- `renamed`: 旧→新映射，纯位移场景
- `removed`: 整条取消的指标
- `added`: 新增指标，附带 description 和 type

### 第四步：跑迁移脚本（dry-run → write）

```bash
# 必须先 dry-run，把影响报告给用户看
python scripts/migrate_indicators.py migration_map.json

# 用户确认后，写入
python scripts/migrate_indicators.py migration_map.json --write
```

dry-run 报告关键字段：
- `Renamed entries`: 因 renamed 字段改写 indicators 的条目数
- `Removed flags`: 因 removed 字段被打 `_indicators_removed` 标记的条目数（**这些条目要人工看是否要删/重写**）
- `Body text fixes`: 正文里指标代码被 regex 替换的次数
- `Validation errors`: 不在 catalog 里的指标代码（多半是 migration_map 写错了）

**如果 dry-run 出现 Validation errors，停下来让用户修，不要继续。**

### 第五步：校验 + 重建 BM25 索引

```bash
python scripts/validate_faq.py
python scripts/build_bm25.py --full
```

预期：`validate_faq.py` 通过（0 errors）；`build_bm25.py --full` 报告 N 条新索引（与 faq.json 总数一致）。

### 第六步：全量回归（确认未退化）

后端需要运行。如果没启动：
```bash
cd backend && python -m uvicorn app.main:app --host 127.0.0.1 --port 8765 &
```

需要登录 token（白名单已启用）：
```bash
python scripts/run_eval.py --phone 13985000001
```

（实际手机号查 `backend/data/whitelist.db`，从第一条取即可。）

预期：102/102 = 100%。任何 fail 都要停下来分析。

### 第七步：清理 `_indicators_removed` 条目

如果第四步产生了 `_indicators_removed` 标记的条目，说明这些条目关联的指标在新版不存在了。让用户决定：
- **删**：指标彻底消失，条目本身也没意义了
- **改写**：条目的问题/答案在新版里还能找到对应的新指标
- **保留**：制度文档只是临时调整，条目仍然有参考价值

让用户在这三选项里逐条决定，AI 不要自动删。

### 第八步（提醒）：重建制度文档 chunk

> 此步骤不自动执行——由用户决定是否、何时做。

QA 侧迁移完成后，如需将新版制度文档全文入库为检索 chunk，请独立运行：

```bash
python scripts/build_chunks.py --input <新制度md路径> --full
python scripts/build_bm25.py --full
python scripts/run_eval.py --phone <phone>
```

chunk 和 QA 完全解耦：chunk 入库不影响 QA 检索，QA 迁移也不动 chunk。制度变更后 chunk 必须 `--full` 全量重建（内容全变了，无增量迁移场景）。

## 红线（与项目 AGENTS.md 一致）

- 修改 `migration_map.json` / `indicator_catalog.json` / `faq.json` 前必须 dry-run
- 删除任何 faq.json 条目前必须确认（即使是 `_indicators_removed` 标记的）
- `.env`、CI/CD、部署脚本不在本 skill 范围内
- 不修改 git 历史，不 force push
- 后端启动用 `python -m uvicorn` 后台跑，**不要用 start_tunnel.bat**（那个会开 Cloudflare Tunnel 公网暴露，年度对齐不需要）

## 完成标准

四项硬指标全部满足才算完成：

1. `indicator_catalog.json` 反映新版指标列表
2. `validate_faq.py` 0 errors
3. `build_bm25.py --full` 报告的索引条目数 = faq.json 总数
4. `run_eval.py` 102/102 = 100%

## 失败处理

| 现象 | 原因 | 处理 |
|------|------|------|
| `validate_faq.py` 出现 `indicators_unknown_code` | 新版 catalog 没填全，或 migration_map.removed 漏了 | 查 catalog，补迁移映射 |
| dry-run 显示 `Validation errors` | migration_map.json 的指标代码不在 catalog 里 | 检查拼写，重新跑第一步 |
| `run_eval.py` 出现 fail | 迁移后某条 KB 答案质量退化 | 看具体哪条 fail，回到该条目人工核对 |
| 用户给的目录里没有 markdown | 用户给了空目录或只有 PDF/Word | 明确告知要求 markdown，不自己转 PDF |

## 调取方式

用户提供一个新制度文档所在目录的路径即可，例如：
- "制度变更，文档在 `D:\code\labor-survey-ai\knowledge-base\raw\markdown\2027\`"
- "对齐 2027 年新制度，文档已经放到 `D:/docs/2027-system/`"

skill 启动后**先把整个流程的计划打印给用户看**，再开始第一步。每一步执行后简短汇报"这一步做了什么、产出什么、下一步做什么"，不要堆到最后再说。
