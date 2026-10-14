"""Capture README screenshots of the running app with headless Chromium.

    streamlit run app.py &
    python scripts/take_screenshots.py --url http://localhost:8501
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
WIDTH = 1440


async def fit_and_shoot(page, name: str, settle_ms: int = 1200, min_height: int = 860) -> None:
    await page.mouse.move(WIDTH - 20, 12)  # no hover toolbars or sidebar buttons in the picture
    await page.wait_for_timeout(settle_ms)
    bottom = await page.evaluate(
        "() => { const el = document.querySelector('[data-testid=\"stMainBlockContainer\"]');"
        " return el ? el.getBoundingClientRect().bottom + window.scrollY : document.body.scrollHeight; }")
    await page.set_viewport_size({"width": WIDTH, "height": int(min(max(bottom + 40, min_height), 3600))})
    await page.wait_for_timeout(1200)
    await page.screenshot(path=OUT / name)
    await page.set_viewport_size({"width": WIDTH, "height": 1000})
    print("saved", name)


async def select_row(page, index: int, subject: str) -> None:
    grid = page.get_by_test_id("stDataFrame").first
    await grid.scroll_into_view_if_needed()
    await page.wait_for_timeout(300)
    box = await grid.bounding_box()
    await page.mouse.click(box["x"] + 18, box["y"] + 35 + 35 * index + 17)
    await page.locator("strong", has_text=subject).first.wait_for(timeout=15_000)  # the open email's title
    await page.wait_for_timeout(1500)


async def main(url: str, executable: str | None) -> None:
    from playwright.async_api import async_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**({"executable_path": executable} if executable else {}))
        page = await browser.new_page(viewport={"width": WIDTH, "height": 1000}, device_scale_factor=2)
        await page.goto(url, wait_until="networkidle")
        await page.get_by_text("Sales inbox").wait_for(timeout=60_000)
        await page.get_by_role("button", name="Run the agent on all emails").click()
        await page.get_by_role("button", name="Approve & send").wait_for(timeout=60_000)
        await page.wait_for_timeout(1500)

        await select_row(page, 3, "Prices for our new shop")
        await fit_and_shoot(page, "inbox.png")
        await page.get_by_role("tab", name="Agent steps").click()
        await fit_and_shoot(page, "agent-steps.png")

        await select_row(page, 4, "Bulk order - 15% discount?")
        await page.get_by_role("tab", name="Quote").click()
        await fit_and_shoot(page, "quote-review.png")

        for index, subject, button in [(0, "Quote for office restock", "Approve & send"),
                                       (6, "Stationery and IT accessories", "Approve & send"),
                                       (1, "Price request - clinic furniture", "Send to manager"),
                                       (5, "Invoice INV-2291 charged twice", "Forward to accounts")]:
            await select_row(page, index, subject)
            await page.get_by_role("button", name=button).click()
            await page.wait_for_timeout(1500)

        await page.get_by_role("link", name="Sent & CRM").click()
        await page.get_by_text("Sent & CRM log").wait_for()
        await fit_and_shoot(page, "crm.png", 2000)

        await page.get_by_role("link", name="Evaluation").click()
        await page.get_by_role("button", name="Run the evaluation").click()
        await page.get_by_text("Intent & routing").wait_for(timeout=60_000)
        await fit_and_shoot(page, "evaluation.png", 2500)

        await page.get_by_role("link", name="Catalog & rules").click()
        await page.get_by_text("Catalog, stock & pricing rules").wait_for()
        await fit_and_shoot(page, "catalog.png", 2500)
        await browser.close()
    print(f"saved to {OUT}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--executable", help="path to a Chromium binary (optional)")
    args = parser.parse_args()
    asyncio.run(main(args.url, args.executable))
