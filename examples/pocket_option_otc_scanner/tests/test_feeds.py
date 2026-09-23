"""Feed tests against a local mock of the Pocket Option quote server (no network)."""

import asyncio
import json
import os

import pytest
from otc_scanner.feeds.base import AssetSelection, AuthError, looks_like_fx_pair
from otc_scanner.feeds.pocket_option import PocketOptionWebSocketFeed
from otc_scanner.models import AssetInfo

AUTH = '42["auth",{"session":"test","isDemo":1,"uid":1,"platform":2}]'
ASSETS = [
    [66, "EURUSD_otc", "EUR/USD OTC", "currency", 1, 92, 60, 30, 3, 0, 170, 0, [], 1, True],
    [86, "GBPUSD_otc", "GBP/USD OTC", "currency", 1, 80, 60, 30, 3, 0, 170, 0, [], 1, True],
    [408, "SP500_otc", "SP500 OTC", "index", 1, 90, 60, 30, 3, 0, 170, 0, [], 1, True],
    [1, "EURUSD", "EUR/USD", "currency", 1, 85, 60, 30, 3, 0, 170, 0, [], 1, True],
]


async def mock_server(ws, received, accept=True):
    """Speaks the same Engine.IO/Socket.IO sequence the real server does."""
    received.append(("origin", ws.request.headers.get("Origin")))
    await ws.send('0{"sid":"s1","upgrades":[],"pingInterval":25000,"pingTimeout":20000}')
    assert await ws.recv() == "40"
    await ws.send('40{"sid":"n1"}')
    received.append(("auth", await ws.recv()))
    if not accept:
        await ws.send('42["NotAuthorized",{}]')
        await ws.wait_closed()
        return
    await ws.send('451-["successauth",{"_placeholder":true,"num":0}]')
    await ws.send(b'{"id":"abc"}')
    await ws.send('451-["updateAssets",{"_placeholder":true,"num":0}]')
    await ws.send(json.dumps(ASSETS).encode())
    await ws.send("2")
    subs = []
    while len(subs) < 2:
        msg = await ws.recv()
        if msg == "3":
            received.append(("pong", msg))
        else:
            subs.append(msg)
    received.append(("subs", subs))
    await ws.send('451-["updateStream",{"_placeholder":true,"num":0}]')
    await ws.send(json.dumps([["EURUSD_otc", 1700000000.1, 1.0812]]).encode())
    await ws.wait_closed()


def run_feed(accept=True, n_events=2):
    from websockets.asyncio.server import serve

    received = []

    async def main():
        async with serve(lambda ws: mock_server(ws, received, accept), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            feed = PocketOptionWebSocketFeed(
                AUTH,
                AssetSelection(pairs="auto", max_pairs=5),
                60,
                [f"ws://127.0.0.1:{port}/socket.io/?EIO=4&transport=websocket"],
                subscribe_interval=0,
            )
            events = []
            agen = feed.events()
            try:
                async for ev in agen:
                    events.append(ev)
                    if len(events) >= n_events:
                        break
            finally:
                await agen.aclose()
            return events

    events = asyncio.run(asyncio.wait_for(main(), 10))
    return events, dict(received)


def test_websocket_feed_handshake_auth_subscribe_and_ticks():
    events, rx = run_feed()
    assert rx["origin"] == "https://pocketoption.com"
    assert rx["auth"] == AUTH
    assert rx["pong"] == "3"
    # auto-selection: OTC currency pairs only, highest payout first; SP500/EURUSD excluded
    assert rx["subs"] == [
        '42["changeSymbol",{"asset":"EURUSD_otc","period":60}]',
        '42["changeSymbol",{"asset":"GBPUSD_otc","period":60}]',
    ]
    kinds = [e.kind for e in events]
    assert kinds[0] == "assets" and "ticks" in kinds
    tick = next(e for e in events if e.kind == "ticks").ticks[0]
    assert (tick.asset, tick.price) == ("EURUSD_otc", 1.0812)


def test_websocket_feed_auth_rejection_is_fatal():
    with pytest.raises(AuthError):
        run_feed(accept=False)


def test_asset_selection_filters():
    adv = [
        AssetInfo("EURUSD_otc", payout=92, is_otc=True),
        AssetInfo("GBPUSD_otc", payout=70, is_otc=True),
        AssetInfo("#AAPL_otc", payout=90, is_otc=True),
        AssetInfo("USDJPY_otc", payout=88, is_otc=True, is_active=False),
    ]
    assert AssetSelection(min_payout=80).choose(adv) == ["EURUSD_otc"]
    assert AssetSelection(currency_pairs_only=False).choose(adv) == [
        "EURUSD_otc",
        "#AAPL_otc",
        "GBPUSD_otc",
    ]
    assert AssetSelection(pairs=["X_otc"]).choose(adv) == ["X_otc"]
    assert AssetSelection(fallback=["F_otc"]).choose([]) == ["F_otc"]
    assert looks_like_fx_pair("AUDCAD_otc") and not looks_like_fx_pair("BTCUSD_otc")


def test_browser_feed_taps_page_socket_and_injects_subscriptions(tmp_path):
    """A local page plays the web terminal (handshake + auth); the BrowserFeed must
    observe its frames and inject subscriptions through the page's own socket."""
    pytest.importorskip("playwright")
    import http.server
    import re
    import threading

    from otc_scanner.feeds.browser import BrowserFeed
    from websockets.asyncio.server import serve

    received = []

    async def main():
        async with serve(lambda ws: mock_server(ws, received), "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            page_js = f"""
              const ws = new WebSocket("ws://127.0.0.1:{port}/socket.io/?EIO=4&transport=websocket");
              ws.onmessage = (e) => {{
                if (typeof e.data !== "string") return;
                if (e.data.startsWith("0{{")) ws.send("40");
                else if (e.data.startsWith("40")) ws.send({json.dumps(AUTH)});
                else if (e.data === "2") ws.send("3");
              }};
            """
            (tmp_path / "index.html").write_text(f"<html><script>{page_js}</script></html>")
            handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(  # noqa: E731
                *a, directory=str(tmp_path), **k
            )
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            feed = BrowserFeed(
                AssetSelection(pairs="auto", max_pairs=5),
                60,
                account=f"http://127.0.0.1:{httpd.server_address[1]}/index.html",
                profile_dir=tmp_path / "profile",
                headless=True,
                subscribe_interval=0,
                socket_pattern=re.compile(r"127\.0\.0\.1:\d+/socket\.io"),
                executable_path=os.environ.get("OTC_SCANNER_CHROMIUM"),
            )
            events = []
            agen = feed.events()
            try:
                async for ev in agen:
                    events.append(ev)
                    if len(events) >= 2:
                        break
            finally:
                await agen.aclose()
                httpd.shutdown()
            return events

    try:
        events = asyncio.run(asyncio.wait_for(main(), 30))
    except Exception as exc:  # no Chromium in this environment
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Chromium not installed (python -m playwright install chromium)")
        raise
    rx = dict(received)
    assert rx["auth"] == AUTH  # sent by the page, not by us
    assert rx["subs"][0] == '42["changeSymbol",{"asset":"EURUSD_otc","period":60}]'  # injected
    assert [e.kind for e in events] == ["assets", "ticks"]
