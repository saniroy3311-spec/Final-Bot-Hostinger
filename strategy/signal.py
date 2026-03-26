"""
strategy/signal.py
Replicates Shiva Sniper v6.5 Pine Script entry conditions EXACTLY.

Pine Script entry logic (mirrored verbatim):
───────────────────────────────────────────────────────────────────────────
trendLong  = trendRegime and emaFast > emaTrend and dip > dim
             and close > high[1] and filtersOK and noPosition

trendShort = trendRegime and emaFast < emaTrend and dim > dip
             and close < low[1] and filtersOK and noPosition

rangeLong  = rangeRegime and rsi < rsiOS and filtersOK and noPosition
rangeShort = rangeRegime and rsi > rsiOB and filtersOK and noPosition

Key Pine Script semantics translated:
  close > high[1]  →  snap.close > snap.prev_high   (confirmed bar close vs prev bar high)
  close < low[1]   →  snap.close < snap.prev_low
  noPosition       →  has_position == False (passed from bot state)
  trendRegime      →  adx > ADX_TREND_TH  (snap.trend_regime)
  rangeRegime      →  adx < ADX_RANGE_TH  (snap.range_regime)
  filtersOK        →  atrOK and volOK and bodyOK  (snap.filters_ok)

BUG FIX (CRITICAL): This module was MISSING from the zip.
main.py, paper_engine.py, and all phase runners import from strategy.signal
but the strategy/ package did not exist — bot would crash on startup with
ImportError before executing a single line of logic.
───────────────────────────────────────────────────────────────────────────
"""

from enum import Enum
from dataclasses import dataclass
from indicators.engine import IndicatorSnapshot
from config import RSI_OB, RSI_OS


class SignalType(Enum):
    NONE        = "NONE"
    TREND_LONG  = "Trend Long"
    TREND_SHORT = "Trend Short"
    RANGE_LONG  = "Range Long"
    RANGE_SHORT = "Range Short"


@dataclass
class Signal:
    signal_type: SignalType
    is_long:     bool
    regime:      str          # "trend" | "range" | "none"

    @property
    def is_none(self) -> bool:
        return self.signal_type == SignalType.NONE


# ── Singleton no-signal object (avoids allocation per bar) ────────────────
_NO_SIGNAL = Signal(signal_type=SignalType.NONE, is_long=False, regime="none")


def evaluate(snap: IndicatorSnapshot, has_position: bool) -> Signal:
    """
    Evaluate all four entry conditions on a confirmed bar snapshot.

    Args:
        snap:         IndicatorSnapshot for the latest confirmed bar.
        has_position: True if bot already has an open position.
                      Mirrors Pine's  `noPosition = strategy.position_size == 0`.

    Returns:
        Signal object. signal_type == NONE if no entry triggered.

    Pine priority order (else-if chain):
        1. Trend Long   (highest priority)
        2. Trend Short
        3. Range Long
        4. Range Short
    """
    if has_position:
        return _NO_SIGNAL

    if not snap.filters_ok:
        return _NO_SIGNAL

    # ── TREND REGIME SIGNALS ─────────────────────────────────────────────
    if snap.trend_regime:
        # Pine: trendLong = trendRegime and emaFast > emaTrend
        #                   and dip > dim and close > high[1]
        if (snap.ema_fast > snap.ema_trend
                and snap.dip  > snap.dim
                and snap.close > snap.prev_high):
            return Signal(
                signal_type = SignalType.TREND_LONG,
                is_long     = True,
                regime      = "trend",
            )

        # Pine: trendShort = trendRegime and emaFast < emaTrend
        #                    and dim > dip and close < low[1]
        if (snap.ema_fast < snap.ema_trend
                and snap.dim  > snap.dip
                and snap.close < snap.prev_low):
            return Signal(
                signal_type = SignalType.TREND_SHORT,
                is_long     = False,
                regime      = "trend",
            )

    # ── RANGE REGIME SIGNALS ─────────────────────────────────────────────
    if snap.range_regime:
        # Pine: rangeLong = rangeRegime and rsi < rsiOS
        if snap.rsi < RSI_OS:
            return Signal(
                signal_type = SignalType.RANGE_LONG,
                is_long     = True,
                regime      = "range",
            )

        # Pine: rangeShort = rangeRegime and rsi > rsiOB
        if snap.rsi > RSI_OB:
            return Signal(
                signal_type = SignalType.RANGE_SHORT,
                is_long     = False,
                regime      = "range",
            )

    return _NO_SIGNAL
