import asyncio
import json

from otc_scanner.config import build_scanner, load_config
from otc_scanner.feeds.base import FeedEvent
from otc_scanner.feeds.simulated import SimulatedFeed
from otc_scanner.models import Tick


class Collect:
    def __init__(self):
        self.signals = []

    def report(self, s):
        self.signals.append(s)

    def status(self, rows):
        pass


def test_default_config_end_to_end_on_simulated_feed(tmp_path):
    cfg = load_config()
    cfg["output"] = {"console": False, "jsonl": str(tmp_path / "s.jsonl")}
    # 15s of simulated time per step, 4 steps per 1m candle -> 150 candles per pair
    feed = SimulatedFeed(ticks_per_second=1, speed=15, seed=7, max_ticks=4 * 150, realtime=False)
    scanner = build_scanner(cfg, feed)
    sink = Collect()
    scanner.reporters.append(sink)
    asyncio.run(scanner.run())

    assert sink.signals, "expected at least one signal over 150 candles on 5 pairs"
    assert {s.rule for s in sink.signals} <= {r.id for r in scanner.engine.rules}
    assert all(s.direction in ("CALL", "PUT") for s in sink.signals)
    lines = (tmp_path / "s.jsonl").read_text().splitlines()
    assert len(lines) == len(sink.signals)
    assert json.loads(lines[0])["asset"].endswith("_otc")


def test_user_config_merges_over_defaults(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "scanner:\n  timeframe: 30\nindicators:\n  - {id: rsi, type: rsi, period: 7}\nsignals:\n  rules:\n    - {type: threshold, column: rsi, below: 20}\n"
    )
    cfg = load_config(p)
    assert cfg["scanner"]["timeframe"] == 30 and cfg["scanner"]["pairs"] == "auto"
    scanner = build_scanner(cfg, SimulatedFeed(max_ticks=1))
    assert scanner.timeframe == 30 and scanner.indicators.min_bars == 21


def test_history_seed_makes_pair_warm_immediately():
    cfg = load_config()
    cfg["output"] = {"console": False}
    scanner = build_scanner(cfg, SimulatedFeed(max_ticks=1))
    hist = [Tick("EURUSD_otc", 60 * i, 1 + (i % 7) / 1000) for i in range(100)]
    scanner.handle(FeedEvent("history", ticks=hist, asset="EURUSD_otc"))
    assert len(scanner.candles.candles("EURUSD_otc")) >= scanner.indicators.min_bars
