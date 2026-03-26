"""
qc_validate.py — Shiva Sniper Bot v6.5 Full QC Suite
Run this before going live to confirm all fixes are in place.

Usage:
    python qc_validate.py

Expected output: 27/27 checks PASS
"""

import sys, os, importlib, ast, inspect, textwrap
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

PASS = "✅ PASS"
FAIL = "❌ FAIL"
WARN = "⚠️  WARN"

results = []

def check(name, condition, note=""):
    status = PASS if condition else FAIL
    results.append((name, status, note))
    icon = "✅" if condition else "❌"
    print(f"  {icon} {name}" + (f"  [{note}]" if note else ""))
    return condition


# ══════════════════════════════════════════════════════════════
# SECTION 1 — Package Import Checks (Bug #1 and #2)
# ══════════════════════════════════════════════════════════════
print("\n── SECTION 1: Package Existence & Imports ─────────────────")

check("strategy/ package exists",
      (ROOT / "strategy" / "__init__.py").exists(),
      "Bug #1 fix")

check("risk/ package exists",
      (ROOT / "risk" / "__init__.py").exists(),
      "Bug #2 fix")

check("strategy/signal.py exists",
      (ROOT / "strategy" / "signal.py").exists())

check("risk/calculator.py exists",
      (ROOT / "risk" / "calculator.py").exists())

try:
    from strategy.signal import evaluate, SignalType, Signal
    check("strategy.signal imports cleanly", True)
except Exception as e:
    check("strategy.signal imports cleanly", False, str(e))

try:
    from risk.calculator import (
        calc_levels, TrailState, calc_trail_stage,
        get_trail_points, get_trail_params,
        should_trigger_be, max_sl_hit, calc_real_pl, RiskLevels,
    )
    check("risk.calculator imports cleanly", True)
except Exception as e:
    check("risk.calculator imports cleanly", False, str(e))

try:
    from indicators.engine import compute, compute_full_series, IndicatorSnapshot
    check("indicators.engine imports cleanly", True)
except Exception as e:
    check("indicators.engine imports cleanly", False, str(e))

try:
    from config import (
        TRAIL_STAGES, TREND_RR, RANGE_RR, TREND_ATR_MULT, RANGE_ATR_MULT,
        MAX_SL_MULT, MAX_SL_POINTS, BE_MULT, ALERT_QTY, COMMISSION_PCT,
        RSI_OB, RSI_OS, ADX_TREND_TH, ADX_RANGE_TH,
        FILTER_ATR_MULT, FILTER_BODY_MULT,
    )
    check("config imports cleanly", True)
except Exception as e:
    check("config imports cleanly", False, str(e))


# ══════════════════════════════════════════════════════════════
# SECTION 2 — Signal Logic Parity (Bug #1)
# ══════════════════════════════════════════════════════════════
print("\n── SECTION 2: Signal Logic — Pine Parity ──────────────────")

from strategy.signal import evaluate, SignalType, Signal
from indicators.engine import IndicatorSnapshot
from config import ADX_TREND_TH, ADX_RANGE_TH, RSI_OB, RSI_OS

def _snap(**kw) -> IndicatorSnapshot:
    """Build a minimal IndicatorSnapshot for logic testing."""
    defaults = dict(
        ema_trend=49000, ema_fast=50000, atr=300, rsi=50,
        dip=25, dim=15, adx=ADX_TREND_TH + 5, adx_raw=24,
        vol_sma=100, atr_sma=350, trend_regime=True, range_regime=False,
        filters_ok=True, atr_ok=True, vol_ok=True, body_ok=True,
        open=50000, high=50400, low=49800, close=50200,
        volume=120, prev_high=50100, prev_low=49900, timestamp=1000000,
    )
    defaults.update(kw)
    return IndicatorSnapshot(**defaults)


# Trend Long: emaFast > emaTrend, dip > dim, close > prev_high
sig = evaluate(_snap(), has_position=False)
check("Trend Long fires correctly",
      sig.signal_type == SignalType.TREND_LONG and sig.is_long)

# No signal when has_position=True (noPosition check)
sig = evaluate(_snap(), has_position=True)
check("No signal when in position (noPosition guard)",
      sig.signal_type == SignalType.NONE)

# Trend Short: emaFast < emaTrend, dim > dip, close < prev_low
sig = evaluate(_snap(
    ema_fast=48000, ema_trend=50000, dim=25, dip=15,
    close=49800, prev_low=49900,
), has_position=False)
check("Trend Short fires correctly",
      sig.signal_type == SignalType.TREND_SHORT and not sig.is_long)

# Range Long: rangeRegime, rsi < rsiOS
sig = evaluate(_snap(
    trend_regime=False, range_regime=True,
    adx=ADX_RANGE_TH - 2, rsi=RSI_OS - 5,
), has_position=False)
check("Range Long fires correctly",
      sig.signal_type == SignalType.RANGE_LONG and sig.is_long)

# Range Short: rangeRegime, rsi > rsiOB
sig = evaluate(_snap(
    trend_regime=False, range_regime=True,
    adx=ADX_RANGE_TH - 2, rsi=RSI_OB + 5,
), has_position=False)
check("Range Short fires correctly",
      sig.signal_type == SignalType.RANGE_SHORT and not sig.is_long)

# filters_ok=False → no signal
sig = evaluate(_snap(filters_ok=False), has_position=False)
check("No signal when filters_ok=False",
      sig.signal_type == SignalType.NONE)

# close <= prev_high → no Trend Long (Pine: close > high[1])
sig = evaluate(_snap(close=50100, prev_high=50200), has_position=False)
check("Trend Long blocked when close <= prev_high",
      sig.signal_type == SignalType.NONE)


# ══════════════════════════════════════════════════════════════
# SECTION 3 — Risk Calculator Parity (Bug #2)
# ══════════════════════════════════════════════════════════════
print("\n── SECTION 3: Risk Calculator — Pine Parity ───────────────")

from risk.calculator import (
    calc_levels, calc_trail_stage, get_trail_points,
    should_trigger_be, max_sl_hit, calc_real_pl,
)
from config import TREND_ATR_MULT, TREND_RR, MAX_SL_POINTS, BE_MULT, TRAIL_STAGES

entry = 50000.0
atr   = 500.0

risk_long = calc_levels(entry, atr, is_long=True, is_trend=True)
# Pine: stopDist = min(500*0.6, 500) = 300; SL=49700; TP=50000+300*4=51200
check("Long SL = entry - min(atr*0.6, 500)",
      abs(risk_long.sl - (entry - min(atr * TREND_ATR_MULT, MAX_SL_POINTS))) < 0.01)
check("Long TP = entry + stopDist * 4.0",
      abs(risk_long.tp - (entry + risk_long.stop_dist * TREND_RR)) < 0.01)

risk_short = calc_levels(entry, atr, is_long=False, is_trend=True)
check("Short SL = entry + stopDist",
      abs(risk_short.sl - (entry + risk_short.stop_dist)) < 0.01)
check("Short TP = entry - stopDist * 4.0",
      abs(risk_short.tp - (entry - risk_short.stop_dist * TREND_RR)) < 0.01)

# Max SL cap at MAX_SL_POINTS
risk_capped = calc_levels(entry, 5000.0, is_long=True, is_trend=True)
check("stopDist capped at MAX_SL_POINTS=500",
      risk_capped.stop_dist == MAX_SL_POINTS)

# Trail stage thresholds
atr_t = 300.0
t1_trig = TRAIL_STAGES[0][0]  # 0.8
check("Trail stage 0 when profit=0",
      calc_trail_stage(0, atr_t) == 0)
check("Trail stage 1 when profit >= atr*0.8",
      calc_trail_stage(atr_t * t1_trig, atr_t) == 1)
check("Trail stage 5 when profit >= atr*6.0",
      calc_trail_stage(atr_t * 6.0, atr_t) == 5)

# Breakeven threshold
check("BE triggers when profit_dist > atr * 0.6",
      should_trigger_be(atr_t * BE_MULT + 1, atr_t))
check("BE does NOT trigger when profit_dist <= atr * 0.6",
      not should_trigger_be(atr_t * BE_MULT - 1, atr_t))

# Max SL guard
check("max_sl_hit long: low <= entry - min(atr*1.5, 500)",
      max_sl_hit(entry - 800, entry, atr_t, is_long=True))
check("max_sl_hit long: NOT hit when within range",
      not max_sl_hit(entry - 100, entry, atr_t, is_long=True))

# P/L calculation
pl = calc_real_pl(50000, 50500, qty=30, is_long=True)
expected_raw = 500 * 30
expected_comm = (50000 + 50500) * 30 * 0.05 / 100 * 2
check("calc_real_pl matches Pine formula",
      abs(pl - (expected_raw - expected_comm)) < 0.01)


# ══════════════════════════════════════════════════════════════
# SECTION 4 — Trail Formula Check (Bug #3)
# ══════════════════════════════════════════════════════════════
print("\n── SECTION 4: Trail Formula — peak_price anchor ───────────")

src = (ROOT / "phase2" / "paper_engine.py").read_text()
check("paper_engine uses peak_price in trail calc (Bug #3)",
      "peak_price - pts" in src or "peak_price + pts" in src)
check("paper_engine does NOT use 'close - pts - off'",
      "close - pts - off" not in src)
check("paper_engine tracks peak_price in EngineState",
      "peak_price" in src)
check("paper_engine initialises peak_price on entry",
      "state.peak_price  = close" in src or "state.peak_price = close" in src)


# ══════════════════════════════════════════════════════════════
# SECTION 5 — ws_feed iloc fix (Bug #7)
# ══════════════════════════════════════════════════════════════
print("\n── SECTION 5: ws_feed candle update safety (Bug #7) ───────")

ws_src = (ROOT / "feed" / "ws_feed.py").read_text()
check("ws_feed uses .loc instead of iloc dict assignment",
      "self._df.loc[last_idx" in ws_src)
check("ws_feed no longer uses iloc dict-style update",
      'self._df.iloc[-1] = {' not in ws_src)


# ══════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ══════════════════════════════════════════════════════════════
print("\n" + "═" * 60)
passed = sum(1 for _, s, _ in results if s == PASS)
failed = sum(1 for _, s, _ in results if s == FAIL)
total  = len(results)

print(f"  RESULT: {passed}/{total} checks PASSED  |  {failed} FAILED")
print("═" * 60)

if failed == 0:
    print("\n  🟢 ALL QC CHECKS PASS — Bot is ready for paper / live trading.")
    print("  Next step: run python phase2/run_phase2.py to validate signals\n")
else:
    print(f"\n  🔴 {failed} CHECK(S) FAILED — Review issues above before trading.\n")
    sys.exit(1)
