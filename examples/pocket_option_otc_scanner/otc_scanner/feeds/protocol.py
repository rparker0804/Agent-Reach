"""Pocket Option wire protocol: encode outgoing frames, decode incoming ones.

!!! FRAGILE — PLATFORM-DEPENDENT !!!
Everything in this module is reverse-engineered from the traffic the Pocket Option
web terminal exchanges with its quote servers (and cross-checked against community
open-source clients). None of it is a documented or stable API. Expect to update
this file when the platform ships front-end/back-end changes. Specifically:

* Transport: Socket.IO over Engine.IO v4 (``?EIO=4&transport=websocket``).
* Auth: the client re-sends the browser's own frame ``42["auth",{...}]``.
* Events used (names and payload shapes are all unofficial):
    - ``successauth`` / ``NotAuthorized``   auth result
    - ``updateStream``                       live ticks  [[asset, ts, price], ...]
    - ``updateAssets``                       asset list (positional arrays)
    - ``updateHistoryNewFast``/``loadHistoryPeriod``  history after subscribing
* Binary payloads arrive as a text "placeholder" frame
  (``451-["updateStream",{"_placeholder":true,"num":0}]``) followed by a binary
  frame carrying the JSON body.

The decoder is deliberately lenient: unknown events are ignored, and binary frames
without a known placeholder are classified by shape. Keeping all protocol knowledge
here means the data feeds, indicators and signal logic never need to change when
the wire format does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from ..models import AssetInfo, Tick

# FRAGILE: quote-server hosts. Taken from the web terminal / community clients.
REGION_URLS: dict[str, str] = {
    "DEMO": "wss://demo-api-eu.po.market/socket.io/?EIO=4&transport=websocket",
    "EUROPA": "wss://api-eu.po.market/socket.io/?EIO=4&transport=websocket",
    "SEYCHELLES": "wss://api-sc.po.market/socket.io/?EIO=4&transport=websocket",
    "HONGKONG": "wss://api-hk.po.market/socket.io/?EIO=4&transport=websocket",
    "FRANCE": "wss://api-fr.po.market/socket.io/?EIO=4&transport=websocket",
    "FINLAND": "wss://api-fin.po.market/socket.io/?EIO=4&transport=websocket",
    "UNITED_STATES": "wss://api-us-north.po.market/socket.io/?EIO=4&transport=websocket",
    "INDIA": "wss://api-in.po.market/socket.io/?EIO=4&transport=websocket",
    "ASIA": "wss://api-asia.po.market/socket.io/?EIO=4&transport=websocket",
}

# FRAGILE: the server checks the Origin header against the web terminal's domain.
DEFAULT_ORIGIN = "https://pocketoption.com"


@dataclass
class ProtocolEvent:
    """A decoded, protocol-independent event handed to the feed layer."""

    kind: str  # "open" | "connected" | "ping" | "auth_ok" | "auth_failed" | "ticks" | "assets" | "history"
    data: Any = None
    raw_name: str = ""


@dataclass
class HistoryData:
    asset: str
    period: int | None
    ticks: list[Tick] = field(default_factory=list)


# --------------------------------------------------------------------------- encode


def encode_event(name: str, payload: Any) -> str:
    """Socket.IO EVENT packet on the default namespace: ``42[name, payload]``."""
    return "42" + json.dumps([name, payload], separators=(",", ":"))


def encode_subscribe(
    asset: str, period: int, templates: list[list[Any]] | None = None
) -> list[str]:
    """Frames that ask the server to stream ``asset``.

    FRAGILE: the web terminal sends ``changeSymbol`` when a chart switches asset.
    Whether one connection may stream several assets concurrently is decided
    server-side; see README "Multi-pair streaming". ``templates`` lets config
    override the frames without code changes: each template is ``[event, payload]``
    where strings ``"{asset}"`` / ``"{period}"`` are substituted.
    """
    if templates is None:
        templates = [["changeSymbol", {"asset": "{asset}", "period": "{period}"}]]
    return [encode_event(t[0], _substitute(t[1], asset, period)) for t in templates]


def normalize_auth_frame(raw: str) -> str:
    """Accept either the full ``42["auth",{...}]`` frame or just its JSON object."""
    raw = raw.strip()
    if raw.startswith("42["):
        return raw
    if raw.startswith("{"):
        return encode_event("auth", json.loads(raw))
    raise ValueError(
        "PO_SSID must be the full auth frame copied from DevTools, e.g. "
        '42["auth",{"session":"...","isDemo":1,"uid":123,"platform":2}]'
    )


def _substitute(obj: Any, asset: str, period: int) -> Any:
    if isinstance(obj, str):
        if obj == "{period}":
            return period
        return obj.replace("{asset}", asset)
    if isinstance(obj, list):
        return [_substitute(o, asset, period) for o in obj]
    if isinstance(obj, dict):
        return {k: _substitute(v, asset, period) for k, v in obj.items()}
    return obj


# --------------------------------------------------------------------------- decode


class ProtocolDecoder:
    """Stateful decoder: remembers binary placeholders so the next binary frame
    can be matched to its event name."""

    def __init__(self) -> None:
        self._pending_binary: list[str] = []

    def decode(self, frame: str | bytes) -> list[ProtocolEvent]:
        try:
            if isinstance(frame, (bytes, bytearray)):
                return self._decode_binary(bytes(frame))
            return self._decode_text(frame)
        except Exception as exc:  # never let one odd frame kill the feed
            logger.debug(f"undecodable frame ({exc}): {str(frame)[:120]!r}")
            return []

    # -- text frames ------------------------------------------------------------

    def _decode_text(self, text: str) -> list[ProtocolEvent]:
        if text == "2":
            return [ProtocolEvent("ping")]
        if text.startswith("0{"):
            return [ProtocolEvent("open", json.loads(text[1:]))]
        if text.startswith("40"):
            return [ProtocolEvent("connected")]
        if text.startswith("45") and "-" in text[:6]:
            # Binary event header: 451-["name",{"_placeholder":true,"num":0}]
            body = json.loads(text.split("-", 1)[1])
            name = body[0] if body else ""
            self._pending_binary.append(name)
            if name.lower() == "successauth":
                return [ProtocolEvent("auth_ok", raw_name=name)]
            return []
        if text.startswith("42"):
            body = json.loads(text[2:])
            if not body:
                return []
            name, payload = body[0], (body[1] if len(body) > 1 else None)
            return self._dispatch(name, payload)
        return []

    # -- binary frames ----------------------------------------------------------

    def _decode_binary(self, data: bytes) -> list[ProtocolEvent]:
        if data[:1] == b"\x04":  # EIO3-style binary marker; harmless to strip
            data = data[1:]
        payload = json.loads(data.decode("utf-8"))
        name = self._pending_binary.pop(0) if self._pending_binary else _guess_event(payload)
        return self._dispatch(name, payload)

    # -- event routing ----------------------------------------------------------

    def _dispatch(self, name: str, payload: Any) -> list[ProtocolEvent]:
        lname = name.lower()
        if lname == "successauth":
            return [ProtocolEvent("auth_ok", payload, name)]
        if lname in ("notauthorized", "unauthorized", "autherror"):
            return [ProtocolEvent("auth_failed", payload, name)]
        if lname == "updatestream":
            ticks = parse_ticks(payload)
            return [ProtocolEvent("ticks", ticks, name)] if ticks else []
        if lname == "updateassets":
            return [ProtocolEvent("assets", parse_assets(payload), name)]
        if lname in ("updatehistorynewfast", "updatehistorynew", "loadhistoryperiod", "history"):
            hist = parse_history(payload)
            return [ProtocolEvent("history", hist, name)] if hist else []
        return []


def _guess_event(payload: Any) -> str:
    """Classify an un-announced binary payload by its shape."""
    if isinstance(payload, list) and payload and isinstance(payload[0], list):
        first = payload[0]
        if len(first) == 3 and isinstance(first[0], str):
            return "updateStream"
        if len(first) > 5 and isinstance(first[1], str):
            return "updateAssets"
    if isinstance(payload, dict) and ("history" in payload or "candles" in payload):
        return "updateHistoryNewFast"
    return "unknown"


# FRAGILE: payload shapes below.


def parse_ticks(payload: Any) -> list[Tick]:
    """``[[asset, unix_ts, price], ...]`` -> ticks. A single flat triple is accepted too."""
    if not isinstance(payload, list):
        return []
    rows = payload if payload and isinstance(payload[0], list) else [payload]
    ticks: list[Tick] = []
    for row in rows:
        if len(row) >= 3 and isinstance(row[0], str):
            try:
                ticks.append(Tick(row[0], float(row[1]), float(row[2])))
            except (TypeError, ValueError):
                continue
    return ticks


def parse_assets(payload: Any) -> list[AssetInfo]:
    """``updateAssets`` rows are positional arrays. Observed layout (unofficial):
    ``[id, symbol, name, type, group, payout, ..., is_otc?, ..., is_active?, ...]``.
    Only ``symbol`` (index 1) is relied on; the rest is best-effort."""
    assets: list[AssetInfo] = []
    if not isinstance(payload, list):
        return assets
    for row in payload:
        if not isinstance(row, list) or len(row) < 2 or not isinstance(row[1], str):
            continue
        symbol = row[1]
        name = row[2] if len(row) > 2 and isinstance(row[2], str) else ""
        payout = row[5] if len(row) > 5 and isinstance(row[5], int) and 0 < row[5] <= 100 else None
        assets.append(
            AssetInfo(
                symbol=symbol,
                name=name,
                payout=payout,
                is_otc=symbol.lower().endswith("_otc"),
                is_active=_guess_active(row),
            )
        )
    return assets


def _guess_active(row: list[Any]) -> bool:
    # FRAGILE: the "is active" flag's index has moved between releases (14 was
    # observed most often). Treat unknown as active; the tick-rate monitor will
    # reveal assets that never stream.
    if len(row) > 14 and isinstance(row[14], bool):
        return row[14]
    return True


def parse_history(payload: Any) -> HistoryData | None:
    """``{"asset":..., "period":60, "history":[[ts, price], ...], "candles":[[ts,o,c,h,l],...]}``.

    Raw tick history is preferred; candles are only used (as close-price ticks)
    when no tick history is present.
    """
    if not isinstance(payload, dict) or "asset" not in payload:
        return None
    asset = payload["asset"]
    period = payload.get("period")
    ticks: list[Tick] = []
    for row in payload.get("history") or payload.get("data") or []:
        if isinstance(row, list) and len(row) >= 2:
            ticks.append(Tick(asset, float(row[0]), float(row[1])))
        elif isinstance(row, dict) and "time" in row and ("price" in row or "close" in row):
            ticks.append(Tick(asset, float(row["time"]), float(row.get("price", row.get("close")))))
    if not ticks:
        for row in payload.get("candles") or []:
            if isinstance(row, list) and len(row) >= 3:
                ticks.append(Tick(asset, float(row[0]), float(row[2])))
    ticks.sort(key=lambda t: t.timestamp)
    return HistoryData(asset, int(period) if period else None, ticks)
