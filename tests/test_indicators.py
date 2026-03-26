"""tests/test_indicators.py - indicator engine unit tests"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pandas as pd
import pytest
from indicators.engine import compute, compute_full_series


def make_df(n=300):
    np.random.seed(42)
    close  = 50000 + np.cumsum(np.random.randn(n) * 100)
    high   = close + np.abs(np.random.randn(n) * 50)
    low    = close - np.abs(np.random.randn(n) * 50)
    open_  = close - np.random.randn(n) * 30
    volume = np.abs(np.random.randn(n) * 1000) + 500
    return pd.DataFrame({"timestamp": range(n), "open": open_,
                         "high": high, "low": low, "close": close, "volume": volume})


def test_compute_returns_snapshot():
    snap = compute(make_df())
    assert snap.ema_trend > 0
    assert snap.atr > 0
    assert 0 < snap.rsi < 100
    assert snap.adx > 0

def test_compute_requires_min_bars():
    with pytest.raises(ValueError):
        compute(make_df(100))

def test_regime_mutually_exclusive():
    snap = compute(make_df())
    assert not (snap.trend_regime and snap.range_regime)

def test_compute_series_last_matches_compute():
    df     = make_df()
    snap   = compute(df)
    series = compute_full_series(df)
    assert abs(snap.ema_trend - series["ema200"].iloc[-1]) < 1e-6
    assert abs(snap.atr       - series["atr"].iloc[-1])    < 1e-6
    assert abs(snap.adx       - series["adx"].iloc[-1])    < 1e-6
