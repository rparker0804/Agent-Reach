"""Indicator contract + registry.

An indicator takes an OHLC DataFrame (columns open/high/low/close, oldest first) and
returns one or more named Series. The scanner joins them onto the frame as columns:

* single-output indicators -> column ``<id>``            (e.g. ``rsi``)
* multi-output indicators  -> columns ``<id>.<output>``  (e.g. ``macd.hist``)

To add one: subclass :class:`Indicator`, decorate with ``@register("my_type")``,
then reference ``type: my_type`` from the config. Nothing else changes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import pandas as pd

_REGISTRY: dict[str, type[Indicator]] = {}


def register(type_name: str):
    def deco(cls: type[Indicator]) -> type[Indicator]:
        _REGISTRY[type_name] = cls
        cls.type_name = type_name
        return cls

    return deco


def available() -> list[str]:
    return sorted(_REGISTRY)


class Indicator(ABC):
    type_name: str = ""

    def __init__(self, id: str, **params: Any) -> None:
        self.id = id
        self.params = params

    @property
    @abstractmethod
    def min_bars(self) -> int:
        """Closed candles needed before the output is meaningful."""

    @abstractmethod
    def compute(self, df: pd.DataFrame) -> dict[str, pd.Series]:
        """Return ``{output_name: series}``; use ``""`` as the name for a single output."""

    def label(self) -> str:
        args = ",".join(str(v) for v in self.params.values())
        return f"{self.type_name.upper()}({args})" if args else self.type_name.upper()


def build_indicator(spec: dict[str, Any]) -> Indicator:
    spec = dict(spec)
    type_name = spec.pop("type")
    ind_id = spec.pop("id", type_name)
    params = spec.pop("params", {}) or {}
    params.update(spec)  # allow params inline too: {type: rsi, period: 14}
    if type_name not in _REGISTRY:
        raise ValueError(f"Unknown indicator type {type_name!r}; available: {available()}")
    return _REGISTRY[type_name](ind_id, **params)


class IndicatorSet:
    def __init__(self, indicators: list[Indicator]) -> None:
        ids = [i.id for i in indicators]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate indicator ids: {ids}")
        self.indicators = indicators

    @classmethod
    def from_config(cls, specs: list[dict[str, Any]]) -> IndicatorSet:
        return cls([build_indicator(s) for s in specs])

    @property
    def min_bars(self) -> int:
        return max((i.min_bars for i in self.indicators), default=0)

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for ind in self.indicators:
            for name, series in ind.compute(df).items():
                out[f"{ind.id}.{name}" if name else ind.id] = series
        return out

    def labels(self) -> dict[str, str]:
        return {i.id: i.label() for i in self.indicators}
