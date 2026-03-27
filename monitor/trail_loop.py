"""
monitor/trail_loop.py  ── WebSocket-driven trail engine
═══════════════════════════════════════════════════════════════════════════════

FIX 1 ── THE 1-SECOND SLEEP BLIND SPOT
  WebSocket trade stream → _on_tick() fires on every exchange trade (~10-50ms).
  REST fallback kept for when WS is unavailable.

FIX 2 ── MARK PRICE → LAST TRADED PRICE
  Price sourced from raw trade stream — matches TradingView exactly.

FIX 3 ── TRAIL DISTANCE CORRECTED  ★ Mismatch A — Critical ★
──────────────────────────────────────────────────────────────────────────────
  WRONG (previous):
    candidate_sl = peak_price - trail_pts   ← trail_pts is the ACTIVATION
                                               threshold, NOT the SL distance
    modify_sl(candidate_sl, trail_off)      ← trail_off used as exchange buffer

  CORRECT (this file):
    if profit_dist >= trail_pts:            ← trail_pts = activation gate only
        candidate_sl = peak_price - trail_off  ← trail_off = physical SL distance
        modify_sl(candidate_sl, BRACKET_SL_BUFFER)  ← flat buffer for limit order

  Pine Script: strategy.exit(trail_points=X, trail_offset=Y)
    trail_points (X) → profit must reach this BEFORE trailing activates
    trail_offset (Y) → how far behind peak the stop sits once active
  The previous bot used trail_pts as the SL distance, making stops much wider.

FIX 4 ── MAX SL — instant tick trigger  ★ Mismatch C ★
  Bot fires market close the instant last-traded price crosses maxSLDist.
  Pine Script fix mirrors this with strategy.exit(stop=...) — see .pine file.
"""

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
    TRAIL_LOOP_SEC, ALERT_QTY, SYMBOL,
    BRACKET_SL_BUFFER, DELTA_TESTNET,
)

logger = logging.getLogger(__name__)

_WS_URL_LIVE       = "wss://socket.delta.exchange"
_WS_URL_TEST       = "wss://testnet-socket.delta.exchange"
MAX_WS_FAILURES    = 3
_MIN_TICK_INTERVAL = 0.005   # 5 ms de-bounce


class TrailMonitor:
    """
    Runs as a background asyncio task while position is open.
    Feeds every exchange trade tick into the trail engine — no polling sleep.
    """

    def __init__(self, order_manager, telegram, journal):
        self.order_mgr       = order_manager
        self.telegram        = telegram
        self.journal         = journal
        self._running        = False
        self._task: Optional[asyncio.Task] = None
        self._last_tick_time = 0.0
        self._ws_failures    = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self, risk_levels, trail_state: TrailState) -> None:
        """Start monitoring. Call immediately after entry fill."""
        self.risk  = risk_levels
        self.state = trail_state

        self.state.current_sl = risk_levels.sl
        self.state.peak_price = risk_levels.entry_price
        self._running         = True
        self._ws_failures     = 0

        self._task = asyncio.create_task(self._run())
        logger.info(
            f"Trail monitor started (WebSocket) | "
            f"entry={risk_levels.entry_price:.2f} "
            f"sl={risk_levels.sl:.2f} tp={risk_levels.tp:.2f} "
            f"atr={risk_levels.atr:.2f}"
        )

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Trail monitor stopped")

    # ── Top-level runner ──────────────────────────────────────────────────────

    async def _run(self) -> None:
        while self._running:
            if self._ws_failures >= MAX_WS_FAILURES:
                logger.warning(
                    f"WS failed {self._ws_failures}x — switching to REST fallback"
                )
                await self._loop_rest()
                return
            try:
                await self._loop_ws()
            except asyncio.CancelledError:
                return
            except Exception as e:
                self._ws_failures += 1
                logger.error(
                    f"WS loop error ({self._ws_failures}/{MAX_WS_FAILURES}): {e}",
                    exc_info=True,
                )
                if self._running:
                    await asyncio.sleep(1)

    # ── WebSocket listener ────────────────────────────────────────────────────

    async def _loop_ws(self) -> None:
        import websockets  # soft import so REST fallback works if not installed

        ws_url  = _WS_URL_TEST if DELTA_TESTNET else _WS_URL_LIVE
        product = SYMBOL.replace("/", "").replace(":USDT", "").replace("-PERP", "")
        channel = f"recent_trade.{product}"

        subscribe_msg = json.dumps({
            "type"   : "subscribe",
            "payload": {"channels": [{"name": channel}]},
        })

        logger.info(f"Connecting WS: {ws_url}  channel={channel}")

        async with websockets.connect(
            ws_url, ping_interval=20, ping_timeout=10, close_timeout=5,
        ) as ws:
            await ws.send(subscribe_msg)
            self._ws_failures = 0

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg      = json.loads(raw)
                    msg_type = msg.get("type", "")
                    if msg_type == "recent_trade":
                        result    = msg.get("result") or {}
                        price_str = result.get("price") or result.get("p")
                        if price_str:
                            await self._on_tick(float(price_str))
                    elif msg_type == "subscriptions":
                        logger.info(f"WS subscribed: {msg}")
                except Exception as e:
                    logger.debug(f"WS parse error: {e}")

    # ── REST fallback ─────────────────────────────────────────────────────────

    async def _loop_rest(self) -> None:
        logger.warning("REST fallback active — install 'websockets' for WS mode")
        while self._running:
            try:
                await self._tick_rest()
            except Exception as e:
                logger.error(f"REST trail error: {e}", exc_info=True)
            await asyncio.sleep(TRAIL_LOOP_SEC)

    async def _tick_rest(self) -> None:
        pos = await self.order_mgr.fetch_position()
        if not pos or pos.get("contracts", 0) == 0:
            logger.info("Position closed externally — stopping trail monitor")
            self.stop()
            return
        current_price = float(
            pos.get("lastPrice") or
            pos.get("markPrice") or
            pos.get("info", {}).get("last_price") or
            self.risk.entry_price
        )
        await self._on_tick(current_price)

    # ── Core tick handler ─────────────────────────────────────────────────────

    async def _on_tick(self, current_price: float) -> None:
        """
        Runs on every WS trade event. No sleep — instant reaction.

        FIX 3: trail_pts = activation threshold, trail_off = SL distance.
        """
        now = time.monotonic()
        if now - self._last_tick_time < _MIN_TICK_INTERVAL:
            return
        self._last_tick_time = now

        if not self._running:
            return

        entry_price = self.risk.entry_price
        is_long     = self.risk.is_long
        atr         = self.risk.atr

        # ── Update peak price ─────────────────────────────────────────────────
        if is_long:
            self.state.peak_price = max(self.state.peak_price, current_price)
        else:
            self.state.peak_price = min(self.state.peak_price, current_price)

        peak_price  = self.state.peak_price
        profit_dist = (peak_price - entry_price) if is_long \
                      else (entry_price - peak_price)

        # ── Max SL guard ──────────────────────────────────────────────────────
        if not self.state.max_sl_fired and max_sl_hit(
                current_price, entry_price, atr, is_long):
            logger.warning(
                f"Max SL hit | price={current_price:.2f} "
                f"entry={entry_price:.2f} atr={atr:.2f}"
            )
            await self.order_mgr.close_position("Max SL Hit")
            real_pl = calc_real_pl(entry_price, current_price, ALERT_QTY, is_long)
            await self.telegram.send(
                f"\U0001f534 MAX SL HIT\n"
                f"Price : {current_price:.2f}\n"
                f"Entry : {entry_price:.2f}\n"
                f"P/L   : {real_pl:+.2f} USDT"
            )
            self.state.max_sl_fired = True
            self.stop()
            return

        # ── Breakeven ─────────────────────────────────────────────────────────
        entry_profit = (current_price - entry_price) if is_long \
                       else (entry_price - current_price)
        if not self.state.be_done and should_trigger_be(entry_profit, atr):
            new_sl = entry_price
            if self._sl_improved(new_sl):
                logger.info(f"Breakeven triggered -> SL={new_sl:.2f}")
                await self.order_mgr.modify_sl(new_sl, BRACKET_SL_BUFFER)
                self.state.current_sl = new_sl
                self.state.be_done    = True
                await self.telegram.send(
                    f"\u26a1 BREAKEVEN\nSL moved to entry: {new_sl:.2f}"
                )

        # ── 5-Stage Trail Ratchet ─────────────────────────────────────────────
        new_stage = calc_trail_stage(profit_dist, atr)
        if new_stage > self.state.stage:
            logger.info(
                f"Trail stage {self.state.stage} -> {new_stage} "
                f"| peak={peak_price:.2f} profit={profit_dist:.2f}"
            )
            self.state.stage = new_stage

        if self.state.stage > 0:
            trail_pts, trail_off = get_trail_params(self.state.stage, atr)

            # ── FIX 3: trail_pts is the ACTIVATION gate only ──────────────────
            # Pine: strategy.exit(trail_points=X, trail_offset=Y)
            #   X (trail_pts)  → profit distance needed to ACTIVATE the trail
            #   Y (trail_off)  → how far behind peak the SL is PLACED
            if profit_dist >= trail_pts:
                if is_long:
                    candidate_sl = peak_price - trail_off   # FIX: was trail_pts
                else:
                    candidate_sl = peak_price + trail_off   # FIX: was trail_pts

                if self._sl_improved(candidate_sl):
                    logger.info(
                        f"Trail ratchet [S{self.state.stage}] "
                        f"{self.state.current_sl:.2f} -> {candidate_sl:.2f} "
                        f"(peak={peak_price:.2f} trail_off={trail_off:.2f})"
                    )
                    # Flat buffer for exchange limit order — not ATR-dynamic
                    await self.order_mgr.modify_sl(candidate_sl, BRACKET_SL_BUFFER)
                    self.state.current_sl = candidate_sl

        # ── Persist to journal ────────────────────────────────────────────────
        try:
            self.journal.update_open_trade(
                trail_stage = self.state.stage,
                current_sl  = self.state.current_sl,
                peak_price  = self.state.peak_price,
            )
        except Exception as e:
            logger.debug(f"journal update skipped: {e}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sl_improved(self, new_sl: float) -> bool:
        if self.risk.is_long:
            return new_sl > self.state.current_sl
        else:
            return new_sl < self.state.current_sl
