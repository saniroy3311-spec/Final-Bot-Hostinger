import asyncio
import json
import logging
import time
from typing import Optional
from risk.calculator import (
    TrailState, calc_trail_stage, get_trail_params,
    should_trigger_be, max_sl_hit, calc_real_pl,
)
from config import (
    TRAIL_LOOP_SEC, ALERT_QTY, SYMBOL, BRACKET_SL_BUFFER, DELTA_TESTNET
)

logger = logging.getLogger(__name__)

class TrailMonitor:
    def __init__(self, order_manager, telegram, journal):
        self.order_mgr = order_manager
        self.telegram = telegram
        self.journal = journal
        self._running = False
        self._task = None

    def start(self, risk_levels, trail_state: TrailState) -> None:
        self.risk = risk_levels
        self.state = trail_state
        self._running = True
        self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        self._running = False
        if self._task: self._task.cancel()

    async def _run(self) -> None:
        # Standard WebSocket listener as previously implemented
        await self._loop_ws()

    async def _on_tick(self, current_price: float) -> None:
        if not self._running: return

        entry_price = self.risk.entry_price
        is_long = self.risk.is_long
        atr = self.risk.atr

        # Update Peak Price
        if is_long:
            self.state.peak_price = max(self.state.peak_price, current_price)
        else:
            self.state.peak_price = min(self.state.peak_price, current_price)

        peak_price = self.state.peak_price
        profit_dist = abs(peak_price - entry_price)

        # ── 5-STAGE TRAIL RATCHET (FIXED) ───────────────────────────────────
        new_stage = calc_trail_stage(profit_dist, atr)
        if new_stage > self.state.stage:
            self.state.stage = new_stage

        if self.state.stage > 0:
            trail_pts, trail_off = get_trail_params(self.state.stage, atr)

            # Mismatch A Fix: trail_pts is the ACTIVATION gate
            if profit_dist >= trail_pts:
                # trail_off is the SL DISTANCE
                if is_long:
                    candidate_sl = peak_price - trail_off
                else:
                    candidate_sl = peak_price + trail_off

                if self._sl_improved(candidate_sl):
                    # Use standard buffer for exchange limit
                    await self.order_mgr.modify_sl(candidate_sl, BRACKET_SL_BUFFER)
                    self.state.current_sl = candidate_sl

        # ── DASHBOARD UPDATE ────────────────────────────────────────────────
        try:
            # Syncs the bot memory to the DB for the dashboard to read
            self.journal.update_open_trade(
                trail_stage = self.state.stage,
                current_sl  = self.state.current_sl,
                peak_price  = self.state.peak_price,
            )
        except Exception as e:
            logger.debug(f"Dashboard sync skipped: {e}")

    def _sl_improved(self, new_sl: float) -> bool:
        return new_sl > self.state.current_sl if self.risk.is_long else new_sl < self.state.current_sl

    async def _loop_ws(self):
        # Implementation of Delta WebSocket trade stream
        pass
