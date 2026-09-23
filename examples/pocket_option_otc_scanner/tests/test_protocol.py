import json

import pytest
from otc_scanner.feeds.protocol import (
    ProtocolDecoder,
    encode_subscribe,
    normalize_auth_frame,
    parse_assets,
    parse_history,
)


def test_engineio_control_frames():
    d = ProtocolDecoder()
    assert [e.kind for e in d.decode("2")] == ["ping"]
    assert d.decode('0{"sid":"abc","pingInterval":25000}')[0].kind == "open"
    assert d.decode('40{"sid":"xyz"}')[0].kind == "connected"


def test_binary_placeholder_then_payload():
    d = ProtocolDecoder()
    assert d.decode('451-["updateStream",{"_placeholder":true,"num":0}]') == []
    events = d.decode(json.dumps([["EURUSD_otc", 1700000000.5, 1.08123]]).encode())
    assert events[0].kind == "ticks"
    t = events[0].data[0]
    assert (t.asset, t.timestamp, t.price) == ("EURUSD_otc", 1700000000.5, 1.08123)


def test_successauth_placeholder_counts_as_auth():
    d = ProtocolDecoder()
    assert d.decode('451-["successauth",{"_placeholder":true,"num":0}]')[0].kind == "auth_ok"
    assert d.decode(b'{"id":"x"}')[0].kind == "auth_ok"  # its binary body is consumed too


def test_eio3_binary_marker_is_stripped():
    d = ProtocolDecoder()
    d.decode('451-["updateStream",{"_placeholder":true,"num":0}]')
    ev = d.decode(b"\x04" + json.dumps([["GBPUSD_otc", 1, 1.2]]).encode())
    assert ev[0].data[0].asset == "GBPUSD_otc"


def test_unannounced_binary_is_classified_by_shape():
    d = ProtocolDecoder()
    assert d.decode(json.dumps([["AUDCAD_otc", 1, 0.9]]).encode())[0].kind == "ticks"


def test_text_event_and_auth_failure():
    d = ProtocolDecoder()
    assert d.decode('42["NotAuthorized",{}]')[0].kind == "auth_failed"
    assert d.decode('42["updateStream",[["EURJPY_otc",1,160.1]]]')[0].kind == "ticks"


def test_garbage_never_raises():
    d = ProtocolDecoder()
    assert d.decode("42[not json") == []
    assert d.decode(b"\xff\xfe") == []


def test_parse_assets_positional_rows():
    rows = [
        [
            66,
            "EURUSD_otc",
            "EUR/USD OTC",
            "currency",
            1,
            92,
            60,
            30,
            3,
            0,
            170,
            0,
            [],
            1700000000,
            True,
        ],
        [1, "EURUSD", "EUR/USD", "currency", 1, 85, 60, 30, 3, 0, 170, 0, [], 1700000000, False],
        "junk",
    ]
    assets = parse_assets(rows)
    assert [a.symbol for a in assets] == ["EURUSD_otc", "EURUSD"]
    assert assets[0].is_otc and assets[0].payout == 92 and assets[0].is_active
    assert not assets[1].is_otc and not assets[1].is_active


def test_parse_history_prefers_ticks_then_candles():
    h = parse_history({"asset": "EURUSD_otc", "period": 60, "history": [[2, 1.1], [1, 1.0]]})
    assert [t.timestamp for t in h.ticks] == [1, 2]
    h2 = parse_history({"asset": "EURUSD_otc", "candles": [[60, 1.0, 1.2, 1.3, 0.9]]})
    assert h2.ticks[0].price == 1.2  # close
    assert parse_history({"no": "asset"}) is None


def test_encode_subscribe_default_and_templates():
    assert encode_subscribe("EURUSD_otc", 60) == [
        '42["changeSymbol",{"asset":"EURUSD_otc","period":60}]'
    ]
    frames = encode_subscribe(
        "X_otc",
        30,
        [["subfor", "{asset}"], ["changeSymbol", {"asset": "{asset}", "period": "{period}"}]],
    )
    assert frames == ['42["subfor","X_otc"]', '42["changeSymbol",{"asset":"X_otc","period":30}]']


def test_normalize_auth_frame():
    full = '42["auth",{"session":"s","isDemo":1,"uid":1,"platform":2}]'
    assert normalize_auth_frame(full) == full
    assert (
        normalize_auth_frame('{"session":"s","isDemo":1}')
        == '42["auth",{"session":"s","isDemo":1}]'
    )
    with pytest.raises(ValueError):
        normalize_auth_frame("just-a-cookie-value")
