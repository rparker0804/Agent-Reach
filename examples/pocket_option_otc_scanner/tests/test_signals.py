import pandas as pd
import pytest
from otc_scanner.signals import SignalEngine


def frame(**cols):
    n = len(next(iter(cols.values())))
    data = {"close": [1.0] * n, **cols}
    return pd.DataFrame(data, index=[i * 60 for i in range(n)])


def run(engine, df):
    """Feed the frame bar by bar, as the scanner does on each candle close."""
    out = []
    for i in range(2, len(df) + 1):
        out += engine.evaluate("EURUSD_otc", df.iloc[:i], 60)
    return out


def test_threshold_fires_on_entry_only():
    eng = SignalEngine.from_config(
        [{"id": "r", "type": "threshold", "column": "rsi", "below": 30, "above": 70}],
        cooldown_bars=0,
    )
    sigs = run(eng, frame(rsi=[50, 25, 20, 40, 75, 80]))
    assert [(s.direction, s.timestamp) for s in sigs] == [("CALL", 120), ("PUT", 300)]
    assert "oversold" in sigs[0].condition and sigs[0].values == {"rsi": 25}


def test_while_mode_and_cooldown():
    eng = SignalEngine.from_config(
        [{"id": "r", "type": "threshold", "column": "rsi", "below": 30, "mode": "while"}],
        cooldown_bars=2,
    )
    sigs = run(eng, frame(rsi=[50, 25, 20, 20, 20, 20]))
    assert [s.timestamp for s in sigs] == [120, 240, 360]


def test_crossover_both_ways_and_numeric_level():
    eng = SignalEngine.from_config(
        [
            {"id": "x", "type": "crossover", "fast": "a", "slow": "b"},
            {"id": "zero", "type": "crossover", "fast": "h", "slow": 0, "down": None},
        ],
        {"a": "EMA(9)"},
        cooldown_bars=0,
    )
    sigs = run(eng, frame(a=[1, 2, 3, 2], b=[2, 2.5, 2.5, 2.5], h=[-1, -1, 1, 1]))
    assert [(s.rule, s.direction) for s in sigs] == [("x", "CALL"), ("zero", "CALL"), ("x", "PUT")]
    assert sigs[0].condition.startswith("EMA(9) crossed above b")


def test_no_false_cross_during_warmup():
    eng = SignalEngine.from_config(
        [{"type": "crossover", "fast": "a", "slow": "b"}], cooldown_bars=0
    )
    assert run(eng, frame(a=[None, None, 3, 3], b=[None, 1, 1, 1])) == []


def test_all_of_needs_agreement():
    eng = SignalEngine.from_config(
        [
            {
                "id": "combo",
                "type": "all_of",
                "rules": [
                    {"type": "threshold", "column": "rsi", "below": 30},
                    {
                        "type": "crossover",
                        "fast": "close",
                        "slow": "lower",
                        "up": None,
                        "down": "CALL",
                    },
                ],
            }
        ],
        cooldown_bars=0,
    )
    df = frame(rsi=[50, 25, 25, 25], close=[1.0, 1.0, 0.8, 0.7], lower=[0.9, 0.9, 0.9, 0.9])
    sigs = run(eng, df)
    assert [(s.direction, s.timestamp) for s in sigs] == [("CALL", 180)]
    assert " AND " in sigs[0].condition


def test_disabled_rules_skipped_and_validation():
    eng = SignalEngine.from_config(
        [
            {"type": "threshold", "column": "rsi", "below": 30},
            {"type": "threshold", "column": "x", "below": 1, "enabled": False},
        ]
    )
    assert len(eng.rules) == 1
    eng.validate({"rsi"})
    with pytest.raises(ValueError, match="unknown column"):
        eng.validate({"macd.hist"})


def test_bad_config_errors():
    with pytest.raises(ValueError):
        SignalEngine.from_config([{"type": "threshold", "column": "rsi"}])
    with pytest.raises(ValueError):
        SignalEngine.from_config([{"type": "crossover", "fast": "a", "slow": "b", "up": "BUY"}])
    with pytest.raises(ValueError, match="Unknown rule"):
        SignalEngine.from_config([{"type": "magic"}])
