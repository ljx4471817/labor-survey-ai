"""pytest 全局配置。

关键：把 backend 目录加入 sys.path，使 `from app.xxx` 能直接工作，
无需先 cd backend 也不需要 pip install -e .。
"""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))