"""Signal rules. All signal logic lives here, driven by config.

A rule looks at one row of the indicator frame and reports a *state*: a direction
(``"CALL"`` / ``"PUT"``) plus a human-readable reason, or ``None`` if the condition
is not met. The engine turns states into signals:

* ``mode: edge`` (default) fires only when the state changes to a direction —
  e.g. the bar RSI first drops below 30, or the bar two MAs actually cross.
* ``mode: while`` fires on every bar the state holds (subject to cooldown).

Column references are indicator output columns (``rsi``, ``macd.hist``, ``bb.lower``)
or raw candle fields (``open``/``high``/``low``/``close``); numbers are accepted too.

To add a rule: subclass :class:`Rule`, decorate with ``@register_rule("name")``,
reference ``type: name`` in the config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

State = tuple[str | None, str]
_RULES: dict[str, type[Rule]] = {}
DIRECTIONS = {"CALL", "PUT", "INFO"}


def register_rule(type_name: str):
    def deco(cls: type[Rule]) -> type[Rule]:
        _RULES[type_name] = cls
        return cls

    return deco


def available_rules() -> list[str]:
    return sorted(_RULES)


class Rule(ABC):
    def __init__(self, id: str, mode: str = "edge", labels: dict[str, str] | None = None) -> None:
        if mode not in ("edge", "while"):
            raise ValueError(f"rule {id}: mode must be 'edge' or 'while'")
        self.id = id
        self.mode = mode
        self.labels = labels or {}

    @abstractmethod
    def state(self, row: pd.Series) -> State:
        """Direction the condition points to on this bar (or None) and why."""

    @abstractmethod
    def columns(self) -> list[str]:
        """Columns this rule reads (used to validate config against indicators)."""

    def ready(self, row: pd.Series) -> bool:
        """All referenced values present (indicator warmed up) on this bar."""
        return all(self._value(row, c) is not None for c in self.columns())

    # helpers
    def _value(self, row: pd.Series, ref: str | float) -> float | None:
        if isinstance(ref, (int, float)):
            return float(ref)
        v = row.get(ref)
        return None if v is None or pd.isna(v) else float(v)

    def _name(self, ref: str | float) -> str:
        if isinstance(ref, (int, float)):
            return f"{ref:g}"
        base, _, part = ref.partition(".")
        label = self.labels.get(base, base)
        return f"{label}.{part}" if part else label


def _direction(d: str | None) -> str | None:
    if d is None:
        return None
    d = d.upper()
    if d not in DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(DIRECTIONS)} or null, got {d!r}")
    return d


@register_rule("threshold")
class Threshold(Rule):
    """``column`` below ``below`` -> ``below_direction``; above ``above`` -> ``above_direction``.

    Default reading is mean-reversion (oversold -> CALL, overbought -> PUT)."""

    def __init__(
        self,
        id: str,
        column: str,
        below: float | None = None,
        above: float | None = None,
        below_direction: str | None = "CALL",
        above_direction: str | None = "PUT",
        below_label: str = "oversold",
        above_label: str = "overbought",
        **kw: Any,
    ) -> None:
        super().__init__(id, **kw)
        if below is None and above is None:
            raise ValueError(f"rule {id}: set at least one of below/above")
        self.column, self.below, self.above = column, below, above
        self.below_dir, self.above_dir = _direction(below_direction), _direction(above_direction)
        self.below_label, self.above_label = below_label, above_label

    def columns(self) -> list[str]:
        return [self.column]

    def state(self, row: pd.Series) -> State:
        v = self._value(row, self.column)
        if v is None:
            return None, ""
        name = self._name(self.column)
        if self.below is not None and v < self.below and self.below_dir:
            return self.below_dir, f"{name}={v:.2f} < {self.below:g} ({self.below_label})"
        if self.above is not None and v > self.above and self.above_dir:
            return self.above_dir, f"{name}={v:.2f} > {self.above:g} ({self.above_label})"
        return None, ""


@register_rule("crossover")
class Crossover(Rule):
    """``fast`` crossing above ``slow`` -> ``up``; crossing below -> ``down``.

    Examples: EMA(9)/EMA(21) trend cross, MACD line vs signal, MACD hist vs 0,
    or ``close`` vs ``bb.upper`` with ``up: PUT`` for a band-break reversal."""

    def __init__(
        self,
        id: str,
        fast: str | float,
        slow: str | float,
        up: str | None = "CALL",
        down: str | None = "PUT",
        **kw: Any,
    ) -> None:
        super().__init__(id, **kw)
        self.fast, self.slow = fast, slow
        self.up, self.down = _direction(up), _direction(down)

    def columns(self) -> list[str]:
        return [c for c in (self.fast, self.slow) if isinstance(c, str)]

    def state(self, row: pd.Series) -> State:
        f, s = self._value(row, self.fast), self._value(row, self.slow)
        if f is None or s is None or f == s:
            return None, ""
        fn, sn = self._name(self.fast), self._name(self.slow)
        verb = "crossed " if self.mode == "edge" else ""
        if f > s:
            return self.up, f"{fn} {verb}above {sn} ({_fmt(f)} > {_fmt(s)})"
        return self.down, f"{fn} {verb}below {sn} ({_fmt(f)} < {_fmt(s)})"


@register_rule("all_of")
class AllOf(Rule):
    """Confluence: fires when every sub-rule points the same direction on the same bar."""

    def __init__(self, id: str, rules: list[dict[str, Any]], **kw: Any) -> None:
        super().__init__(id, **kw)
        self.children = [
            build_rule({**r, "id": r.get("id", f"{id}[{i}]")}, self.labels)
            for i, r in enumerate(rules)
        ]

    def columns(self) -> list[str]:
        return [c for r in self.children for c in r.columns()]

    def state(self, row: pd.Series) -> State:
        states = [r.state(row) for r in self.children]
        dirs = {d for d, _ in states}
        if len(dirs) == 1 and None not in dirs:
            return dirs.pop(), " AND ".join(reason for _, reason in states)
        return None, ""


def _fmt(v: float) -> str:
    return f"{v:.5f}" if abs(v) < 10 else f"{v:.3f}"


def build_rule(spec: dict[str, Any], labels: dict[str, str] | None = None) -> Rule:
    spec = dict(spec)
    type_name = spec.pop("type")
    rule_id = spec.pop("id", type_name)
    spec.pop("enabled", None)
    if type_name not in _RULES:
        raise ValueError(f"Unknown rule type {type_name!r}; available: {available_rules()}")
    return _RULES[type_name](rule_id, labels=labels, **spec)
