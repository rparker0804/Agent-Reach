"""Tick -> OHLC candle aggregation with a bounded rolling window per asset."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

import pandas as pd

from .models import Candle, Tick


class CandleAggregator:
    def __init__(self, timeframe: int = 60, max_candles: int = 500) -> None:
        self.timeframe = timeframe
        self.max_candles = max_candles
        self._closed: dict[str, deque[Candle]] = {}
        self._forming: dict[str, Candle] = {}

    def add(self, tick: Tick) -> Candle | None:
        """Add a tick; returns the candle it closed, if any."""
        start = int(tick.timestamp // self.timeframe) * self.timeframe
        cur = self._forming.get(tick.asset)
        if cur is None:
            self._forming[tick.asset] = self._new(tick, start)
            return None
        if start == cur.start:
            cur.update(tick.price)
            return None
        if start < cur.start:
            return None  # late/out-of-order tick: drop rather than rewrite history
        self._history(tick.asset).append(cur)
        self._forming[tick.asset] = self._new(tick, start)
        return cur

    def seed(self, asset: str, ticks: Iterable[Tick]) -> int:
        """Back-fill from history; only ticks older than what we already hold are used."""
        existing = self._history(asset)
        if existing:
            cutoff_ts: int | None = existing[0].start
        elif asset in self._forming:
            cutoff_ts = self._forming[asset].start
        else:
            cutoff_ts = None
        tmp = CandleAggregator(self.timeframe, self.max_candles)
        for t in ticks:
            if cutoff_ts is None or t.timestamp < cutoff_ts:
                tmp.add(t)
        seeded = list(tmp._closed.get(asset, []))
        forming = tmp._forming.get(asset)
        if forming and cutoff_ts is not None:
            seeded.append(forming)  # the next real candle starts at cutoff, so this one is complete
        elif forming and asset not in self._forming:
            self._forming[asset] = forming
        merged = seeded + list(existing)
        self._closed[asset] = deque(merged[-self.max_candles :], maxlen=self.max_candles)
        return len(seeded)

    def candles(self, asset: str, include_forming: bool = False) -> list[Candle]:
        out = list(self._closed.get(asset, []))
        if include_forming and asset in self._forming:
            out.append(self._forming[asset])
        return out

    def frame(self, asset: str, include_forming: bool = False) -> pd.DataFrame:
        rows = self.candles(asset, include_forming)
        df = pd.DataFrame(
            [(c.start, c.open, c.high, c.low, c.close, c.ticks) for c in rows],
            columns=["time", "open", "high", "low", "close", "ticks"],
        )
        return df.set_index("time")

    @property
    def assets(self) -> list[str]:
        return sorted(set(self._closed) | set(self._forming))

    def _history(self, asset: str) -> deque[Candle]:
        if asset not in self._closed:
            self._closed[asset] = deque(maxlen=self.max_candles)
        return self._closed[asset]

    def _new(self, tick: Tick, start: int) -> Candle:
        p = tick.price
        return Candle(tick.asset, start, self.timeframe, p, p, p, p)
