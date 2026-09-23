"""CLI: ``python -m otc_scanner [--config config.yaml] [--feed simulated] ...``"""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from .config import build_scanner, load_config
from .feeds.base import AuthError
from .indicators import available
from .signals import available_rules


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="otc_scanner", description="Pocket Option OTC signal scanner")
    p.add_argument("-c", "--config", help="YAML config (defaults: config.example.yaml)")
    p.add_argument(
        "--feed", choices=["websocket", "browser", "simulated"], help="override feed.type"
    )
    p.add_argument("--pairs", help='override scanner.pairs: "auto" or "EURUSD_otc,GBPUSD_otc"')
    p.add_argument("--timeframe", type=int, help="override candle size in seconds")
    p.add_argument("--jsonl", help="also append signals to this JSON-lines file")
    p.add_argument("--list", action="store_true", help="list indicator and rule types, then exit")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    if args.list:
        print("indicators:", ", ".join(available()))
        print("rules:     ", ", ".join(available_rules()))
        return 0

    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if args.verbose else "INFO",
        format="<dim>{time:HH:mm:ss}</dim> {level: <7} {message}",
    )

    cfg = load_config(args.config)
    if args.feed:
        cfg["feed"]["type"] = args.feed
    if args.pairs:
        cfg["scanner"]["pairs"] = args.pairs
    if args.timeframe:
        cfg["scanner"]["timeframe"] = args.timeframe
    if args.jsonl:
        cfg.setdefault("output", {})["jsonl"] = args.jsonl

    scanner = build_scanner(cfg)
    logger.info(
        f"feed={cfg['feed']['type']} timeframe={cfg['scanner']['timeframe']}s "
        f"warm-up={scanner.indicators.min_bars} candles, rules={[r.id for r in scanner.engine.rules]}"
    )
    try:
        asyncio.run(scanner.run())
    except KeyboardInterrupt:
        logger.info(f"Stopped. {len(scanner.signals)} signal(s) this session.")
    except AuthError as exc:
        logger.error(str(exc))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
