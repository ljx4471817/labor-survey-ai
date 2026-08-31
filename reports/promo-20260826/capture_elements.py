"""Element-level screenshots for the promo one-pagers.

Production (login / chat / dashboard top) + local mocked whitelist page.
No real personal data is captured: whitelist API is intercepted with fake rows,
and dashboard is cropped to header + stat cards only.
"""

import json
import pathlib
import re

from playwright.sync_api import sync_playwright


RAW = pathlib.Path(__file__).resolve().parent / "_raw"
FRONT = "https://laborforceai.xyz"
LOCAL = "http://127.0.0.1:8001"
PHONE = "13985000001"
QUESTION = "灵活就业人员怎么登记？"

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

ROWS = [
    {
        "phone": f"138****000{i}",
        "name": name,
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
    }
    for i, name in enumerate(["张*", "李*", "王*"], start=1)
]


def wait_answer(page, timeout_s: int = 90) -> None:
    import time

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if page.locator(".msg-row.assistant").count() >= 1:
            if page.locator(".msg-row.assistant .thinking").count() == 0:
                text = page.locator(".msg-row.assistant .bubble.answer-block").inner_text()
                if len(text.strip()) > 10:
                    return
        time.sleep(1)
    raise TimeoutError("assistant answer did not render in time")


def shot_el(page, selector, path: pathlib.Path) -> None:
    page.locator(selector).first.screenshot(path=str(path))
    print("saved", path.name)


def clip_qa(page, path: pathlib.Path) -> None:
    q = page.locator(".bubble.question").first
    a = page.locator(".msg-row.assistant").last
    qb = q.bounding_box()
    ab = a.bounding_box()
    assert qb and ab
    x0 = min(qb["x"], ab["x"]) - 18
    y0 = min(qb["y"], ab["y"]) - 18
    x1 = max(qb["x"] + qb["width"], ab["x"] + ab["width"]) + 18
    y1 = max(qb["y"] + qb["height"], ab["y"] + ab["height"]) + 18
    page.screenshot(
        path=str(path),
        clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
    )
    print("saved", path.name)


def clip_top(page, path: pathlib.Path, max_h: int = 560) -> None:
    page.screenshot(path=str(path), clip={"x": 0, "y": 0, "width": 1440, "height": max_h})
    print("saved", path.name)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)

        # ---- production: login card + chat Q&A ----
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()
        page.goto(FRONT, wait_until="domcontentloaded")
        page.wait_for_selector("div.card", timeout=15000)
        shot_el(page, "div.card", RAW / "login-card.png")

        page.fill("#phone", PHONE)
        page.click("#submit-btn")
        page.wait_for_selector("#question-input", timeout=20000)
        page.fill("#question-input", QUESTION)
        page.click("#send-btn")
        wait_answer(page)
        page.wait_for_timeout(800)
        clip_qa(page, RAW / "chat-qa-clip.png")
        ctx.close()

        # ---- production: dashboard top (header + stat cards only) ----
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
        page2 = ctx2.new_page()
        page2.goto(FRONT, wait_until="domcontentloaded")
        page2.fill("#phone", PHONE)
        page2.click("#submit-btn")
        page2.wait_for_selector("#question-input", timeout=20000)
        page2.goto(FRONT + "/dashboard", wait_until="domcontentloaded")
        page2.wait_for_selector("#feedbackCards", timeout=20000)
        page2.wait_for_timeout(1500)
        clip_top(page2, RAW / "dashboard-top.png", max_h=560)
        ctx2.close()

        # ---- local mocked whitelist: header / toolbar / table ----
        ctx3 = browser.new_context(viewport={"width": 1440, "height": 900})
        page3 = ctx3.new_page()
        page3.add_init_script(
            "localStorage.setItem('lsx_token','mock');"
            "localStorage.setItem('lsx_token_expires_at', String(Date.now()/1000+3600));"
        )

        def on_api(route):
            url = route.request.url
            if url.endswith("/api/admin/whitelist/whoami"):
                route.fulfill(status=200, content_type="application/json", body=json.dumps(WHOAMI))
            elif url.endswith("/api/admin/whitelist"):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"items": ROWS, "total": len(ROWS)}),
                )
            else:
                route.continue_()

        page3.route(re.compile(r".*/api/admin/whitelist(/.*)?$"), on_api)
        page3.goto(LOCAL + "/whitelist-admin", wait_until="domcontentloaded")
        page3.wait_for_selector("#tbody tr", timeout=15000)
        page3.wait_for_timeout(800)
        shot_el(page3, "header.admin-header", RAW / "whitelist-header.png")
        shot_el(page3, ".toolbar", RAW / "whitelist-toolbar.png")
        shot_el(page3, ".table-wrap", RAW / "whitelist-table.png")
        ctx3.close()

        browser.close()
    print("done")


if __name__ == "__main__":
    main()
