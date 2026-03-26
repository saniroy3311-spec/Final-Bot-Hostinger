"""
monitor/trail_loop.py
Async 1-second loop — replicates Pine's per-tick trail engine:
  - 5-stage trail ratchet (anchored to PEAK price, not current price)
  - Breakeven move
  - Max SL guard (uses stored ATR from RiskLevels, not reverse-engineered)
  - Dynamic SL modify on exchange via sl_order_id

FIXES (vs original):
  1. TRAIL FORMULA (Critical):
     Old: candidate_sl = current_price - trail_pts - trail_off  (WRONG)
     New: candidate_sl = peak_price - trail_pts                 (CORRECT)
     Pine's strategy.exit(trail_points=X) anchors to the HIGHEST HIGH
     seen since entry (for longs). The bot must track peak_price and
     compute the trail SL from that, not from the current tick price.
     trail_offset is a limit-order slippage buffer, NOT part of SL calc.

  2. TRAIL OFFSET REMOVED FROM SL (Bug):
     trail_offset was being subtracted from candidate_sl, double-counting
     it as extra distance. It belongs only in the limit order placement
     (bracket_stop_loss_limit_price), not in the stop trigger price.

  3. MAX SL ATR (Critical):
     Old: reverse-engineered ATR from stop_dist (locked at entry value,
          and wrong for trend trades where atrMult != 1.0).
     New: uses self.risk.atr directly (stored in RiskLevels at entry).

  4. MAX SL PRICE CHECK (Bug):
     Old: used markPrice only -> missed wicks that triggered SL.
     New: tracks worst_price (min for longs, max for shorts) across
          all ticks so any intra-second wick is caught.

  5. PEAK PRICE TRACKING:
     TrailState.peak_price updated every tick to the best price seen.
     Initialised to entry_price on trade open.
"""

import asyncio
import logging
from risk.calculator import (
    TrailState, calc_trail_stage, get_trail_points,
    should_trigger_be, max_sl_hit, calc_real_pl,
)
from config import TRAIL_LOOP_SEC, ALERT_QTY, SYMBOL, CANDLE_TIMEFRAME

logger = logging.getLogger(__name__)


class TrailMonitor:
    """
    Runs as a background asyncio task while position is open.
    Calls order_manager.modify_sl() when SL needs to ratchet.
    """

    def __init__(self, order_manager, telegram, journal):
        self.order_mgr = order_manager
        self.telegram  = telegram
        self.journal   = journal
        self._running  = False
        self._task     = None

    def start(self, risk_levels, trail_state: TrailState) -> None:
        """Start monitoring. Call immediately after entry fill."""
        self.risk  = risk_levels
        self.state = trail_state

        # Initialise SL and peak from the actual fill entry price
        self.state.current_sl  = risk_levels.sl
        self.state.peak_price  = risk_levels.entry_price   # FIX #5
        self._running = True
        self._task    = asyncio.create_task(self._loop())
        logger.info(
            f"Trail monitor started | entry={risk_levels.entry_price:.2f} "
            f"sl={risk_levels.sl:.2f} tp={risk_levels.tp:.2f} "
            f"atr={risk_levels.atr:.2f}"
        )

    def stop(self) -> None:
        """Stop monitoring. Call on position close."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Trail monitor stopped")

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.error(f"Trail loop error: {e}", exc_info=True)
            await asyncio.sleep(TRAIL_LOOP_SEC)

    async def _tick(self) -> None:
        """Single evaluation — called every TRAIL_LOOP_SEC seconds."""
        pos = await self.order_mgr.fetch_position()
        if not pos or pos.get("contracts", 0) == 0:
            logger.info("Position closed externally — stopping trail monitor")
            self.stop()
            return

        # Use markPrice for the current tick price
        current_price = float(
            pos.get("markPrice") or pos.get("lastPrice") or
            pos.get("info", {}).get("mark_price", self.risk.entry_price)
        )

        entry_price = self.risk.entry_price
        is_long     = self.risk.is_long
        atr         = self.risk.atr    # FIX #3: use stored ATR directly

        # ── Update peak price (FIX #5) ────────────────────────────────
        # Pine tracks highest high (long) / lowest low (short) since entry.
        # We approximate with mark price each tick (close enough for 1s loop).
        if is_long:
            self.state.peak_price = max(self.state.peak_price, current_price)
        else:
            self.state.peak_price = min(self.state.peak_price, current_price)

        peak_price  = self.state.peak_price

        # ── Profit distance ───────────────────────────────────────────
        # Uses peak_price so trail stage upgrades happen at the right moment
        profit_dist = (peak_price - entry_price) if is_long \
                      else (entry_price - peak_price)

        # ── Max SL guard (FIX R2: check bar wick via fetch_ohlcv) ────
        # Mark price catches most cases; but intra-second wicks can spike
        # past maxSL without being reflected in the REST mark price poll.
        # We now also fetch the latest completed candle low/high to catch wicks.
        wick_price = current_price
        try:
            ohlcv = self.order_mgr.exchange.fetch_ohlcv(
                SYMBOL, CANDLE_TIMEFRAME, limit=1
            )
            if ohlcv:
                bar = ohlcv[-1]
                # For longs, worst price is the bar low; for shorts, the bar high
                wick_price = float(bar[3]) if is_long else float(bar[2])
        except Exception as e:
            logger.debug(f"wick fetch skipped: {e}")

        # Use the worse of mark price vs wick for max SL check
        worst_price = min(current_price, wick_price) if is_long \
                      else max(current_price, wick_price)

        if not self.state.max_sl_fired and max_sl_hit(
                worst_price, entry_price, atr, is_long):
            logger.warning(
                f"Max SL hit | worst_price={worst_price:.2f} "
                f"entry={entry_price:.2f} atr={atr:.2f}"
            )
            await self.order_mgr.close_position("Max SL Hit")
            real_pl = calc_real_pl(entry_price, worst_price, ALERT_QTY, is_long)
            await self.telegram.send(
                f"\U0001f534 MAX SL HIT\n"
                f"Price : {worst_price:.2f}\n"
                f"Entry : {entry_price:.2f}\n"
                f"P/L   : {real_pl:+.2f} USDT"
            )
            self.state.max_sl_fired = True
            self.stop()
            return

        # ── Breakeven ─────────────────────────────────────────────────
        # Measure from entry_price (not peak) — mirrors Pine profitDist
        entry_profit = (current_price - entry_price) if is_long \
                       else (entry_price - current_price)
        if not self.state.be_done and should_trigger_be(entry_profit, atr):
            new_sl = entry_price
            if self._sl_improved(new_sl):
                logger.info(f"Breakeven triggered -> SL moved to {new_sl:.2f}")
                await self.order_mgr.modify_sl(new_sl)
                self.state.current_sl = new_sl
                self.state.be_done    = True
                await self.telegram.send(
                    f"\u26a1 BREAKEVEN\n"
                    f"SL moved to entry: {new_sl:.2f}"
                )

        # ── 5-Stage Trail Ratchet (FIX #1 + #2) ──────────────────────
        # Stage upgrade uses profit_dist from PEAK (not current tick)
        new_stage = calc_trail_stage(profit_dist, atr)
        if new_stage > self.state.stage:
            logger.info(
                f"Trail stage {self.state.stage} -> {new_stage} "
                f"| peak={peak_price:.2f} profit={profit_dist:.2f}"
            )
            self.state.stage = new_stage

        if self.state.stage > 0:
            trail_pts = get_trail_points(self.state.stage, atr)

            # FIX #1: SL = peak_price - trail_pts  (not current_price - pts - off)
            # Pine: strategy.exit(trail_points=X) -> stop = highest_high - X
            if is_long:
                candidate_sl = peak_price - trail_pts
            else:
                candidate_sl = peak_price + trail_pts

            if self._sl_improved(candidate_sl):
                logger.info(
                    f"Trail ratchet [S{self.state.stage}] "
                    f"{self.state.current_sl:.2f} -> {candidate_sl:.2f} "
                    f"(peak={peak_price:.2f} trail_pts={trail_pts:.2f})"
                )
                await self.order_mgr.modify_sl(candidate_sl)
                self.state.current_sl = candidate_sl

    def _sl_improved(self, new_sl: float) -> bool:
        """
        Only modify SL if it moves in the protective direction (ratchet only).
        Long: new SL must be higher than current.
        Short: new SL must be lower than current.
        """
        if self.risk.is_long:
            return new_sl > self.state.current_sl
        else:
            return new_sl < self.state.current_sl
