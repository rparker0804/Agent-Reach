"""Offline random-walk feed: exercise indicators and signal rules with no account
and no network. ``speed`` compresses time (speed=60 -> one 1m candle per second)."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator

from ..models import AssetInfo, Tick
from .base import FeedEvent, PriceFeed

DEFAULT_PAIRS = ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "AUDCAD_otc", "EURJPY_otc"]


class SimulatedFeed(PriceFeed):
    def __init__(
        self,
        pairs: list[str] | None = None,
        ticks_per_second: float = 2.0,
        speed: float = 60.0,
        seed: int | None = None,
        max_ticks: int | None = None,
        realtime: bool = True,  # False: emit as fast as possible (tests, back-runs)
    ) -> None:
        self.pairs = pairs or DEFAULT_PAIRS
        self.interval = 1.0 / ticks_per_second
        self.speed = speed
        self.rng = random.Random(seed)
        self.max_ticks = max_ticks
        self.realtime = realtime
        self._closed = False

    async def events(self) -> AsyncIterator[FeedEvent]:
        yield FeedEvent("assets", assets=[AssetInfo(p, p, 92, True) for p in self.pairs])
        price = {p: (150.0 if "JPY" in p else 1.1) * self.rng.uniform(0.9, 1.1) for p in self.pairs}
        drift = dict.fromkeys(self.pairs, 0.0)
        sim_time = float(int(time.time()))
        emitted = 0
        while not self._closed:
            sim_time += self.interval * self.speed
            batch = []
            for p in self.pairs:
                if self.rng.random() < 0.02:  # regime change -> trends and reversals
                    drift[p] = self.rng.gauss(0, 0.00015)
                price[p] *= 1 + drift[p] + self.rng.gauss(0, 0.0004)
                batch.append(Tick(p, sim_time, round(price[p], 5)))
            yield FeedEvent("ticks", ticks=batch)
            emitted += 1
            if self.max_ticks and emitted >= self.max_ticks:
                return
            await asyncio.sleep(self.interval if self.realtime else 0)

    async def close(self) -> None:
        self._closed = True
