"""Capture your Pocket Option auth frame (``PO_SSID``) without DevTools.

    python -m otc_scanner.get_ssid                 # demo account, saves to ~/.otc_scanner/po_ssid
    python -m otc_scanner.get_ssid --gh-secret     # ...and uploads it as the repo's PO_SSID secret

A Chromium window opens on the Pocket Option terminal. Log in as usual. The page
sends its own ``42["auth",{...}]`` frame when it connects to the quote server; this
script copies that frame from the page's socket, saves it with owner-only permissions
and closes the window. Your password is typed into Pocket Option's own page and is
never seen or stored by this script.

!!! FRAGILE — PLATFORM-DEPENDENT !!!  Relies on the same terminal URLs, socket URL
pattern and auth-frame prefix as the browser feed (see feeds/browser.py, protocol.py).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .feeds.browser import SOCKET_PATTERN, TERMINAL_URLS

AUTH_PREFIX = '42["auth"'
DEFAULT_OUT = "~/.otc_scanner/po_ssid"


async def capture_auth_frame(
    url: str,
    profile_dir: str | Path = "~/.otc_scanner/browser-profile",
    *,
    headless: bool = False,
    timeout: float = 300.0,
    socket_pattern: re.Pattern[str] | str = SOCKET_PATTERN,
    executable_path: str | None = None,
) -> str:
    """Open ``url`` and return the first auth frame the page sends to the quote socket."""
    from playwright.async_api import async_playwright

    profile = Path(profile_dir).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    found: asyncio.Future[str] = asyncio.get_running_loop().create_future()

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(profile),
            headless=headless,
            executable_path=executable_path,
            viewport={"width": 1400, "height": 900},
        )

        def route(ws: Any) -> None:
            server = ws.connect_to_server()

            def page_to_server(msg: str | bytes) -> None:
                server.send(msg)
                if isinstance(msg, str) and msg.startswith(AUTH_PREFIX) and not found.done():
                    found.set_result(msg)

            ws.on_message(page_to_server)
            server.on_message(lambda msg: ws.send(msg))

        await context.route_web_socket(socket_pattern, route)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        try:
            return await asyncio.wait_for(found, timeout)
        finally:
            await context.close()


def save_frame(frame: str, path: str | Path) -> Path:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(frame)
    os.chmod(p, 0o600)  # also tighten a pre-existing file
    return p


def describe(frame: str) -> str:
    """Non-secret summary: account type and uid, never the session value."""
    demo = re.search(r'"isDemo"\s*:\s*(\d)', frame)
    uid = re.search(r'"uid"\s*:\s*(\d+)', frame)
    kind = {"1": "DEMO", "0": "REAL"}.get(demo.group(1) if demo else "", "unknown")
    return f"{kind} account" + (f", uid {uid.group(1)}" if uid else "")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="otc_scanner.get_ssid", description=__doc__.split("\n")[0])
    p.add_argument("--account", default="demo", help="demo | real | full terminal URL")
    p.add_argument("--out", default=DEFAULT_OUT, help=f"where to save it (default {DEFAULT_OUT})")
    p.add_argument("--profile-dir", default="~/.otc_scanner/browser-profile")
    p.add_argument("--timeout", type=float, default=300, help="seconds to wait for login")
    p.add_argument("--executable-path", help="use an installed Chrome/Chromium binary")
    p.add_argument(
        "--gh-secret",
        action="store_true",
        help="also upload it as the PO_SSID Actions secret of the current repo (needs `gh`)",
    )
    p.add_argument("--repo", help="with --gh-secret: OWNER/REPO (default: repo of the cwd)")
    p.add_argument("--print", action="store_true", help="also print the frame (it is a secret!)")
    args = p.parse_args(argv)

    url = TERMINAL_URLS.get(args.account, args.account)
    print(f"Opening {url}\nLog in in the browser window; it closes by itself once captured.")
    try:
        frame = asyncio.run(
            capture_auth_frame(
                url, args.profile_dir, timeout=args.timeout, executable_path=args.executable_path
            )
        )
    except asyncio.TimeoutError:
        print(f"No auth frame seen within {args.timeout:.0f}s. Did the terminal finish loading?")
        return 1

    path = save_frame(frame, args.out)
    print(f"Captured auth frame for {describe(frame)} -> {path} (owner-only permissions)")
    if '"isDemo":0' in frame.replace(" ", ""):
        print("WARNING: this is a REAL-money session. Prefer --account demo for scanning.")
    if args.print:
        print(frame)

    if args.gh_secret:
        if not shutil.which("gh"):
            print("`gh` (GitHub CLI) not found: install it or add the secret by hand.")
            return 1
        cmd = ["gh", "secret", "set", "PO_SSID"] + (["--repo", args.repo] if args.repo else [])
        result = subprocess.run(cmd, input=frame, text=True)
        if result.returncode != 0:
            return result.returncode
        print("Uploaded as the PO_SSID Actions secret.")
    else:
        print(
            'Use it locally:   export PO_SSID="$(cat ' + str(path) + ')"\n'
            "Upload to GitHub: gh secret set PO_SSID < " + str(path)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
