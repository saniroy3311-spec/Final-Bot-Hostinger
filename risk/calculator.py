"""
risk/calculator.py
Replicates ALL risk management logic from Shiva Sniper v6.5 Pine Script.

Mirrors these Pine Script sections verbatim:
  - RISK LEVELS       (stopDist, SL, TP)
  - TRAIL ENGINE      (5-stage ratchet anchored to peak price)
  - BREAKEVEN         (beMult threshold)
  - MAX STOP LOSS     (maxSLMult + maxSLPoints cap)

BUG FIX (CRITICAL): This module was MISSING from the zip.
main.py, trail_loop.py, paper_engine.py, and all phase runners import from
risk.calculator but the risk/ package did not exist — bot would crash on
startup with ImportError before any trade logic could execute.

Pine Script reference (section RISK LEVELS):
───────────────────────────────────────────────────────────────────────────
  atrMult  = isTrendTrade ? trendATRMult : rangeATRMult   (0.6 or 0.5)
  rrRatio  = isTrendTrade ? trendRR      : rangeRR         (4.0 or 2.5)
  stopDist = math.min(atr * atrMult, maxSLPoints)          (cap at 500 pts)

  longSL   = entryPx - stopDist
  longTP   = entryPx + stopDist * rrRatio
  shortSL  = entryPx + stopDist
  shortTP  = entryPx - stopDist * rrRatio
───────────────────────────────────────────────────────────────────────────

Pine Script Trail Engine (5-stage):
  Triggers when profitDist >= atr * tXTrig
  activePts = atr * tXPts    <- distance from PEAK price to SL trigger
  activeOff = atr * tXOff    <- used only as bracket limit buffer, NOT in SL calc

Pine Script Breakeven:
  if profitDist > atr * beMult:
      stop = entryPx  (SL moves to entry, trail continues)

Pine Script Max SL:
  maxSLDist = math.min(atr * maxSLMult, maxSLPoints)
  if long  and low  <= entryPx - maxSLDist: close_all()
  if short and high >= entryPx + maxSLDist: close_all()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config import (
    TRAIL_STAGES,
    TREND_RR, RANGE_RR,
    TREND_ATR_MULT, RANGE_ATR_MULT,
    MAX_SL_MULT, MAX_SL_POINTS,
    BE_MULT,
    ALERT_QTY,
    COMMISSION_PCT,
)


# ═══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RiskLevels:
    """
    Computed entry risk parameters.
    Created once per trade and held until position is closed.
    """
    entry_price: float
    sl:          float
    tp:          float
    stop_dist:   float    # raw stopDist (before any trail move)
    atr:         float    # ATR at entry bar — used by trail_loop for all calcs
    is_long:     bool
    is_trend:    bool     # True = trend trade, False = range trade


@dataclass
class TrailState:
    """
    Mutable per-trade trail state.
    Tracks trail stage, current SL, peak price, and flag states.
    """
    stage:        int   = 0
    current_sl:   float = 0.0
    peak_price:   float = 0.0     # Highest high (long) / Lowest low (short) since entry
    be_done:      bool  = False
    max_sl_fired: bool  = False


# ═══════════════════════════════════════════════════════════════════════════
# RISK LEVEL CALCULATION
# ═══════════════════════════════════════════════════════════════════════════

def calc_levels(
    entry_price: float,
    atr:         float,
    is_long:     bool,
    is_trend:    bool,
) -> RiskLevels:
    """
    Compute SL and TP from entry price and ATR.

    Pine Script (RISK LEVELS section):
        atrMult  = trendATRMult if isTrendTrade else rangeATRMult
        rrRatio  = trendRR      if isTrendTrade else rangeRR
        stopDist = math.min(atr * atrMult, maxSLPoints)
        longSL   = entryPx - stopDist
        longTP   = entryPx + stopDist * rrRatio
    """
    atr_mult  = TREND_ATR_MULT if is_trend else RANGE_ATR_MULT
    rr_ratio  = TREND_RR       if is_trend else RANGE_RR

    stop_dist = min(atr * atr_mult, MAX_SL_POINTS)

    if is_long:
        sl = entry_price - stop_dist
        tp = entry_price + stop_dist * rr_ratio
    else:
        sl = entry_price + stop_dist
        tp = entry_price - stop_dist * rr_ratio

    return RiskLevels(
        entry_price = entry_price,
        sl          = sl,
        tp          = tp,
        stop_dist   = stop_dist,
        atr         = atr,
        is_long     = is_long,
        is_trend    = is_trend,
    )


# ═══════════════════════════════════════════════════════════════════════════
# TRAIL ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def calc_trail_stage(profit_dist: float, atr: float) -> int:
    """
    Determine the current trail stage (0–5) from profit distance.

    Pine Script (TRAIL ENGINE section):
        if trailStage < 5 and profitDist >= atr * t5Trig: trailStage := 5
        else if ... t4Trig: trailStage := 4
        ...
        else if ... t1Trig: trailStage := 1

    Stage is monotonically increasing — never decreases.
    Caller is responsible for ensuring new_stage >= current_stage.
    """
    for stage_idx in range(len(TRAIL_STAGES) - 1, -1, -1):
        trig, _, _ = TRAIL_STAGES[stage_idx]
        if profit_dist >= atr * trig:
            return stage_idx + 1   # stages are 1-indexed (1..5)
    return 0


def get_trail_points(stage: int, atr: float) -> float:
    """
    Return trail_points (distance from PEAK price to SL trigger) for a given stage.

    Pine Script:
        activePts = atr * t{stage}Pts

    NOTE: trail_offset (activeOff) is NOT part of the SL trigger price.
    It is the activation gap / limit-order buffer and belongs only in
    the bracket_stop_loss_limit_price (BRACKET_SL_BUFFER in orders/manager.py).
    """
    if stage < 1 or stage > len(TRAIL_STAGES):
        return 0.0
    _, pts, _ = TRAIL_STAGES[stage - 1]
    return atr * pts


def get_trail_params(stage: int, atr: float) -> tuple[float, float]:
    """
    Return (trail_points, trail_offset) for a given stage.
    Used by paper_engine.py for full parameter access.

    trail_points = distance from peak to SL trigger  (primary SL calc)
    trail_offset = limit-order buffer below SL trigger (secondary, bracket only)
    """
    if stage < 1 or stage > len(TRAIL_STAGES):
        return 0.0, 0.0
    _, pts, off = TRAIL_STAGES[stage - 1]
    return atr * pts, atr * off


# ═══════════════════════════════════════════════════════════════════════════
# BREAKEVEN
# ═══════════════════════════════════════════════════════════════════════════

def should_trigger_be(profit_dist: float, atr: float) -> bool:
    """
    Pine Script (BREAKEVEN section):
        if not beDone and not na(entryPx)
            if strategy.position_size > 0 and profitDist > atr * beMult
                strategy.exit("BE-L", stop=entryPx, ...)
                beDone := true

    Returns True when breakeven threshold is crossed.
    Caller must guard with state.be_done to trigger only once.
    """
    return profit_dist > atr * BE_MULT


# ═══════════════════════════════════════════════════════════════════════════
# MAX STOP LOSS
# ═══════════════════════════════════════════════════════════════════════════

def max_sl_hit(
    current_price: float,
    entry_price:   float,
    atr:           float,
    is_long:       bool,
) -> bool:
    """
    Pine Script (MAX STOP LOSS section):
        maxSLDist = math.min(atr * maxSLMult, maxSLPoints)
        if strategy.position_size > 0 and low <= entryPx - maxSLDist
            strategy.close_all(comment="Max SL")
        if strategy.position_size < 0 and high >= entryPx + maxSLDist
            strategy.close_all(comment="Max SL")

    current_price should be low (long) or high (short) to catch wicks.
    trail_loop.py uses mark price as the closest real-time equivalent.
    paper_engine.py uses bar low/high for true intra-bar wick detection.
    """
    max_sl_dist = min(atr * MAX_SL_MULT, MAX_SL_POINTS)
    if is_long:
        return current_price <= entry_price - max_sl_dist
    else:
        return current_price >= entry_price + max_sl_dist


# ═══════════════════════════════════════════════════════════════════════════
# P/L CALCULATION
# ═══════════════════════════════════════════════════════════════════════════

def calc_real_pl(
    entry_px: float,
    exit_px:  float,
    qty:      int,
    is_long:  bool,
) -> float:
    """
    Mirrors Pine Script P/L label calculation:
        rawPL  = (exitPx - entryPx) * qty      if long
               = (entryPx - exitPx) * qty      if short
        comm   = (entryPx + exitPx) * qty * 0.001   (0.05% each leg)
        realPL = rawPL - comm
    """
    if is_long:
        raw_pl = (exit_px - entry_px) * qty
    else:
        raw_pl = (entry_px - exit_px) * qty

    commission = (entry_px + exit_px) * qty * COMMISSION_PCT * 2   # entry + exit
    return raw_pl - commission
