"""Screenshot the dashboard top with a mocked 区县业务管理员 session.

The real production test phone is not a dashboard admin, so API responses are
intercepted with plausible generic values; the crop only covers header + stat
cards + tab bar, never question text.
"""

import json
import pathlib
import re

from playwright.sync_api import sync_playwright


RAW = pathlib.Path(__file__).resolve().parent / "_raw"
LOCAL = "http://127.0.0.1:8001"

WHOAMI = {
    "phone": "13900000000",
    "name": "测试业务员",
    "province": "贵州省",
    "city": "贵阳市",
    "county": "云岩区",
    "admin_level": "区县",
    "sys_role": "业务管理员",
    "active": True,
}

USAGE = {
    "results": [
        {
            "phone": "138****0001",
            "name": "张*",
            "province": "贵州省",
            "city": "贵阳市",
            "county": "云岩区",
            "township": "xx街道",
            "community": "xx社区",
            "query_count": 32,
            "last_query_at": "2026-08-25 14:30",
            "feedback_count": 5,
            "adopted_count": 4,
        },
        {
            "phone": "138****0002",
            "name": "李*",
            "province": "贵州省",
            "city": "贵阳市",
            "county": "云岩区",
            "township": "xx街道",
            "community": "xx社区",
            "query_count": 18,
            "last_query_at": "2026-08-24 09:12",
            "feedback_count": 2,
            "adopted_count": 1,
        },
        {
            "phone": "138****0003",
            "name": "王*",
            "province": "贵州省",
            "city": "贵阳市",
            "county": "云岩区",
            "township": "xx街道",
            "community": "xx社区",
            "query_count": 9,
            "last_query_at": "2026-08-22 16:05",
            "feedback_count": 1,
            "adopted_count": 1,
        },
    ]
}


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.add_init_script(
            "localStorage.setItem('lsx_token','mock');"
            "localStorage.setItem('lsx_token_expires_at', String(Date.now()/1000+3600));"
        )

        def on_api(route):
            url = route.request.url
            if url.endswith("/api/admin/whitelist/whoami"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps(WHOAMI))
            elif "/api/admin/usage/search" in url:
                route.fulfill(status=200, content_type="application/json", body=json.dumps(USAGE))
            else:
                route.fulfill(status=200, content_type="application/json", body="{}")

        page.route(re.compile(r".*/api/admin/.*"), on_api)
        page.goto(LOCAL + "/dashboard", wait_until="domcontentloaded")
        page.wait_for_selector(".usage-table tbody tr", timeout=15000)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(RAW / "dashboard-top.png"), clip={"x": 0, "y": 0, "width": 1440, "height": 480})
        browser.close()
    print("dashboard-top.png saved (mock stats)")


if __name__ == "__main__":
    main()
