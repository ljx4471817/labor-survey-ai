"""markitdown text-only extraction with the magika OOM workaround.

markitdown 0.1.6 unconditionally loads magika's onnx model at init and crashes
with `bad allocation` in low-memory sandboxes; `enable_magika=False` does NOT skip
it. This script stubs magika.Magika with a no-op before import so conversion falls
back to extension-based routing.

Usage:
    python markitdown_patch.py <src.pptx> <out.md>

NOTE: markitdown image references are dead `![](image.jpg)` links, not real images.
For image-searchable output, use extract_pptx.py + ocr_images.py instead.
"""
import sys, magika


class _Output:
    label = "unknown"
    is_text = False
    extensions = []
    mime_type = ""


class _Prediction:
    output = _Output()


class _Result:
    status = "ok"
    prediction = _Prediction()


class _DummyMagika:
    def __init__(self, *a, **k):
        pass

    def identify_stream(self, *a, **k):
        return _Result()

    def identify_path(self, *a, **k):
        return _Result()

    def identify_bytes(self, *a, **k):
        return _Result()


magika.Magika = _DummyMagika

from markitdown import MarkItDown

SRC = sys.argv[1] if len(sys.argv) > 1 else r"源文件.pptx"
OUT = sys.argv[2] if len(sys.argv) > 2 else r"out.md"
md = MarkItDown(enable_magika=False)
text = md.convert(SRC).text_content
with open(OUT, "w", encoding="utf-8") as f:
    f.write(text)
print("OK", len(text), "chars ->", OUT)
