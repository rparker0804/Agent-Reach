from otc_scanner.candles import CandleAggregator
from otc_scanner.models import Tick


def T(ts, p, a="EURUSD_otc"):
    return Tick(a, ts, p)


def test_ohlc_and_close_on_boundary():
    agg = CandleAggregator(60)
    assert agg.add(T(0, 1.0)) is None
    agg.add(T(10, 1.5))
    agg.add(T(20, 0.5))
    agg.add(T(59.9, 1.2))
    closed = agg.add(T(60, 1.3))
    assert (closed.start, closed.open, closed.high, closed.low, closed.close, closed.ticks) == (
        0,
        1.0,
        1.5,
        0.5,
        1.2,
        4,
    )
    assert len(agg.candles("EURUSD_otc")) == 1
    assert len(agg.candles("EURUSD_otc", include_forming=True)) == 2


def test_late_ticks_dropped_and_assets_independent():
    agg = CandleAggregator(60)
    agg.add(T(100, 1.0))
    agg.add(T(130, 2.0, "GBPUSD_otc"))
    assert agg.add(T(30, 9.9)) is None  # older bar: ignored
    assert agg.candles("EURUSD_otc", True)[0].high == 1.0
    assert agg.assets == ["EURUSD_otc", "GBPUSD_otc"]


def test_window_is_bounded():
    agg = CandleAggregator(1, max_candles=5)
    for i in range(20):
        agg.add(T(i, float(i)))
    assert [c.start for c in agg.candles("EURUSD_otc")] == [14, 15, 16, 17, 18]


def test_seed_backfills_before_live_data():
    agg = CandleAggregator(60)
    agg.add(T(600, 2.0))
    agg.add(T(660, 2.1))  # closes bar 600
    n = agg.seed("EURUSD_otc", [T(t, 1.0 + t / 1000) for t in range(0, 700, 15)])
    starts = [c.start for c in agg.candles("EURUSD_otc")]
    assert n == 10  # bars 0..540; history overlapping live data is ignored
    assert starts == list(range(0, 660, 60))
    assert agg.candles("EURUSD_otc")[-1].open == 2.0  # live bar untouched


def test_seed_with_no_live_data_keeps_forming_bar():
    agg = CandleAggregator(60)
    agg.seed("EURUSD_otc", [T(0, 1.0), T(61, 1.1), T(130, 1.2)])
    assert [c.start for c in agg.candles("EURUSD_otc")] == [0, 60]
    assert agg.add(T(150, 1.3)) is None  # continues the seeded forming bar 120
    assert agg.add(T(180, 1.4)).close == 1.3


def test_frame_columns():
    agg = CandleAggregator(60)
    for t in (0, 60, 120):
        agg.add(T(t, 1.0))
    df = agg.frame("EURUSD_otc")
    assert list(df.columns) == ["open", "high", "low", "close", "ticks"]
    assert list(df.index) == [0, 60]
