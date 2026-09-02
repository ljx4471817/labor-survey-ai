# kb-optimize — 单 query 检索结果优化（奥卡姆版 v2）

> 触发：用户说「这个 query 答得不好 / 答得不一致 / 答非所问」或主动调用 `/kb-optimize`。
> 边界：**单 query / 一类 query 的单点优化**。不做批量、不做架构决策、不做新增代码层。
> 原则：奥卡姆剃刀 —— 能改 KB 就不改 prompt，能改 prompt 就不改 retriever，能不动代码就不动。
> v2 改进（2026-08-10 评估后）：加基线留存 / 风险偏好 / 置信度具体化 / 超奥卡姆回退提示。

## 定位

**80% 价值在诊断，20% 在执行。**

这个 skill 最值钱的输出是**把问题看清楚**（拉日志 / 对比 FAQ / 定位冲突），而不是给改动。改动本身通常就几行。

## 三条红线（动手前必过）

```
□ 这个优化只涉及现有 KB 条目 / eval / prompt 吗？
□ 需要新增代码层吗？
□ 改动能用一行 git diff 描述吗？
```

**任何一条不过 → 停下问用户，不动手。**

## 五步流程

### Step 1 — 复现与诊断（核心）

目标：**定位问题出在哪一层**。

```bash
# 1.1 跑 query，看检索日志
cd backend && python -c "
from app.rag.retriever import retrieve
from app.rag.pure import merge_query_with_history
q = 'USER_QUERY'
merged = merge_query_with_history(q, [])
sources = retrieve(merged, top_k=5)
for s in sources:
    print(s['id'], s['score'], s['metadata'].get('category',''))
    print('  ', s['document'][:120])
"
```

**看什么**：
- `n_sources`：命中几条
- `score`：top-1 分数（< 0.5 通常召回质量差）
- 命中 FAQ 的 **answer 是否互相冲突**

**冲突识别**：
- 两条 FAQ 都提到同一指标（如 F27）但结论相反
- 一条说「应 X」，另一条说「不应 X」
- 一条是制度原文，一条是兜底话术，二者适用范围重叠

**诊断输出格式**：

```
[诊断报告]
Query: <原始 query>
命中数: n 条
冲突检测: 
  - id=XXX（结论：…）
  - id=YYY（结论：…）
  → 冲突类型：适用范围重叠 + 结论相反
初步判断：Q1/Q2/Q3 哪一层
置信度：<具体描述，见下方>
```

### Step 1.5 — 基线留存（必须！）

**在改任何代码之前**，先跑一次留存当前输出：

```bash
# 调 DeepSeek 或本地 LLM 跑一次 query，保存输出
python -c "
import sys; sys.path.insert(0, r'BACKEND_PATH')
from app.rag.llm import chat
from app.rag.prompts import SYSTEM_PROMPT, USER_TEMPLATE
# ... 构造 messages ...
answer = chat(messages)
open('reports/kb-optimize-baseline.txt', 'w', encoding='utf-8').write(answer)
print('BASELINE_SAVED')
"
```

**不跑基线就动手 = 无法证明修复有效**。

### Step 2 — 给用户候选方案（按置信度排序）

**不要直接给答案**，给候选，让用户选。

| 层级 | 改动 | 置信度（具体描述）| 奥卡姆优先级 |
|---|---|---|---|
| KB | 给 FAQ answer 加 scope 限定 / 拆分冲突条目 / 补缺失 QA | **高**：今天改明天就见效，可验证 | 1 |
| eval | 加 must_contain_any / should_not_contain | **高**：门禁只加分不扣分 | 必须和 KB/prompt 同步 |
| prompt | system prompt 加硬规则 / 冲突裁决 | **中**：依赖 LLM 遵循，偶尔翻车需 eval 补刀 | 2 |
| retriever | 改 top_k / 阈值 / 分块策略 | **低**：全局影响，改了不知道影响多少 query | 最后手段 |

**输出格式**：

```
[候选方案]
A. [KB 层] <具体改动>
   - 改动量：<文件 + 行数>
   - 置信度：高 = 今天改明天就见效
   - 风险：低

B. [prompt 层] <具体改动>
   - 改动量：<文件 + 行数>
   - 置信度：中 = 依赖 LLM 遵循，偶尔翻车
   - 风险：中（全局影响）

C. [eval 层] <具体改动>（必须和 A 或 B 同步上）
   - 改动量：<文件 + 行数>
   - 置信度：高 = 只加分不扣分
   - 风险：无

奥卡姆推荐：A + C（最小改动 + 门禁）
```

### Step 2.5 — 风险偏好选择（新增）

**给用户表达偏好的入口**，不要机械推奥卡姆：

```
[风险偏好]
你想怎么修？
1. 最简（奥卡姆）：只改 KB + 加 eval ← 推荐
2. 最稳（多层）：KB + prompt + eval 全上 ← 多一层保险
3. 自定义：你指定要哪几层

输入 1 / 2 / 3：
```

**如果用户选了 2（最稳）**，给一次回退机会：

```
[确认] 你选了「最稳」方案，会加一条 system prompt 硬规则。
这条规则影响所有 query，未来每次 prompt 改动都要考虑它。
确认继续？ (y/n)
```

**如果用户选了 3（自定义）**，列出所有候选让用户勾选。

### Step 3 — 执行（用户拍板后）

按用户选的方案执行。**每次只选一个方案**，不要贪多。

**KB 层改动模板**：
```bash
# 定位条目
python -c "
import json
data = json.load(open('knowledge-base/qa/faq.json', encoding='utf-8'))
for item in data:
    if item['id'] == 'TARGET_ID':
        print(json.dumps(item, ensure_ascii=False, indent=2))
"
# 改 answer / keywords / scope
# 验证 JSON 合法
python -c "import json; json.load(open('knowledge-base/qa/faq.json', encoding='utf-8')); print('JSON-OK')"
# 单测
cd backend && pytest tests/ -q
```

**prompt 层改动模板**：
```bash
# 定位 system prompt 硬规则段落
grep -n "硬规则" backend/app/rag/prompts.py
# 追加规则
# 验证 Python 合法
python -m py_compile backend/app/rag/prompts.py
# 单测
cd backend && pytest tests/ -q
```

**eval 层改动模板**：
```bash
# 定位 eval_set.json 末尾
tail -20 knowledge-base/qa/eval_set.json
# 追加 eval 条目
# 验证 JSON 合法
python -c "import json; json.load(open('knowledge-base/qa/eval_set.json', encoding='utf-8')); print('JSON-OK')"
```

### Step 4 — 验证与对比

**改前 / 改后对比**（基线留存的价值）：

```bash
# 跑改后的 query
python -c "
# ... 构造同样的 messages ...
answer = chat(messages)
open('reports/kb-optimize-after.txt', 'w', encoding='utf-8').write(answer)
print('AFTER_SAVED')
"

# 对比
diff reports/kb-optimize-baseline.txt reports/kb-optimize-after.txt
```

**最终报告格式**：

```
[修复报告]
Query: <原始 query>
问题: <一句话定位>
方案: <用户选的>
风险偏好: 最简 / 最稳 / 自定义
改动:
  - <文件路径>: <一句话描述>
验证:
  - JSON 合法: ✅
  - pytest: ✅ (N passed)
  - 改前输出: reports/kb-optimize-baseline.txt
  - 改后输出: reports/kb-optimize-after.txt
  - 对比结论: <一句话说清改好了没>
  - eval 跑通: ⏳（需真人跑 run_eval.py）
风险: <低 / 中 / 高 + 理由>
下一步: <留待真人做的事>
```

## 和现有 skill 的关系

| skill | 职责 | 何时用 |
|---|---|---|
| `kb-update-workflow` | 新题入库 5 阶段 | 新文档入库 |
| `kb-optimize`（本 skill）| 已有 query 的诊断 + 修复 | query 答得不好 |
| `regulations-migrate` | 制度年度迁移 7 步 | 每年 12 月 |
| `neat-freak` | 大版本三层盘点 | 季度级大扫除 |

**冲突预检**：`kb-update-workflow` 的新题入库阶段应做冲突预检（避免事后修复需求）。详见 `references/conflict-precheck.md`。本 skill 是**事后修复**路径。

## 不做的事（明确边界）

- ❌ 自动检测触发（用户主动调用）
- ❌ 批量重构（超过 3 个 query → 建议用 neat-freak）
- ❌ 新增代码层（retriever 修改 / reranker / 新 prompt 机制 → 先问用户）
- ❌ ADR / 架构决策（如果改动大到要改架构，停下问用户）
- ❌ 改 KB schema（那是 ADR 0008 的事）
- ❌ 不跑基线就动手（Step 1.5 是必须）

## 自检清单（每次跑完）

- [ ] 动手前三条红线过了吗？
- [ ] 跑了基线留存吗？（Step 1.5）
- [ ] 给了候选方案让用户选，没直接动手？
- [ ] 问了风险偏好吗？（Step 2.5）
- [ ] 选了超奥卡姆方案时给了回退机会吗？
- [ ] 每次只选了一个方案？
- [ ] 验证跑了吗（JSON + pytest）？
- [ ] 给了改前 / 改后对比？
- [ ] 给了最终报告？