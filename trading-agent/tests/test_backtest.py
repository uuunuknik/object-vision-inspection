import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backtest as bt  # noqa: E402


def bars(closes, opens=None, lows=None, highs=None):
    closes = np.asarray(closes, float)
    opens = closes if opens is None else np.asarray(opens, float)
    lows = np.minimum(opens, closes) if lows is None else np.asarray(lows, float)
    highs = np.maximum(opens, closes) if highs is None else np.asarray(highs, float)
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=idx)


def test_ema_matches_pine_seed():
    x = np.arange(1, 11, dtype=float)
    out = bt.ema(x, 3)
    assert np.isnan(out[1])
    assert out[2] == pytest.approx(2.0)  # SMA of 1,2,3
    assert out[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)


def test_rsi_bounds():
    up = bt.rsi(np.arange(1, 30, dtype=float), 2)
    down = bt.rsi(np.arange(30, 1, -1, dtype=float), 2)
    assert up[-1] == 100.0
    assert down[-1] == 0.0


def params(**kw):
    # short lengths so small hand-made series work
    return bt.Params(trend_len=5, exit_len=2, atr_len=2, rsi_len=2, rsi_entry=50, **kw)


# Steady uptrend 10..20, then one dip bar (index 11) that stays above the trend EMA.
UP = list(range(10, 21))


def test_buy_on_close_fill_next_open_and_target_exit():
    p = params(commission=0)
    df = bt.add_indicators(bars(UP + [18.6, 19.0, 20.5, 21], UP + [20.0, 18.5, 19.2, 20.6]), p)
    trades, _ = bt.simulate(df, p, 5)
    t = trades.iloc[0]
    assert t["entry_date"] == df.index[12]
    assert t["entry_price"] == 18.5  # next bar's open, not the signal close
    assert t["exit_reason"] == "target"
    assert t["exit_date"] == df.index[13]
    assert t["exit_price"] == 19.2
    assert t["return_pct"] == pytest.approx((19.2 / 18.5 - 1) * 100)


def test_stop_loss_fills_at_open_on_gap_down():
    p = params(atr_mult=0.5, commission=0)
    df = bt.add_indicators(bars(UP + [18.6, 18.4, 15.0, 15], UP + [20.0, 18.5, 16.0, 15]), p)
    trades, _ = bt.simulate(df, p, 5)
    t = trades.iloc[0]
    assert t["exit_reason"] == "stop_loss"
    assert t["exit_price"] == 16.0  # gapped below the stop (17.9) -> filled at the open


def test_time_exit_after_max_bars():
    p = params(max_bars=2, commission=0)
    # After the dip the price drifts down slowly: never above the exit EMA, never hits the stop.
    closes = UP + [18.6, 18.5, 18.4, 18.3, 18.2]
    df = bt.add_indicators(bars(closes, UP + [20.0, 18.5, 18.45, 18.35, 18.25]), p)
    trades, _ = bt.simulate(df, p, 5)
    t = trades.iloc[0]
    assert t["exit_reason"] == "time_exit"
    assert t["bars"] == 3  # decided at the close of bar 2, filled at the next open


def test_commission_reduces_return():
    data = bars(UP + [18.6, 19.0, 20.5, 21], UP + [20.0, 18.5, 19.2, 20.6])
    p0, p1 = params(commission=0), params(commission=0.0015)
    r0 = bt.simulate(bt.add_indicators(data, p0), p0, 5)[0]["return_pct"].iloc[0]
    r1 = bt.simulate(bt.add_indicators(data, p1), p1, 5)[0]["return_pct"].iloc[0]
    assert r1 == pytest.approx((19.2 * 0.9985 / (18.5 * 1.0015) - 1) * 100)
    assert r1 < r0


def test_random_walk_end_to_end(tmp_path):
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.015, 1500)))
    opens = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.003, 1500))
    df = bars(close, opens, np.minimum(opens, close) * 0.995, np.maximum(opens, close) * 1.005)
    r = bt.run_one("RW", df, bt.Params(), tmp_path)
    assert r["trades"] > 10
    assert (tmp_path / "trades_RW.csv").exists()
    table, best, test = bt.optimize(df, bt.Params())
    assert {"default", "best"} == set(test)
