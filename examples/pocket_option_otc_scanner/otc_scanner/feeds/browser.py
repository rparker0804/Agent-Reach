"""Pocket Option feed through a real (Playwright-driven) Chromium window.

The page logs in and keeps its own socket alive exactly as it does for a human;
we sit in the middle of that socket (``BrowserContext.route_web_socket``), copy every
server->client frame into the same protocol decoder the direct feed uses, and add
subscription frames for the pairs we want.

Use this when the direct WebSocket feed gets rejected (Cloudflare/Origin checks,
changed auth frame) or when you don't want to handle the SSID yourself.

!!! FRAGILE — PLATFORM-DEPENDENT !!!
* ``TERMINAL_URLS`` and the ``po.market/socket.io`` URL pattern.
* Everything listed in :mod:`.protocol` (same decoder).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from loguru import logger

from .base import AssetSelection, AuthError, FeedEvent, PriceFeed
from .pocket_option import PocketOptionSession

# FRAGILE: web terminal URLs.
TERMINAL_URLS = {
    "demo": "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
    "real": "https://pocketoption.com/en/cabinet/quick-high-low/",
}
# FRAGILE: quote sockets live on *.po.market today.
SOCKET_PATTERN = re.compile(r"po\.market/socket\.io")


class BrowserFeed(PriceFeed):
    def __init__(
        self,
        selection: AssetSelection,
        period: int = 60,
        *,
        account: str = "demo",
        profile_dir: str | Path = "~/.otc_scanner/browser-profile",
        headless: bool = False,
        inject_subscriptions: bool = True,
        subscribe_templates: list[list[Any]] | None = None,
        subscribe_interval: float = 0.3,
        login_timeout: float = 300.0,
        socket_pattern: re.Pattern[str] | str = SOCKET_PATTERN,
        executable_path: str | None = None,
    ) -> None:
        self.selection = selection
        self.period = period
        self.url = TERMINAL_URLS.get(account, account)
        self.profile_dir = Path(profile_dir).expanduser()
        self.headless = headless
        self.inject = inject_subscriptions
        self.subscribe_templates = subscribe_templates
        self.subscribe_interval = subscribe_interval
        self.login_timeout = login_timeout
        self.socket_pattern = socket_pattern
        self.executable_path = executable_path
        self._sessions: list[PocketOptionSession] = []

    async def events(self) -> AsyncIterator[FeedEvent]:
        from playwright.async_api import async_playwright

        queue: asyncio.Queue[Any] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                str(self.profile_dir),
                headless=self.headless,
                executable_path=self.executable_path,
                viewport={"width": 1400, "height": 900},
            )

            async def route(ws: Any) -> None:
                server = ws.connect_to_server()

                async def send_to_server(msg: str) -> None:
                    server.send(msg)

                session = PocketOptionSession(
                    queue,
                    self.selection,
                    self.period,
                    send_to_server,
                    drive_handshake=False,  # the page handles handshake, auth and pings
                    subscribe=self.inject,
                    subscribe_templates=self.subscribe_templates,
                    subscribe_interval=self.subscribe_interval,
                )
                self._sessions.append(session)

                def on_server(msg: str | bytes) -> None:
                    ws.send(msg)  # keep the page working normally
                    # Playwright invokes this callback on our event loop; tasks start
                    # in FIFO order and decode synchronously, so frame order holds.
                    task = loop.create_task(session.feed(msg))
                    task.add_done_callback(lambda t: _surface_error(t, queue))

                ws.on_message(lambda msg: server.send(msg))  # page -> server passthrough
                server.on_message(on_server)
                logger.debug(f"Tapped socket {ws.url}")

            await context.route_web_socket(self.socket_pattern, route)
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(self.url, wait_until="domcontentloaded")
            logger.info(
                f"Browser open at {self.url}. If this is the first run, log in in that "
                f"window (session is saved in {self.profile_dir})."
            )
            watchdog = asyncio.create_task(self._login_watchdog(queue))
            try:
                while True:
                    item = await queue.get()
                    if isinstance(item, BaseException):
                        raise item
                    yield item
            finally:
                watchdog.cancel()
                for s in self._sessions:
                    s.cancel()
                await context.close()

    async def _login_watchdog(self, queue: asyncio.Queue[Any]) -> None:
        await asyncio.sleep(self.login_timeout)
        if not any(s.authenticated.is_set() for s in self._sessions):
            await queue.put(
                AuthError(f"No authenticated quote socket after {self.login_timeout:.0f}s")
            )


def _surface_error(task: asyncio.Task[None], queue: asyncio.Queue[Any]) -> None:
    if not task.cancelled() and task.exception() is not None:
        queue.put_nowait(task.exception())
