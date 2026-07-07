"""从 cloudflared quick tunnel 日志里提取第一个 https://*.trycloudflare.com URL。

用法（在 .bat 里）：
    python scripts\extract_cf_url.py <log_path>

成功：stdout 打印 URL（无尾换行），exit 0
失败：stdout 无输出，exit 1
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: extract_cf_url.py <log_path>", file=sys.stderr)
        return 2
    log_path = Path(sys.argv[1])
    if not log_path.exists():
        return 1
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = URL_RE.search(line)
        if m:
            print(m.group(0), end="")
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
