"""Config loading and object wiring. Defaults come from ``config.example.yaml``; a
user config is deep-merged over it (lists replace, dicts merge)."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from .feeds.base import AssetSelection, PriceFeed
from .indicators import IndicatorSet
from .output import build_reporters
from .scanner import Scanner
from .signals import SignalEngine

DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "config.example.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg = yaml.safe_load(DEFAULTS_PATH.read_text(encoding="utf-8"))
    if path:
        user = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8")) or {}
        cfg = _merge(cfg, user)
    return cfg


def _merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def build_selection(sc: dict[str, Any]) -> AssetSelection:
    pairs = sc.get("pairs", "auto")
    if isinstance(pairs, str) and pairs != "auto":
        pairs = [p.strip() for p in pairs.split(",") if p.strip()]
    return AssetSelection(
        pairs=pairs,
        currency_pairs_only=sc.get("currency_pairs_only", True),
        min_payout=sc.get("min_payout"),
        max_pairs=sc.get("max_pairs"),
        fallback=list(sc.get("fallback_pairs") or []),
    )


def build_feed(cfg: dict[str, Any]) -> PriceFeed:
    fc, sc = cfg["feed"], cfg["scanner"]
    kind = fc["type"]
    selection = build_selection(sc)
    period = int(sc["timeframe"])
    templates = fc.get("subscribe_templates")

    if kind == "simulated":
        from .feeds.simulated import SimulatedFeed

        sim = fc.get("simulated", {})
        pairs = selection.pairs if isinstance(selection.pairs, list) else None
        return SimulatedFeed(
            pairs, sim.get("ticks_per_second", 20), sim.get("speed", 300), sim.get("seed")
        )

    if kind == "websocket":
        from .feeds.pocket_option import PocketOptionWebSocketFeed

        ws = fc.get("websocket", {})
        env = ws.get("auth_env", "PO_SSID")
        auth = os.environ.get(env, "").strip()
        if not auth:
            raise SystemExit(
                f'{env} is not set. Copy the 42["auth",{{...}}] frame from your browser\'s '
                f"DevTools (see README → Authentication) and export {env}='<frame>'."
            )
        return PocketOptionWebSocketFeed(
            auth,
            selection,
            period,
            ws.get("regions"),
            user_agent=ws.get("user_agent"),
            subscribe_templates=templates,
            subscribe_interval=ws.get("subscribe_interval", 0.3),
            idle_timeout=ws.get("idle_timeout", 60),
        )

    if kind == "browser":
        from .feeds.browser import BrowserFeed

        br = fc.get("browser", {})
        return BrowserFeed(
            selection,
            period,
            account=br.get("account", "demo"),
            profile_dir=br.get("profile_dir", "~/.otc_scanner/browser-profile"),
            headless=br.get("headless", False),
            inject_subscriptions=br.get("inject_subscriptions", True),
            subscribe_templates=templates,
            subscribe_interval=br.get("subscribe_interval", 0.3),
            executable_path=br.get("executable_path"),
        )

    raise SystemExit(f"Unknown feed.type {kind!r} (websocket | browser | simulated)")


def build_scanner(cfg: dict[str, Any], feed: PriceFeed | None = None) -> Scanner:
    indicators = IndicatorSet.from_config(cfg["indicators"])
    sig = cfg["signals"]
    engine = SignalEngine.from_config(
        sig["rules"], indicators.labels(), sig.get("cooldown_bars", 3)
    )
    # Validate rule column references against what the indicators actually produce.
    import pandas as pd

    probe = pd.DataFrame({c: [1.0] * 3 for c in ("open", "high", "low", "close")})
    engine.validate(set(indicators.compute(probe).columns))
    sc = cfg["scanner"]
    return Scanner(
        feed or build_feed(cfg),
        indicators,
        engine,
        build_reporters(cfg.get("output", {})),
        timeframe=int(sc["timeframe"]),
        max_candles=int(sc.get("max_candles", 500)),
        status_interval=float(sc.get("status_interval", 60)),
    )
