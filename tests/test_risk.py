"""tests/test_risk.py - risk calculator unit tests"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pytest
from risk.calculator import (
    calc_levels, calc_trail_stage, get_trail_params,
    should_trigger_be, max_sl_hit, calc_real_pl,
)


def test_long_sl_below_entry():
    r = calc_levels(50000, 500, True, True)
    assert r.sl < r.entry_price

def test_long_tp_above_entry():
    r = calc_levels(50000, 500, True, True)
    assert r.tp > r.entry_price

def test_short_sl_above_entry():
    r = calc_levels(50000, 500, False, True)
    assert r.sl > r.entry_price

def test_short_tp_below_entry():
    r = calc_levels(50000, 500, False, True)
    assert r.tp < r.entry_price

def test_stop_dist_capped():
    r = calc_levels(50000, 2000, True, True)
    assert r.stop_dist <= 500.0

def test_trend_rr_4():
    r = calc_levels(50000, 100, True, True)
    assert abs(r.tp - (r.entry_price + r.stop_dist * 4.0)) < 0.01

def test_range_rr_2_5():
    r = calc_levels(50000, 100, True, False)
    assert abs(r.tp - (r.entry_price + r.stop_dist * 2.5)) < 0.01

def test_trail_stage_0_no_profit():
    assert calc_trail_stage(0, 500) == 0

def test_trail_stage_1():
    assert calc_trail_stage(0.8 * 500, 500) == 1

def test_trail_stage_5():
    assert calc_trail_stage(6.0 * 500, 500) == 5

def test_be_trigger():
    assert should_trigger_be(0.6 * 500 + 1, 500) is True
    assert should_trigger_be(0.6 * 500 - 1, 500) is False

def test_max_sl_long():
    assert max_sl_hit(49200, 50000, 500, True) is True
    assert max_sl_hit(49600, 50000, 500, True) is False

def test_max_sl_short():
    assert max_sl_hit(50800, 50000, 500, False) is True
    assert max_sl_hit(50400, 50000, 500, False) is False

def test_calc_real_pl_long_profit():
    pl = calc_real_pl(50000, 50500, 30, True)
    assert pl > 0

def test_calc_real_pl_long_loss():
    pl = calc_real_pl(50000, 49500, 30, True)
    assert pl < 0

def test_calc_real_pl_short_profit():
    pl = calc_real_pl(50000, 49500, 30, False)
    assert pl > 0
