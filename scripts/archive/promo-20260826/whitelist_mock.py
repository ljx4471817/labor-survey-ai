"""Screenshot the whitelist-admin page with a mocked 区县业务管理员 session.

API responses are intercepted so no real account or personal data is used.
"""

import pathlib
import re

from playwright.sync_api import sync_playwright


RAW = pathlib.Path(__file__).resolve().parent / "_raw"
BASE = "http://127.0.0.1:8001"

WHOAMI = {
    "phone": "13900000000",
    "name": "测试业务员",
    "province": "贵州省",
    "city": "贵阳市",
    "county": "云岩区",
    "township": "",
    "community": "",
    "admin_level": "区县",
    "sys_role": "业务管理员",
    "active": True,
}

ROWS = [
    {
        "phone": "138****0001",
        "name": "张*",
        "province": "贵州省",
        "city": "贵阳市",
        "county": "云岩区",
        "township": "xx街道",
        "community": "xx社区",
        "admin_level": "调查员",
        "sys_role": "普通用户",
        "active": True,
        "updated_at": "2026-08-20 10:21",
        "remark": "调查员",
    },
    {
        "phone": "138****0002",
        "name": "李*",
        "province": "贵州省",
        "city": "贵阳市",
        "county": "云岩区",
        "township": "xx街道",
        "community": "xx社区",
        "admin_level": "调查员",
        "sys_role": "普通用户",
        "active": True,
        "updated_at": "2026-08-19 15:02",
        "remark": "调查员",
    },
    {
        "phone": "138****0003",
        "name": "王*",
        "province": "贵州省",
        "city": "贵阳市",
        "county": "云岩区",
        "township": "xx街道",
        "community": "xx社区",
        "admin_level": "调查员",
        "sys_role": "普通用户",
        "active": True,
        "updated_at": "2026-08-18 09:40",
        "remark": "调查员",
    },
]


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()

        page.add_init_script(
            "localStorage.setItem('lsx_token','mock');"
            "localStorage.setItem('lsx_token_expires_at', String(Date.now()+3600*1000));"
        )

        def on_api(route):
            url = route.request.url
            if url.endswith("/api/admin/whitelist/whoami"):
                route.fulfill(status=200, content_type="application/json", body=__import__("json").dumps(WHOAMI))
            elif url.endswith("/api/admin/whitelist"):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=__import__("json").dumps({"items": ROWS, "total": len(ROWS)}),
                )
            else:
                route.continue_()

        page.route(re.compile(r".*/api/admin/whitelist(/.*)?$"), on_api)
        page.goto(BASE + "/whitelist-admin", wait_until="domcontentloaded")
        page.wait_for_selector("#tbody tr", timeout=15000)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(RAW / "whitelist.png"), full_page=True)
        browser.close()

    print("whitelist.png saved")


if __name__ == "__main__":
    main()
