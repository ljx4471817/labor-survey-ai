"""Run local OCR on all images extracted by extract_pptx.py and write the
recognized text back into pages.json / pages.md so image content is searchable.

Usage:
    python ocr_images.py <out_dir>

Requires: rapidocr-onnxruntime (pip install rapidocr-onnxruntime)
Local only: no network, no external API. Handles Chinese UI screenshots well.
"""
import json, os, sys
from rapidocr_onnxruntime import RapidOCR

OUT = sys.argv[1] if len(sys.argv) > 1 else r"pptx_extract"
with open(os.path.join(OUT, "pages.json"), encoding="utf-8") as f:
    pages = json.load(f)

ocr = RapidOCR()
total_lines = 0
for p in pages:
    new_imgs = []
    for rel in p["images"]:
        full = os.path.join(OUT, rel)
        try:
            res, _ = ocr(full)
        except Exception as e:
            print("ERR", rel, e)
            res = None
        lines = [ln[1] for ln in (res or [])]
        new_imgs.append({"path": rel, "ocr_text": "\n".join(lines)})
        total_lines += len(lines)
    p["images"] = new_imgs
    print(f"page {p['page']:02d}: {len(new_imgs)} imgs, "
          f"ocr lines={sum(len(i['ocr_text'].splitlines()) for i in new_imgs)}")

with open(os.path.join(OUT, "pages.json"), "w", encoding="utf-8") as f:
    json.dump(pages, f, ensure_ascii=False, indent=2)

with open(os.path.join(OUT, "pages.md"), "w", encoding="utf-8") as f:
    for p in pages:
        f.write(f"## 第 {p['page']} 页\n\n")
        if p["title"]:
            f.write(f"**标题**: {p['title']}\n\n")
        if p["body"]:
            f.write(p["body"] + "\n\n")
        if p["notes"]:
            f.write(f"**备注**: {p['notes']}\n\n")
        for im in p["images"]:
            f.write(f"![{im['path']}]({im['path']})\n")
            if im["ocr_text"]:
                f.write(f"\n> 图片内容(可检索): {im['ocr_text']}\n\n")
            else:
                f.write("\n")

print("=== OCR done. total ocr text lines:", total_lines, "===")
