"""Turns rule states on the latest closed candle into :class:`Signal` objects."""

from __future__ import annotations

from typing import Any

import pandas as pd

from ..models import Signal
from .rules import Rule, build_rule

CANDLE_COLUMNS = {"open", "high", "low", "close", "ticks"}


class SignalEngine:
    def __init__(self, rules: list[Rule], cooldown_bars: int = 3) -> None:
        self.rules = rules
        self.cooldown_bars = cooldown_bars
        self._last_fired: dict[tuple[str, str], int] = {}

    @classmethod
    def from_config(
        cls,
        specs: list[dict[str, Any]],
        labels: dict[str, str] | None = None,
        cooldown_bars: int = 3,
    ) -> SignalEngine:
        rules = [build_rule(s, labels) for s in specs if s.get("enabled", True)]
        return cls(rules, cooldown_bars)

    def validate(self, indicator_columns: set[str]) -> None:
        """Fail fast on typos: every column a rule reads must exist."""
        known = indicator_columns | CANDLE_COLUMNS
        for rule in self.rules:
            missing = [c for c in rule.columns() if c not in known]
            if missing:
                raise ValueError(
                    f"rule {rule.id!r} references unknown column(s) {missing}; "
                    f"available: {sorted(known)}"
                )

    def evaluate(self, asset: str, frame: pd.DataFrame, timeframe: int) -> list[Signal]:
        """Evaluate every rule on the last row of ``frame`` (the newest closed candle)."""
        if len(frame) < 2:
            return []
        cur, prev = frame.iloc[-1], frame.iloc[-2]
        bar_start = int(frame.index[-1])
        signals = []
        for rule in self.rules:
            if not rule.ready(cur):
                continue
            direction, reason = rule.state(cur)
            if direction is None:
                continue
            if rule.mode == "edge":
                if not rule.ready(prev) or rule.state(prev)[0] == direction:
                    continue
            key = (asset, rule.id)
            last = self._last_fired.get(key)
            if last is not None and bar_start - last < self.cooldown_bars * timeframe:
                continue
            self._last_fired[key] = bar_start
            signals.append(
                Signal(
                    asset=asset,
                    timestamp=bar_start + timeframe,  # the moment the candle closed
                    rule=rule.id,
                    direction=direction,
                    condition=reason,
                    price=float(cur["close"]),
                    values={c: float(cur[c]) for c in rule.columns() if c in cur.index},
                )
            )
        return signals
