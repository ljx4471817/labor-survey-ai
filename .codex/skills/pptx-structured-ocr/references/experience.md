# PPTX 按页结构化 + 图片可检索 经验参考

> 配套 SKILL.md 的详细版：踩坑、完整脚本、实测数据、入库建议、复现 Checklist。
> 本文件供 WorkBuddy 在执行时按需载入参考。

---

## 0. 适用场景与前置判断

| 情况 | 推荐做法 |
|---|---|
| PPTX 文字为主、图片少、不含关键操作截图 | 直接用 markitdown 转纯文本 Markdown 即可，不必拆图 |
| **PPTX 图片多、是系统操作培训/手册（截图=核心信息）** | **必须走「按页结构化 + 图片可检索」** |
| 内部资料、不能传外部 API | OCR 必须本地跑，绝不用外部识别接口 |
| 部署服务器小（如 2核4G）、跑不了 embedding 模型 | 入库倾向 BM25/全文检索，而非服务器硬扛向量 |

---

## 1. 环境准备

隔离受管 Python venv（不要污染系统 Python）。需安装：

```bash
pip install markitdown python-pptx rapidocr-onnxruntime
```

- `markitdown`：纯文本提取备选路线（含坑，见 4.1）。
- `python-pptx`：按页取文本/Notes（markitdown 基础版不含此依赖，必须单独装）。
- `rapidocr-onnxruntime`：本地 OCR，基于 onnxruntime（体积仅十几~几十 MB），**不过 OOM、不联网**，适合中文界面截图。
- 设环境变量降内存（受限环境务必加）：`export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1`

---

## 2. 整体流程

```
PPTX
 ├─(A) markitdown 纯文本提取  ──→ 仅文字、图片是死链（备选/对照，非最终方案）
 └─(C) 按页结构化（本文主线）
       ├─ python-pptx + zipfile 提取每页 标题/正文/Notes + 真实图片  → pages.json / pages.md
       ├─ rapidocr 本地 OCR 全部图片，抽出图内文字                        → 并入 pages.json.images[].ocr_text
       └─ 组装：每页一节点，图片真实可显示 + 图内文字可检索
```

最终产物目录：

```
pptx_extract/
├── pages.json          # 程序化入库用：[{page,title,body,notes,images:[{path,ocr_text}]}]
├── pages.md            # 人读/预览用：每页一节 + 图片 + 「> 图片内容(可检索)」
└── images/
    ├── page_01/xxx.jpeg
    ├── page_02/xxx.png
    └── ...
```

---

## 3. 步骤详解

### 3.1 markitdown 纯文本提取（备选路线，含坑）

```bash
markitdown "源文件.pptx" > out.md
```

**坑（必看，详见 4.1）**：markitdown 0.1.6 初始化会**无条件加载 magika 的 onnx 模型**，内存受限环境会 `bad allocation` 崩。需先打 magika 空壳补丁再调用（见 `scripts/markitdown_patch.py`）。纯文本提取后图片只是 `![](图片x.jpg)` 死链，**不适合直接进库**。

### 3.2 按页结构化提取（核心）

要点：
1. `python-pptx` 遍历每页，取 `shape.text_frame.text`（首段当标题，其余当正文）、`notes_slide.notes_text_frame` 取演讲者备注。
2. 用 `zipfile` 直接读 `ppt/slides/slideN.xml` 里的 `r:embed` 关系 id，再去 `slideN.xml.rels` 反查 `Target`，得到该页对应的 `ppt/media/...` 图片。
3. **关键坑**：跨平台路径必须用 `posixpath`，不能用 `os.path.normpath`（Windows 下会变反斜杠，导致 `in z.namelist()` 判定失败、图片静默漏提）。见 4.2。

完整脚本见 `scripts/extract_pptx.py`。

### 3.3 本地 OCR（图片可检索的关键）

为什么选 `rapidocr-onnxruntime`：
- **本地运行**，内部截图数据不出机（合规）；
- 基于 onnxruntime（markitdown/magika 已带，无需额外大依赖），模型小，**不会 OOM**；
- 中文识别效果好（针对中文界面/按钮/报错截图）。

单图验证（先跑一张确认能出字、不过 OOM）：

```python
from rapidocr_onnxruntime import RapidOCR
import glob
img = sorted(glob.glob(r"pptx_extract/images/page_01/*"))[0]
ocr = RapidOCR()
res, _ = ocr(img)
print([line[1] for line in (res or [])][:8])
```

全量脚本见 `scripts/ocr_images.py`。

### 3.4 产物结构（入库契约）

`pages.json` 每条：

```json
{
  "page": 3,
  "title": "账号注册与登录",
  "body": "进入专用直报平台…",
  "notes": "卡在75%需重设调查范围",
  "images": [
    {"path": "images/page_03/image12.jpeg", "ocr_text": "短信验证码\n请输入手机号…"}
  ]
}
```

`pages.md`：每页 `## 第 N 页`，标题/正文/备注 + 真实 `![]()` 图片 + `> 图片内容(可检索): …`。

---

## 4. 踩坑与解决（重点，照抄可避雷）

| # | 现象 | 根因 | 解决 |
|---|---|---|---|
| 4.1 | `markitdown xxx.pptx` 直接 `bad allocation` 崩 | 0.1.6 `__init__` 无条件 `magika.Magika()` 加载 onnx 模型，内存受限环境爆掉；`enable_magika=False` 也没真正跳过 | 在 `from markitdown import MarkItDown` 之前把 `magika.Magika` 替换成不加载模型的空壳（见 `scripts/markitdown_patch.py`）。之后按扩展名路由解析 |
| 4.2 | 图片全部漏提（静默 0 张） | 用 `os.path.normpath` 把 zip 内 `ppt/media/...` 正斜杠路径变成反斜杠，与 `z.namelist()` 里的正斜杠不匹配 | 改用 `posixpath.normpath` / `posixpath.join` 保持正斜杠 |
| 4.3 | 页-图映射抓不到图 | 只用 rels 里 `../media/` 正则，路径写法不统一漏配 | 从 slide XML 抓 `r:embed` 关系 id，再去 rels 按 `Id="..."` 反查 `Target` |
| 4.4 | 无本地 OCR 能力 | 环境无 tesseract、无 easyocr/paddleocr（后者依赖 PyTorch 易 OOM） | 用 `rapidocr-onnxruntime`（onnxruntime 轻量，不联网、不过 OOM） |
| 4.5 | 直接把 markitdown 纯文本进 RAG | 死图链接变成无意义 token，污染 embedding | 要么走方案 C 把图真实导出，要么至少把死链清成 `[图片]` 标注、按页分块 |

**4.1 的 magika 空壳脚本**见 `scripts/markitdown_patch.py`。

---

## 5. 实测效果（量级参考）

- 源：79 页 PPTX，图片密集的操作培训。
- markitdown 纯文本：839 行 / 12162 字符，含 **74 处死图链接**（不适合直接进库）。
- 方案 C：提取 **75 张真实图片**（按页归档），本地 OCR 共识别 **4801 行图内文字**（界面按钮、菜单、报错提示均入索引）。
- 内部截图数据**全程未出本机**。

---

## 6. 入库建议（RAG / 全文检索）

1. **切分**：以「每页」为一个 chunk；chunk 文本 = 标题 + 正文 + 备注 + 该页所有图片的 `ocr_text`。页码写进 metadata，便于定位原 PPT 第几页。
2. **检索方案选型**：
   - 若部署服务器能跑 embedding → 向量 RAG，query 与 index 必须用同一模型。
   - **若部署在 2核4G 等小服务器、跑不了 embedding 模型**：不必在服务器加载向量模型。可选：
     - (A) 本地生成向量后，仅让服务器做轻量相似度检索（FAISS/Chroma/pgvector）；
     - **(C，推荐) 放弃向量，用 BM25 / SQLite FTS5 + jieba 全文检索**——零模型依赖、小服务器可跑、数据不出内网，且对「按术语找操作步骤」命中率往往高于语义向量。
3. **合规**：内部资料避免使用外部 embedding/OCR API（如阿里百炼 DashScope），防止文字内容出内网。本地 OCR + 本地/全文检索最稳妥。
4. **局限**：轻量 OCR 对手写、艺术字、极低分辨率、复杂流程图识别率有限；如某页明显识别错，可针对该页换更强模型或重跑。

---

## 7. 复现 Checklist

- [ ] 建隔离 venv，装 `markitdown python-pptx rapidocr-onnxruntime`
- [ ] 跑 `extract_pptx.py` 拿到每页文本 + 真实图片（确认图片数 > 0，警惕 4.2 反斜杠坑）
- [ ] 跑 `ocr_images.py` 全量 OCR，把 `ocr_text` 并入 `pages.json`
- [ ] 决定检索方式：小服务器优先 BM25/全文，不硬扛向量
- [ ] 以页为 chunk 入库，OCR 文字并进索引文本
