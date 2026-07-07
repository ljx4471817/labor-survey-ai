# 内容提取编排 Prompt

> ⚠️ **本文件是一次性任务的归档 Prompt**（2026-06 初版）。当前 KB 维护已转用脚本（`scripts/build_kb.py` + `scripts/eval_from_docx.py`），不再手工执行 Prompt。新增 QA 见 `docs/04-知识库规范.md` 第四节「录入流程」。
>
> 如确需重跑（重大制度更新时），按下方「Prompt 全文」执行。

> **历史用途**：把 `knowledge-base/raw/` 下的原始文件转成结构化 QA JSON，供 RAG 系统使用。
> **历史使用方式**：把下面"Prompt 全文"部分整段复制，发给另一个 AI（如 GPT-4、Claude 网页版），让它执行。
> **历史输出位置**：`knowledge-base/qa/faq.json`（项目内路径，不再使用绝对路径）

---

## Prompt 全文

```
# 角色
你是一名政务领域知识工程专家，专门为国家统计局劳动力调查的辅助调查员整理 RAG 知识库。

# 项目背景
- 产品：劳动力调查 AI 助手（微信小程序 + FastAPI 后端，Chroma 向量库）
- 用户：辅助调查员（基层入户调查人员）
- 填报工具：调查员使用专用 app，app 自带基础填报指引
- 编码填报：行职业编码由办公室人员填报，调查员不接触
- AI 助手核心场景：调查员遇到复杂场景时，问"某个指标该如何填"，AI 查询知识库后给出填报建议

# 关键场景提醒
**调查员不需要**：编码规则、纯制度条文复述、初级填报指引（app 已有）
**调查员需要**：
- 复杂场景的判断（边界情况、特殊情况）
- 指标定义与适用情形
- 真实案例与处理方式
- 简明、可直接操作的填报建议

# 输入文件
请读取以下 3 个文件：
- `<PROJECT_ROOT>\knowledge-base\raw\劳动力调查制度（2026年定期报表）-定稿.doc`
- `<PROJECT_ROOT>\knowledge-base\raw\劳动力调查专业题库（24年8月）.docx`
- `<PROJECT_ROOT>\knowledge-base\raw\劳动力调查及指标讲解.pptx`

# 任务步骤

## 步骤 1：markitdown 转换
安装并使用 markitdown 库（同时安装 python-docx、python-pptx 作为依赖）：

```bash
pip install --user markitdown python-pptx python-docx
```

把 3 个文件批量转成 markdown：

```python
from pathlib import Path
from markitdown import MarkItDown

md = MarkItDown()
raw = Path(r"<PROJECT_ROOT>\knowledge-base\raw")
out = raw / "markdown"
out.mkdir(exist_ok=True)

for f in raw.glob("*"):
    if f.suffix.lower() in {".doc", ".docx", ".pptx"} and not f.name.startswith("~"):
        result = md.convert(str(f))
        (out / (f.stem + ".md")).write_text(result.text_content, encoding="utf-8")
        print(f"转出: {f.name} -> {f.stem}.md")
```

## 步骤 2：通读与分类
读 3 个 markdown 文件，按"知识库规范"的分类法给每条 QA 分配 category：

- 就业状态判断
- 工作时间
- 调查时点
- 调查对象
- 行业职业（少用——编码由办公室处理，仅保留"是什么"的解释）
- 失业原因
- 填报规范（重点）
- 抽样方法
- 家庭关系
- 收入相关
- 数据质量

如有必要新增分类，按命名风格扩展。

## 步骤 3：QA 提取与标准化

每条 QA 必须严格符合以下 JSON schema（逐字段不能少）：

```json
{
  "id": "001",
  "category": "就业状态判断",
  "question": "一个人每周工作15小时，算不算就业人口？",
  "answer": "根据劳动力调查制度，就业人口的判断标准是：在调查参考周内，从事1小时以上有收入的劳动即为就业。每周工作15小时当然属于就业人口。需要注意的是...",
  "source": "劳动力调查制度（2026版）第三章第二节",
  "keywords": ["就业", "工作时间", "15小时", "就业人口"]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|------|------|------|
| id | ✅ | 3 位数字字符串，"001" 到 "999"，顺序递增，不复用 |
| category | ✅ | 一级分类（见步骤 2） |
| question | ✅ | 口语化，调查员实际会问的方式（含典型数字、案例） |
| answer | ✅ | 简洁直接，100-300 字，含制度依据，可操作 |
| source | ✅ | 精确到章/节，如"劳动力调查制度（2026版）第三章第二节" |
| keywords | ✅ | 至少 3 个，含核心名词、典型数字、场景词 |

## 步骤 4：质量红线（不可违反）

❌ 禁止录入：
1. 无来源依据的问答（制度文档中找不到对应条款）
2. 含主观判断（"我认为"、"一般情况"等非制度性表述）
3. 行职业编码相关问答（与调查员场景无关）
4. 重复问答（已在题库中的相同问题，保留来源最权威的版本）

✅ 应该优先录入：
1. 复杂场景判断（边界情况、特殊人群）
2. 易混淆指标（就业/失业/不在劳动力的边界）
3. 真实案例型问答（题库中已有的形式）
4. 制度中提到的"特别注意"、"例外情形"

## 步骤 5：输出 JSON

输出到 `<PROJECT_ROOT>\knowledge-base\qa\faq.json`：

```json
[
  {"id": "001", "category": "...", "question": "...", "answer": "...", "source": "...", "keywords": [...]},
  {"id": "002", ...}
]
```

## 步骤 6：交付前自查清单

完成后回答以下问题：

1. 总共生成了多少条 QA？
2. 各 category 的分布？（就业状态判断 X 条、工作时间 Y 条...）
3. 有几条没有 source 依据？（必须为 0）
4. 有几条涉及行职业编码？（应剔除）
5. 有几条与已有题库内容重复？（应剔除）
6. 抽取 5 条你最满意、最有代表性的 QA 展示给我看

# 数量目标

首批至少 100 条（覆盖高频问题），后续扩展到 200-500 条。质量优先，宁缺毋滥——100 条高质量胜过 200 条掺水。

# 注意

- 不要试图一次性把 3 个文档全录完——制度文档重点章节详录，次要章节简略
- 不要重复——题库中已有的问答，制度文档不再重复录
- 不要怕少——首批 100 条没覆盖到所有问题没关系，后续可增量补充
- 不要编造——任何不能追溯到制度原文的问答，宁可不录
```

---

## 给另一个 AI 的指令要点

你发给另一个 AI 时，除了 prompt 全文，还可以补充：

1. **环境确认**：确认 Python 3.10+ 已装，有足够磁盘空间（markitdown 转换的中间产物）
2. **执行方式**：让它**一步一步报告进度**，不要一口气跑完
3. **交付形式**：除 faq.json 外，还要给你 markdown 转换产物（在 `knowledge-base/raw/markdown/`）
4. **质量验证**：要求它完成步骤 6 的自查清单，回答 6 个问题

---

## 收到结果后你要做的事

1. 把 `faq.json` 复制到 `<PROJECT_ROOT>\knowledge-base\qa\faq.json`
2. 把 markdown 文件放到 `<PROJECT_ROOT>\knowledge-base\raw\markdown\`
3. 让我（Claude Code）做后续验证：JSON 解析、字段完整性、Top-5 检索测试