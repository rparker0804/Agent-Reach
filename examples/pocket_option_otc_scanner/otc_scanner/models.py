"""Plain data types shared by every layer (feed -> candles -> indicators -> signals)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True)
class Tick:
    """One price update for one asset."""

    asset: str
    timestamp: float  # unix seconds (float, sub-second precision)
    price: float


@dataclass
class Candle:
    """OHLC bar. ``start`` is the unix time the bar opened (aligned to the timeframe)."""

    asset: str
    start: int
    timeframe: int
    open: float
    high: float
    low: float
    close: float
    ticks: int = 1

    def update(self, price: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.ticks += 1


@dataclass(frozen=True)
class AssetInfo:
    """An asset advertised by the platform (from the ``updateAssets`` event)."""

    symbol: str
    name: str = ""
    payout: int | None = None
    is_otc: bool = False
    is_active: bool = True


@dataclass(frozen=True)
class Signal:
    """A flagged trading opportunity."""

    asset: str
    timestamp: float
    rule: str  # rule name from config, e.g. "rsi_extremes"
    direction: str  # "CALL" (up), "PUT" (down) or "INFO"
    condition: str  # human-readable trigger, e.g. "RSI(14)=27.9 < 30 (oversold)"
    price: float
    values: dict[str, float] = field(default_factory=dict)

    @property
    def time_utc(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc)
