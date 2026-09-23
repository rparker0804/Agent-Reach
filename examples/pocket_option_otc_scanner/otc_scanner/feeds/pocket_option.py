"""Pocket Option feed over a direct WebSocket connection (the primary data source).

!!! FRAGILE — PLATFORM-DEPENDENT !!!
Depends on: the quote-server hostnames, the Engine.IO v4 handshake, the Origin
check, the ``auth`` frame format, and the ``changeSymbol`` subscription behaviour.
All wire details are isolated in :mod:`.protocol`; this module only handles the
connection lifecycle (handshake, auth, subscribe, reconnect).
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from loguru import logger

from .base import AssetSelection, AuthError, FeedError, FeedEvent, PriceFeed
from .protocol import (
    DEFAULT_ORIGIN,
    REGION_URLS,
    ProtocolDecoder,
    ProtocolEvent,
    encode_subscribe,
    normalize_auth_frame,
)

Send = Callable[[str], Awaitable[None]]


class PocketOptionSession:
    """Protocol-level state machine shared by the WebSocket and browser feeds.

    ``drive_handshake=True``  -> we own the socket: answer pings, send ``40`` and auth.
    ``drive_handshake=False`` -> the browser page owns it; we only observe and inject
                                subscription frames once the page has authenticated.
    """

    def __init__(
        self,
        queue: asyncio.Queue[Any],
        selection: AssetSelection,
        period: int,
        send: Send,
        *,
        auth_frame: str | None = None,
        drive_handshake: bool = True,
        subscribe: bool = True,
        subscribe_templates: list[list[Any]] | None = None,
        subscribe_interval: float = 0.3,
        asset_wait: float = 8.0,
    ) -> None:
        self.queue = queue
        self.selection = selection
        self.period = period
        self.send = send
        self.auth_frame = auth_frame
        self.drive_handshake = drive_handshake
        self.subscribe = subscribe
        self.subscribe_templates = subscribe_templates
        self.subscribe_interval = subscribe_interval
        self.asset_wait = asset_wait
        self.decoder = ProtocolDecoder()
        self.authenticated = asyncio.Event()
        self._assets_seen = asyncio.Event()
        self._advertised: list = []
        self._subscribe_task: asyncio.Task[None] | None = None
        self.subscribed: list[str] = []

    async def feed(self, frame: str | bytes) -> None:
        for ev in self.decoder.decode(frame):
            await self._handle(ev)

    async def _handle(self, ev: ProtocolEvent) -> None:
        if ev.kind == "ping" and self.drive_handshake:
            await self.send("3")
        elif ev.kind == "open" and self.drive_handshake:
            await self.send("40")
        elif ev.kind == "connected" and self.drive_handshake and self.auth_frame:
            await self.send(self.auth_frame)
        elif ev.kind == "auth_ok" and not self.authenticated.is_set():
            logger.info("Pocket Option session authenticated")
            self.authenticated.set()
            if self.subscribe:
                self._subscribe_task = asyncio.create_task(self._subscribe_all())
        elif ev.kind == "auth_failed":
            raise AuthError(
                "Pocket Option rejected the session (NotAuthorized). Re-copy the auth "
                "frame from DevTools — sessions expire on logout/password change."
            )
        elif ev.kind == "assets":
            self._advertised = ev.data
            self._assets_seen.set()
            await self.queue.put(FeedEvent("assets", assets=ev.data))
        elif ev.kind == "ticks":
            await self.queue.put(FeedEvent("ticks", ticks=ev.data))
        elif ev.kind == "history":
            await self.queue.put(FeedEvent("history", ticks=ev.data.ticks, asset=ev.data.asset))

    async def _subscribe_all(self) -> None:
        if not isinstance(self.selection.pairs, list):
            try:
                await asyncio.wait_for(self._assets_seen.wait(), self.asset_wait)
            except asyncio.TimeoutError:
                logger.warning(
                    "No updateAssets received; falling back to configured fallback pairs"
                )
        symbols = self.selection.choose(self._advertised)
        if not symbols:
            logger.error("No assets selected — set scanner.pairs or scanner.fallback_pairs")
            return
        logger.info(f"Subscribing to {len(symbols)} assets: {', '.join(symbols)}")
        for sym in symbols:
            for frame in encode_subscribe(sym, self.period, self.subscribe_templates):
                await self.send(frame)
            self.subscribed.append(sym)
            # Pace requests like a human flicking between charts; bursts are what
            # rate limiters notice first.
            await asyncio.sleep(self.subscribe_interval)

    def cancel(self) -> None:
        if self._subscribe_task:
            self._subscribe_task.cancel()


class PocketOptionWebSocketFeed(PriceFeed):
    """Connects straight to the quote server using the user's own browser session."""

    def __init__(
        self,
        auth_frame: str,
        selection: AssetSelection,
        period: int = 60,
        regions: list[str] | None = None,
        *,
        origin: str = DEFAULT_ORIGIN,
        user_agent: str | None = None,
        subscribe_templates: list[list[Any]] | None = None,
        subscribe_interval: float = 0.3,
        idle_timeout: float = 60.0,
        max_backoff: float = 120.0,
    ) -> None:
        self.auth_frame = normalize_auth_frame(auth_frame)
        self.selection = selection
        self.period = period
        self.urls = [REGION_URLS.get(r, r) for r in (regions or ["DEMO"])]
        self.origin = origin
        self.user_agent = user_agent
        self.subscribe_templates = subscribe_templates
        self.subscribe_interval = subscribe_interval
        self.idle_timeout = idle_timeout
        self.max_backoff = max_backoff
        self._closed = False
        self._was_authed = False

    async def events(self) -> AsyncIterator[FeedEvent]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        runner = asyncio.create_task(self._run_forever(queue))
        try:
            while True:
                item = await queue.get()
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            self._closed = True
            runner.cancel()

    async def close(self) -> None:
        self._closed = True

    async def _run_forever(self, queue: asyncio.Queue[Any]) -> None:
        attempt = 0
        while not self._closed:
            url = self.urls[attempt % len(self.urls)]
            try:
                authed = await self._run_once(url, queue)
                attempt = 0 if authed else attempt + 1
            except AuthError as exc:
                await queue.put(exc)  # unrecoverable: the user must refresh the SSID
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # network errors, handshake rejections, idle timeouts
                logger.warning(f"Connection to {url.split('/')[2]} lost: {exc!r}")
                # A drop after a healthy session restarts the backoff; repeated
                # failures to even authenticate grow it.
                attempt = 1 if self._was_authed else attempt + 1
            if self._closed:
                return
            delay = min(self.max_backoff, 2 ** min(attempt, 7)) + random.uniform(0, 1)
            logger.info(f"Reconnecting in {delay:.1f}s")
            await asyncio.sleep(delay)

    async def _run_once(self, url: str, queue: asyncio.Queue[Any]) -> bool:
        from websockets.asyncio.client import connect

        headers = {"Origin": self.origin}
        if self.user_agent:
            headers["User-Agent"] = self.user_agent
        logger.info(f"Connecting to {url.split('/')[2]}")
        async with connect(
            url,
            additional_headers=headers,
            ping_interval=None,  # Engine.IO does its own ping/pong ("2"/"3")
            max_size=None,
            open_timeout=20,
        ) as ws:
            self._was_authed = False
            session = PocketOptionSession(
                queue,
                self.selection,
                self.period,
                ws.send,
                auth_frame=self.auth_frame,
                subscribe_templates=self.subscribe_templates,
                subscribe_interval=self.subscribe_interval,
            )
            try:
                while not self._closed:
                    timeout = self.idle_timeout if session.authenticated.is_set() else 20.0
                    try:
                        frame = await asyncio.wait_for(ws.recv(), timeout)
                    except asyncio.TimeoutError:
                        if not session.authenticated.is_set():
                            raise FeedError("no auth response within 20s (blocked or bad frame?)")
                        raise FeedError(f"no data for {self.idle_timeout:.0f}s")
                    await session.feed(frame)
                    self._was_authed = session.authenticated.is_set()
            finally:
                session.cancel()
            return session.authenticated.is_set()
