"""tests/test_signal.py - signal engine unit tests"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pandas as pd
import pytest
from indicators.engine import compute
from strategy.signal   import evaluate, SignalType


def make_df(n=300):
    np.random.seed(42)
    close  = 50000 + np.cumsum(np.random.randn(n) * 100)
    high   = close + np.abs(np.random.randn(n) * 50)
    low    = close - np.abs(np.random.randn(n) * 50)
    open_  = close - np.random.randn(n) * 30
    volume = np.abs(np.random.randn(n) * 1000) + 500
    return pd.DataFrame({"timestamp": range(n), "open": open_,
                         "high": high, "low": low, "close": close, "volume": volume})


def test_no_signal_when_in_position():
    snap = compute(make_df())
    sig  = evaluate(snap, has_position=True)
    assert sig.signal_type == SignalType.NONE

def test_signal_returns_valid_type():
    snap = compute(make_df())
    sig  = evaluate(snap, has_position=False)
    assert sig.signal_type in SignalType

def test_signal_long_is_long_flag():
    snap = compute(make_df())
    sig  = evaluate(snap, has_position=False)
    if sig.signal_type in (SignalType.TREND_LONG, SignalType.RANGE_LONG):
        assert sig.is_long is True
    elif sig.signal_type in (SignalType.TREND_SHORT, SignalType.RANGE_SHORT):
        assert sig.is_long is False
