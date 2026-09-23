"""Data-access layer. Import feeds lazily so optional deps (websockets, playwright)
are only needed for the feed you actually use."""

from .base import AssetSelection, AuthError, FeedError, FeedEvent, PriceFeed

__all__ = ["AssetSelection", "AuthError", "FeedError", "FeedEvent", "PriceFeed"]
