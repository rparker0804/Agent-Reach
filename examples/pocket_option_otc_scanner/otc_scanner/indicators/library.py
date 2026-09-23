"""Built-in indicators (pure pandas, no TA-Lib needed)."""

from __future__ import annotations

import pandas as pd

from .base import Indicator, register


def _src(df: pd.DataFrame, source: str) -> pd.Series:
    if source == "hl2":
        return (df["high"] + df["low"]) / 2
    if source == "hlc3":
        return (df["high"] + df["low"] + df["close"]) / 3
    return df[source]


def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False, min_periods=period).mean()


@register("sma")
class SMA(Indicator):
    def __init__(self, id: str, period: int = 20, source: str = "close") -> None:
        super().__init__(id, period=period, source=source)
        self.period, self.source = period, source

    @property
    def min_bars(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"": _src(df, self.source).rolling(self.period).mean()}

    def label(self) -> str:
        return f"SMA({self.period})"


@register("ema")
class EMA(Indicator):
    def __init__(self, id: str, period: int = 20, source: str = "close") -> None:
        super().__init__(id, period=period, source=source)
        self.period, self.source = period, source

    @property
    def min_bars(self) -> int:
        return self.period * 2  # let the EMA seed settle

    def compute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        return {"": ema(_src(df, self.source), self.period)}

    def label(self) -> str:
        return f"EMA({self.period})"


@register("rsi")
class RSI(Indicator):
    """Wilder's RSI (same smoothing as TradingView / TA-Lib)."""

    def __init__(self, id: str, period: int = 14, source: str = "close") -> None:
        super().__init__(id, period=period, source=source)
        self.period, self.source = period, source

    @property
    def min_bars(self) -> int:
        return self.period * 3

    def compute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        delta = _src(df, self.source).diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        alpha = 1 / self.period
        avg_gain = gain.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()
        avg_loss = loss.ewm(alpha=alpha, adjust=False, min_periods=self.period).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
        rsi = rsi.where(avg_loss != 0, 100.0).where(avg_gain.notna())
        return {"": rsi}

    def label(self) -> str:
        return f"RSI({self.period})"


@register("macd")
class MACD(Indicator):
    def __init__(
        self, id: str, fast: int = 12, slow: int = 26, signal: int = 9, source: str = "close"
    ) -> None:
        super().__init__(id, fast=fast, slow=slow, signal=signal, source=source)
        self.fast, self.slow, self.signal, self.source = fast, slow, signal, source

    @property
    def min_bars(self) -> int:
        return self.slow + self.signal * 2

    def compute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        s = _src(df, self.source)
        line = ema(s, self.fast) - ema(s, self.slow)
        sig = line.ewm(span=self.signal, adjust=False, min_periods=self.signal).mean()
        return {"macd": line, "signal": sig, "hist": line - sig}

    def label(self) -> str:
        return f"MACD({self.fast},{self.slow},{self.signal})"


@register("bollinger")
class Bollinger(Indicator):
    def __init__(
        self, id: str, period: int = 20, stddev: float = 2.0, source: str = "close"
    ) -> None:
        super().__init__(id, period=period, stddev=stddev, source=source)
        self.period, self.stddev, self.source = period, stddev, source

    @property
    def min_bars(self) -> int:
        return self.period

    def compute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        s = _src(df, self.source)
        mid = s.rolling(self.period).mean()
        sd = s.rolling(self.period).std(ddof=0)
        return {"upper": mid + self.stddev * sd, "middle": mid, "lower": mid - self.stddev * sd}

    def label(self) -> str:
        return f"BB({self.period},{self.stddev:g})"


@register("stochastic")
class Stochastic(Indicator):
    def __init__(self, id: str, k: int = 14, d: int = 3, smooth: int = 3) -> None:
        super().__init__(id, k=k, d=d, smooth=smooth)
        self.k, self.d, self.smooth = k, d, smooth

    @property
    def min_bars(self) -> int:
        return self.k + self.smooth + self.d

    def compute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        lo = df["low"].rolling(self.k).min()
        hi = df["high"].rolling(self.k).max()
        raw = 100 * (df["close"] - lo) / (hi - lo).replace(0, pd.NA)
        k = raw.astype(float).rolling(self.smooth).mean()
        return {"k": k, "d": k.rolling(self.d).mean()}

    def label(self) -> str:
        return f"STOCH({self.k},{self.d},{self.smooth})"
