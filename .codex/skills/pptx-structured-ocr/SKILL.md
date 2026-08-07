---
name: pptx-structured-ocr
description: This skill should be used when a user needs to convert an image-heavy PowerPoint (PPTX) — especially system-operation training or manuals where screenshots carry the core information — into structured, image-searchable data for a knowledge base (vector RAG or full-text search). It extracts per-slide text/notes, real slide images, and runs local OCR on those images so that on-image text (button labels, menu paths, error messages) becomes searchable. Use it for tasks like "拆 PPTX 成按页结构化 + 图片可检索", "把培训PPT转成可检索的知识库数据", or any PPTX → per-page structured JSON/Markdown with OCR'd image text, keeping all processing local (no external APIs) for internal/compliance-sensitive documents.
agent_created: true
---

# PPTX Structured OCR

## Overview

Convert an image-dense PPTX into per-page structured records where each slide's text, speaker notes, real images, and OCR'd in-image text are captured. The output feeds knowledge bases: every slide becomes one chunk, and screenshot text becomes searchable. All processing is local (no external OCR/embedding APIs), suitable for internal/compliance-sensitive material.

## When To Use

- Source is a PPTX with many screenshots (operation training, system manuals, UI walkthroughs).
- Goal: feed into a knowledge base where image text must be retrievable (e.g., searching a button name or error message that only appears inside a screenshot).
- Constraint: must not send content to external APIs; deployment server may be small (e.g., 2-core/4GB) and unable to run embedding models → prefer BM25/full-text over heavy vectors.

Do NOT use this for text-only PPTX with few images — a simple `markitdown` conversion suffices then.

## Workflow

### Step 0 — Environment

Use an isolated Python venv. Install:
```
pip install markitdown python-pptx rapidocr-onnxruntime
```
Set `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1` to reduce memory in constrained environments. Local OCR uses `rapidocr-onnxruntime` (onnxruntime-based, small model, no OOM, no network, good Chinese support).

### Step 1 — Per-page extraction (core)

Run `scripts/extract_pptx.py <src.pptx> <out_dir>`. It:
- Iterates slides via `python-pptx`, capturing each shape's text (first non-empty text = title, rest = body) and notes slide text.
- Reads `ppt/slides/slideN.xml` for `r:embed` relationship ids, resolves each via `slideN.xml.rels` `Target` to a `ppt/media/...` path, and extracts those images to `out_dir/images/page_NN/`.
- Writes `pages.json` (`{page,title,body,notes,images:[relpaths]}`) and `pages.md`.

**Critical pitfall (Windows):** never normalize zip-internal paths with `os.path.normpath` — it turns `ppt/media/...` into backslashes and silently drops all images (the `in z.namelist()` check fails). Always use `posixpath`.

### Step 2 — Local OCR (image searchability)

Run `scripts/ocr_images.py <out_dir>`. It loads `pages.json`, runs RapidOCR on each image, stores recognized lines into `images[].ocr_text`, and rewrites both `pages.json` and `pages.md` (appending `> 图片内容(可检索): ...` under each image). RapidOCR handles Chinese UI screenshots well; handwritten/artistic/very-low-res text has lower accuracy.

### Step 3 — (Optional) pure-text route via markitdown

If only text is needed (no images), `markitdown` can extract directly — but markitdown 0.1.6 unconditionally loads magika's onnx model at init and crashes with `bad allocation` in low-memory sandboxes (even `enable_magika=False` does not skip it). Use `scripts/markitdown_patch.py <src> <out.md>`, which stubs `magika.Magika` with a no-op before import so conversion falls back to extension-based routing. Note: markitdown image references are dead `![](image.jpg)` links, NOT suitable for direct RAG ingestion.

### Step 4 — Ingestion guidance

- Chunk by page: chunk text = title + body + notes + all `ocr_text` of that page's images. Store page number as metadata.
- Small-server deployment (cannot run embedding models): prefer BM25 / SQLite FTS5 + jieba full-text search over vectors; the server only needs to store data and run lightweight similarity/full-text queries, not an embedding model.
- Compliance: avoid external embedding/OCR APIs for internal material.

## Resources

- `scripts/extract_pptx.py` — per-page text + real image extraction → pages.json/md.
- `scripts/ocr_images.py` — local OCR of all images, writes `ocr_text` back.
- `scripts/markitdown_patch.py` — magika-stubbed markitdown text extraction (fallback route).
- `references/experience.md` — detailed pitfalls, full scripts, real-run metrics, and a reproduction checklist.
