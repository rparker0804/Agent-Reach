import math

import numpy as np
import pandas as pd
import pytest
from otc_scanner.indicators import IndicatorSet, build_indicator


def ohlc(closes):
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c})


def wilder_rsi_reference(closes, n):
    """Textbook loop implementation to check the vectorised version against."""
    deltas = np.diff(closes)
    gains, losses = np.clip(deltas, 0, None), np.clip(-deltas, 0, None)
    ag, al = gains[:n].mean(), losses[:n].mean()
    # pandas ewm(adjust=False) seeds with the first value rather than the SMA, so
    # compare far enough out that the seed has decayed.
    for g, loss in zip(gains[n:], losses[n:]):
        ag = (ag * (n - 1) + g) / n
        al = (al * (n - 1) + loss) / n
    return 100 - 100 / (1 + ag / al)


def test_rsi_matches_wilder_reference():
    rng = np.random.default_rng(1)
    closes = 100 + rng.normal(0, 1, 600).cumsum()
    rsi = build_indicator({"type": "rsi", "period": 14}).compute(ohlc(closes))[""]
    assert rsi.iloc[-1] == pytest.approx(wilder_rsi_reference(closes, 14), abs=0.05)
    assert rsi.iloc[:13].isna().all()


def test_rsi_extremes():
    up = build_indicator({"type": "rsi"}).compute(ohlc(range(50)))[""]
    down = build_indicator({"type": "rsi"}).compute(ohlc(range(50, 0, -1)))[""]
    assert up.iloc[-1] == 100 and down.iloc[-1] == 0


def test_sma_ema():
    df = ohlc([1, 2, 3, 4, 5])
    assert build_indicator({"type": "sma", "period": 3}).compute(df)[""].tolist()[2:] == [2, 3, 4]
    e = build_indicator({"type": "ema", "period": 2}).compute(df)[""]
    assert math.isnan(e.iloc[0]) and e.iloc[-1] == pytest.approx(4.5, abs=0.02)


def test_macd_outputs_are_consistent():
    df = ohlc(100 + np.sin(np.arange(200) / 5))
    out = build_indicator({"type": "macd"}).compute(df)
    assert set(out) == {"macd", "signal", "hist"}
    pd.testing.assert_series_equal(out["hist"], out["macd"] - out["signal"], check_names=False)


def test_bollinger_bands_bracket_the_mean():
    df = ohlc(100 + np.random.default_rng(2).normal(0, 1, 100))
    bb = build_indicator({"type": "bollinger", "period": 20, "stddev": 2}).compute(df)
    tail = slice(19, None)
    assert (bb["upper"][tail] > bb["middle"][tail]).all() and (
        bb["lower"][tail] < bb["middle"][tail]
    ).all()
    assert bb["middle"].iloc[-1] == pytest.approx(df["close"].tail(20).mean())


def test_indicator_set_columns_and_min_bars():
    s = IndicatorSet.from_config(
        [
            {"id": "rsi", "type": "rsi"},
            {"id": "m", "type": "macd"},
            {"id": "st", "type": "stochastic"},
        ]
    )
    cols = set(s.compute(ohlc(range(60))).columns)
    assert {"rsi", "m.macd", "m.signal", "m.hist", "st.k", "st.d"} <= cols
    assert s.min_bars == 26 + 9 * 2
    assert s.labels()["m"] == "MACD(12,26,9)"


def test_unknown_or_duplicate_indicator_rejected():
    with pytest.raises(ValueError, match="Unknown indicator"):
        build_indicator({"type": "nope"})
    with pytest.raises(ValueError, match="Duplicate"):
        IndicatorSet.from_config([{"type": "rsi"}, {"type": "rsi"}])
