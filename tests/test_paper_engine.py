"""tests/test_paper_engine.py - paper engine integration test"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import numpy as np
import pandas as pd
import pytest
from phase2.paper_engine import run as paper_run, trades_to_df


def make_df(n=400):
    np.random.seed(7)
    close  = 50000 + np.cumsum(np.random.randn(n) * 150)
    high   = close + np.abs(np.random.randn(n) * 80)
    low    = close - np.abs(np.random.randn(n) * 80)
    open_  = close - np.random.randn(n) * 40
    volume = np.abs(np.random.randn(n) * 800) + 400
    return pd.DataFrame({"timestamp": [1700000000000 + i*3600000 for i in range(n)],
                         "open": open_, "high": high,
                         "low": low, "close": close, "volume": volume})


def test_paper_run_returns_list():
    trades = paper_run(make_df())
    assert isinstance(trades, list)

def test_no_same_bar_exit():
    trades = paper_run(make_df())
    for t in trades:
        assert t.exit_bar > t.entry_bar, \
            f"Trade {t.trade_id}: exit bar {t.exit_bar} <= entry bar {t.entry_bar}"

def test_sl_tp_valid():
    trades = paper_run(make_df())
    for t in trades:
        if t.is_long:
            assert t.sl < t.entry_price
            assert t.tp > t.entry_price
        else:
            assert t.sl > t.entry_price
            assert t.tp < t.entry_price

def test_exit_reasons_valid():
    trades  = paper_run(make_df())
    valid   = {"TP", "SL", "Trail/BE SL", "Max SL"}
    for t in trades:
        assert t.exit_reason in valid, f"Unknown exit: {t.exit_reason}"

def test_trades_to_df():
    trades = paper_run(make_df())
    df     = trades_to_df(trades)
    if not df.empty:
        assert "entry_price" in df.columns
        assert "real_pl"     in df.columns
        assert "exit_reason" in df.columns
