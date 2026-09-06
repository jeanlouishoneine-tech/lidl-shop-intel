"""
Capture the dashboard across every tab, theme and viewport.

Boots the app on a spare port, drives it with Playwright, writes full-page PNGs to
artifacts/ui/, then shuts the server down. Design work is judged against these images.

    uv run python scripts/screenshot.py
    uv run python scripts/screenshot.py --out artifacts/ui-before
"""
import argparse
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8050  # matches src/app.py's hardcoded app.run(port=8050) and the Makefile's `run` target

TABS = ["Overview", "Items", "Offers for You", "AI Insights"]
THEMES = ["light", "dark"]
VIEWPORTS = {"desktop": (1440, 900), "mobile": (390, 844)}

# Plotly draws after the callback returns, so every capture needs a settle pause.
SETTLE_MS = 900


def _wait_for_server(url: str, proc: subprocess.Popen, timeout: float = 60.0) -> None:
    """Poll until the app answers, failing loudly if it died on startup."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"App exited with code {proc.returncode} before serving. Output:\n"
                f"{(proc.stdout.read() if proc.stdout else '')}"
            )
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
            time.sleep(0.4)
    raise TimeoutError(f"App did not start within {timeout}s at {url}")


def _settle(page: Page) -> None:
    """Wait for Dash callbacks and any Plotly render to finish."""
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass  # networkidle is best-effort; the timeout below still gives charts time
    page.wait_for_timeout(SETTLE_MS)


def _set_theme(page: Page, theme: str) -> None:
    current = page.evaluate("() => document.documentElement.dataset.theme || 'light'")
    if current != theme:
        page.click("#theme-toggle-btn")
        _settle(page)


def _open_tab(page: Page, label: str) -> None:
    page.click(f"#tabs >> text={label}")
    _settle(page)


def _select_all_time(page: Page) -> None:
    """The fixture data's newest receipt predates the default 30-day filter, which would
    otherwise screenshot every tab empty. Widen the window so charts/tables/cards render."""
    page.click("#time-filter")
    page.wait_for_timeout(200)
    page.get_by_text("All time", exact=True).click()
    _settle(page)


def _capture_receipt(page: Page, out: Path, theme: str) -> None:
    """Click a point on the spend chart to open the receipt drill-down."""
    chart = page.locator("#trend-chart .scatterlayer .points path").first
    if chart.count() == 0:
        print(f"    receipt: no chart points to click, skipping")
        return
    chart.click(force=True)
    _settle(page)
    page.screenshot(path=str(out / f"desktop-{theme}-receipt.png"), full_page=True)
    print(f"    desktop-{theme}-receipt.png")


def shoot(base_url: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for vp_name, (width, height) in VIEWPORTS.items():
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(base_url, wait_until="domcontentloaded")
            _settle(page)
            _select_all_time(page)
            for theme in THEMES:
                _set_theme(page, theme)
                for tab in TABS:
                    _open_tab(page, tab)
                    slug = tab.lower().replace(" ", "-")
                    name = f"{vp_name}-{theme}-{slug}.png"
                    page.screenshot(path=str(out / name), full_page=True)
                    print(f"    {name}")
                if vp_name == "desktop":
                    _open_tab(page, "Overview")
                    _capture_receipt(page, out, theme)
            page.close()
        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="artifacts/ui", help="output directory for PNGs")
    parser.add_argument("--url", help="shoot an already-running app instead of booting one")
    args = parser.parse_args()

    out = ROOT / args.out
    if out.exists():
        shutil.rmtree(out)

    if args.url:
        print(f"Shooting {args.url} -> {out}")
        shoot(args.url, out)
        return 0

    url = f"http://127.0.0.1:{PORT}"
    proc = subprocess.Popen(
        [sys.executable, "src/app.py"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        print(f"Booting app on {url} ...")
        _wait_for_server(url, proc)
        print(f"Shooting -> {out}")
        shoot(url, out)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
