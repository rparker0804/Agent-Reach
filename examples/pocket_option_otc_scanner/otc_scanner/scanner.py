"""Orchestrator: feed -> candles -> indicators -> signals -> reporters.

Holds no knowledge of Pocket Option, indicator maths or rule logic; each of those is
injected, so any layer can be replaced without touching the others.
"""

from __future__ import annotations

import time
from collections import defaultdict

from loguru import logger

from .candles import CandleAggregator
from .feeds.base import FeedEvent, PriceFeed
from .indicators import IndicatorSet
from .models import Signal
from .output import Reporter
from .signals import SignalEngine


class Scanner:
    def __init__(
        self,
        feed: PriceFeed,
        indicators: IndicatorSet,
        engine: SignalEngine,
        reporters: list[Reporter],
        timeframe: int = 60,
        max_candles: int = 500,
        status_interval: float = 60.0,
    ) -> None:
        self.feed = feed
        self.indicators = indicators
        self.engine = engine
        self.reporters = reporters
        self.timeframe = timeframe
        self.candles = CandleAggregator(timeframe, max_candles)
        self.status_interval = status_interval
        self.signals: list[Signal] = []
        self._tick_counts: dict[str, int] = defaultdict(int)
        self._last_price: dict[str, float] = {}
        self._last_status = time.monotonic()
        # Only the last N bars are needed for the indicators; bounds per-close cost.
        self._window = max(self.indicators.min_bars * 3, 100)

    async def run(self) -> None:
        try:
            async for event in self.feed.events():
                self.handle(event)
                self._maybe_status()
        finally:
            await self.feed.close()

    def handle(self, event: FeedEvent) -> list[Signal]:
        fired: list[Signal] = []
        if event.kind == "ticks":
            for tick in event.ticks:
                self._tick_counts[tick.asset] += 1
                self._last_price[tick.asset] = tick.price
                if self.candles.add(tick) is not None:
                    fired += self._on_candle_close(tick.asset)
        elif event.kind == "history" and event.ticks:
            n = self.candles.seed(event.asset, event.ticks)
            logger.info(f"{event.asset}: back-filled {n} candles from history")
        elif event.kind == "assets":
            otc = [a for a in event.assets if a.is_otc]
            logger.info(f"Platform advertises {len(event.assets)} assets ({len(otc)} OTC)")
        return fired

    def _on_candle_close(self, asset: str) -> list[Signal]:
        df = self.candles.frame(asset)
        if len(df) < self.indicators.min_bars:
            return []
        frame = self.indicators.compute(df.tail(self._window))
        fired = self.engine.evaluate(asset, frame, self.timeframe)
        for s in fired:
            self.signals.append(s)
            for r in self.reporters:
                r.report(s)
        return fired

    def _maybe_status(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_status
        if self.status_interval <= 0 or elapsed < self.status_interval:
            return
        rows = []
        for asset in self.candles.assets:
            bars = len(self.candles.candles(asset))
            rows.append(
                {
                    "asset": asset,
                    "ticks_per_min": self._tick_counts.get(asset, 0) * 60 / elapsed,
                    "bars": bars,
                    "min_bars": self.indicators.min_bars,
                    "warm": bars >= self.indicators.min_bars,
                    "last_price": self._last_price.get(asset),
                }
            )
        for r in self.reporters:
            r.status(rows)
        self._tick_counts.clear()
        self._last_status = now
