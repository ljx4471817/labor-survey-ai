"""Capture real pages of the labor-survey AI system for promo one-pagers.

Uses the protected test phone 13985000001 (system admin, whitelisted).
Outputs raw PNGs into reports/promo-20260826/_raw/.
"""

import pathlib
import time

from playwright.sync_api import sync_playwright


ROOT = pathlib.Path(__file__).resolve().parent
RAW = ROOT / "_raw"
RAW.mkdir(parents=True, exist_ok=True)

FRONT = "https://laborforceai.xyz"
PHONE = "13985000001"
QUESTION = "灵活就业人员怎么登记？"


def wait_answer(page, timeout_s: int = 90) -> None:
    """Wait until the assistant bubble is rendered and no thinking placeholder remains."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rows = page.locator(".msg-row.assistant").count()
        thinking = page.locator(".msg-row.assistant .thinking").count()
        if rows >= 1 and thinking == 0:
            text = page.locator(".msg-row.assistant .bubble.answer-block").inner_text()
            if len(text.strip()) > 10:
                return
        time.sleep(1)
    raise TimeoutError("assistant answer did not render in time")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        page = ctx.new_page()

        # 1. Login page (public, no auth needed)
        page.goto(FRONT, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(RAW / "login.png"), full_page=True)

        # 2. Log in with the protected test phone
        page.fill("#phone", PHONE)
        page.click("#submit-btn")
        page.wait_for_selector("#question-input", timeout=20000)
        page.wait_for_timeout(1000)
        page.screenshot(path=str(RAW / "chat-empty.png"), full_page=True)

        # 3. Ask a real question and wait for the answer
        page.fill("#question-input", QUESTION)
        page.click("#send-btn")
        wait_answer(page)
        page.wait_for_timeout(800)
        page.screenshot(path=str(RAW / "chat-qa.png"), full_page=True)
        ctx.close()

        # 4. Admin pages (desktop viewport)
        ctx2 = browser.new_context(viewport={"width": 1440, "height": 900})
        page2 = ctx2.new_page()
        page2.goto(FRONT, wait_until="domcontentloaded")
        page2.fill("#phone", PHONE)
        page2.click("#submit-btn")
        page2.wait_for_selector("#question-input", timeout=20000)

        page2.goto(FRONT + "/dashboard", wait_until="domcontentloaded")
        page2.wait_for_timeout(2500)
        page2.screenshot(path=str(RAW / "dashboard.png"), full_page=True)

        page2.goto(FRONT + "/whitelist-admin", wait_until="domcontentloaded")
        page2.wait_for_selector("#tbody tr", timeout=20000)
        page2.wait_for_timeout(1200)
        page2.screenshot(path=str(RAW / "whitelist.png"), full_page=True)
        ctx2.close()
        browser.close()

    print("done:", sorted(p.name for p in RAW.iterdir()))


if __name__ == "__main__":
    main()
