"""End-to-end Playwright tests for the Golem dashboard.

These tests start a real FastAPI/Uvicorn dashboard server and use Playwright
to verify rendering, theme application, and navigation.

Requires: playwright (pip install playwright && playwright install chromium)

NOTE: Uses async Playwright API because pytest-asyncio provides an event loop
and sync_playwright() cannot be used inside one.
"""

import asyncio
import socket
import threading
import time

import pytest

# Skip entire module if playwright is not installed
pw_mod = pytest.importorskip("playwright")

from playwright.async_api import async_playwright  # noqa: E402


def _find_free_port() -> int:
    """Find a free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Server fixture — module-scoped, runs in a background thread
# ---------------------------------------------------------------------------

_SERVER_PORT = _find_free_port()
_SERVER_URL = f"http://127.0.0.1:{_SERVER_PORT}"
_server_ref = None


def _start_server():
    """Start a uvicorn server in a new event loop on a background thread."""
    global _server_ref  # noqa: PLW0603

    async def _run():
        global _server_ref  # noqa: PLW0603
        import uvicorn
        from fastapi import FastAPI

        from golem.core.dashboard import mount_dashboard

        app = FastAPI()
        mount_dashboard(app)

        config = uvicorn.Config(
            app, host="127.0.0.1", port=_SERVER_PORT, log_level="error"
        )
        server = uvicorn.Server(config)
        _server_ref = server
        await server.serve()

    loop = asyncio.new_event_loop()
    thread = threading.Thread(
        target=loop.run_until_complete, args=(_run(),), daemon=True
    )
    thread.start()

    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", _SERVER_PORT), timeout=0.1):
                return
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)
    pytest.fail("Dashboard server did not start in time")


# Auto-start server once for the whole module
_start_server()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDashboardLoads:
    """Dashboard renders and serves content."""

    async def test_dashboard_returns_html(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(_SERVER_URL + "/dashboard")
            await page.wait_for_load_state("domcontentloaded")
            title = await page.title()
            assert title != ""
            await browser.close()

    async def test_overview_tab_present(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(_SERVER_URL + "/dashboard")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(500)
            tabs = await page.locator("[data-tab], .tab-btn, .nav-tab").all()
            assert len(tabs) > 0
            await browser.close()

    async def test_no_js_errors_on_load(self):
        errors = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            page.on(
                "console",
                lambda msg: errors.append(msg.text) if msg.type == "error" else None,
            )
            await page.goto(_SERVER_URL + "/dashboard")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(1000)
            await browser.close()
        real_errors = [
            e
            for e in errors
            if "favicon" not in e.lower() and "failed to load resource" not in e.lower()
        ]
        assert real_errors == [], f"Console errors: {real_errors}"


class TestThemeVariables:
    """Mission Control theme CSS variables are applied correctly."""

    async def _get_css_var(self, page, var_name):
        return await page.evaluate(
            f"getComputedStyle(document.documentElement).getPropertyValue('{var_name}').trim()"
        )

    async def test_accent_color_is_amber(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(_SERVER_URL + "/dashboard")
            await page.wait_for_load_state("domcontentloaded")
            accent = await self._get_css_var(page, "--accent")
            assert accent == "#e8a832"
            await browser.close()

    async def test_bg_base_is_dark_graphite(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(_SERVER_URL + "/dashboard")
            await page.wait_for_load_state("domcontentloaded")
            bg = await self._get_css_var(page, "--bg-base")
            assert bg == "#0c0c0e"
            await browser.close()

    async def test_font_family_includes_dm_sans(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(_SERVER_URL + "/dashboard")
            await page.wait_for_load_state("domcontentloaded")
            font = await self._get_css_var(page, "--font-sans")
            assert "DM Sans" in font
            await browser.close()

    async def test_all_new_variables_defined(self):
        """All 6 previously-undefined CSS variables are now defined."""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(_SERVER_URL + "/dashboard")
            await page.wait_for_load_state("domcontentloaded")
            for var in (
                "--cyan",
                "--danger",
                "--bg-success",
                "--border-success",
                "--bg-danger",
                "--border-danger",
            ):
                val = await self._get_css_var(page, var)
                assert val != "", f"{var} is not defined"
            await browser.close()


class TestNavigation:
    """Tab navigation works."""

    async def test_clicking_tab_does_not_crash(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(_SERVER_URL + "/dashboard")
            await page.wait_for_load_state("domcontentloaded")
            await page.wait_for_timeout(500)
            tabs = await page.locator("[data-tab], .tab-btn, .nav-tab").all()
            if len(tabs) >= 2:
                await tabs[1].click()
                await page.wait_for_timeout(300)
            # Page should still be on the dashboard (no crash/redirect)
            assert "/dashboard" in page.url
            await browser.close()


class TestStaticAssets:
    """CSS and JS files are served correctly."""

    async def test_shared_css_loads(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            response = await page.goto(_SERVER_URL + "/dashboard/shared.css")
            assert response.status == 200
            text = await response.text()
            assert "--accent" in text
            await browser.close()

    async def test_task_api_js_loads(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            response = await page.goto(_SERVER_URL + "/dashboard/task_api.js")
            assert response.status == 200
            text = await response.text()
            assert "PHASE_COLORS" in text
            await browser.close()


class TestAPIEndpoints:
    """API data endpoints respond."""

    async def test_api_live_returns_json(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            response = await page.goto(_SERVER_URL + "/api/live")
            assert response.status == 200
            data = await response.json()
            assert isinstance(data, dict)
            await browser.close()

    async def test_api_ping(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            response = await page.goto(_SERVER_URL + "/api/ping")
            assert response.status == 200
            await browser.close()
