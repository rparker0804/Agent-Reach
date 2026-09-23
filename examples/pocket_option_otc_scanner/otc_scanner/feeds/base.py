"""Data-feed contract. The scanner only ever sees :class:`FeedEvent` objects, so a
new data source (another broker, a CSV replay, a paid data vendor) is one new class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field

from ..models import AssetInfo, Tick


@dataclass
class FeedEvent:
    kind: str  # "ticks" (live), "history" (bulk back-fill for one asset), "assets"
    ticks: list[Tick] = field(default_factory=list)
    assets: list[AssetInfo] = field(default_factory=list)
    asset: str = ""


class FeedError(RuntimeError):
    pass


class AuthError(FeedError):
    """Session rejected — the SSID is missing, expired or for the wrong account type."""


class PriceFeed(ABC):
    @abstractmethod
    def events(self) -> AsyncIterator[FeedEvent]:
        """Yield events until :meth:`close` is called or an unrecoverable error occurs."""

    async def close(self) -> None:  # noqa: B027 - optional hook
        pass


@dataclass
class AssetSelection:
    """Which assets to scan.

    ``pairs`` is either an explicit list (``["EURUSD_otc", ...]``) or ``"auto"``,
    meaning: every OTC asset the platform advertises, filtered by the options below.
    """

    pairs: list[str] | str = "auto"
    currency_pairs_only: bool = True  # drop OTC stocks/commodities/indices/crypto
    min_payout: int | None = None
    max_pairs: int | None = None
    fallback: list[str] = field(default_factory=list)

    def choose(self, advertised: Iterable[AssetInfo]) -> list[str]:
        if isinstance(self.pairs, list):
            return list(self.pairs)
        chosen = []
        for a in advertised:
            if not (a.is_otc and a.is_active):
                continue
            if self.currency_pairs_only and not looks_like_fx_pair(a.symbol):
                continue
            if self.min_payout is not None and (a.payout or 0) < self.min_payout:
                continue
            chosen.append(a)
        chosen.sort(key=lambda a: -(a.payout or 0))
        symbols = [a.symbol for a in chosen] or list(self.fallback)
        return symbols[: self.max_pairs] if self.max_pairs else symbols


_CCY = {
    "AUD", "CAD", "CHF", "CNH", "EUR", "GBP", "JPY", "NZD", "USD", "AED", "BHD", "CNY",
    "EGP", "HKD", "IDR", "ILS", "INR", "JOD", "KES", "KWD", "LBP", "MAD", "MXN", "MYR",
    "NGN", "OMR", "PHP", "PKR", "QAR", "RUB", "SAR", "SGD", "THB", "TND", "TRY", "UAH",
    "VND", "YER", "ZAR", "BRL", "ARS", "CLP", "COP", "BDT", "DZD", "SEK", "NOK", "PLN",
}  # fmt: skip


def looks_like_fx_pair(symbol: str) -> bool:
    base = symbol.upper().removesuffix("_OTC")
    return len(base) == 6 and base[:3] in _CCY and base[3:] in _CCY
