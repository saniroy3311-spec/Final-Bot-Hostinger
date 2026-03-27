"""
monitor/trail_loop.py  ── WebSocket-driven trail engine (NO sleep blind spot)
═══════════════════════════════════════════════════════════════════════════════

FIX 1 ── THE 1-SECOND SLEEP BLIND SPOT (Root Cause)
─────────────────────────────────────────────────────
OLD: asyncio.sleep(TRAIL_LOOP_SEC)  → bot blind for 1 full second per tick
NEW: WebSocket trade stream → _on_tick() fires on EVERY exchange trade event
     (typically every 10–50 ms). No sleep. No blind spot.

FIX 2 ── MARK PRICE → LAST TRADED PRICE
─────────────────────────────────────────
OLD: current_price = pos.get("markPrice")  → index average, lags real fills
NEW: price is sourced from the raw trade stream ("lastPrice" / "p" field),
     which is the actual last traded price — exactly what TradingView uses.

FIX 3 ── BRACKET SL BUFFER IS NOW ATR-DYNAMIC
──────────────────────────────────────────────
OLD: sl_limit = new_sl - BRACKET_SL_BUFFER  (hardcoded 10 pts flat)
NEW: sl_limit buffer = atr * trail_offset_mult for the current stage,
     matching Pine's dynamic offset. Falls back to BRACKET_SL_BUFFER when
     stage == 0 (no active trail yet — breakeven or initial SL).

Architecture:
  TrailMonitor.start()  →  opens Binance/Delta WebSocket trade stream
  _on_tick(price)       →  runs full trail evaluation synchronously on every tick
  _loop_ws()            →  async WS listener, feeds prices into _on_tick()
  stop()                →  cancels WS task, closes socket cleanly

The REST fallback (_loop_rest) is kept as a safety net if the WS fails to
connect after MAX_WS_FAILURES retries. It uses TRAIL_LOOP_SEC as before.
"""

import asyncio
import json
import logging
import time
from typing import Optional

import ccxt.async_support as ccxt

from risk.calculator import (
    TrailState, calc_trail_stage, get_trail_points, get_trail_params,
    should_trigger_be, max_sl_hit, calc_real_pl,
)
from config import (
    TRAIL_LOOP_SEC, ALERT_QTY, SYMBOL, CANDLE_TIMEFRAME,
    BRACKET_SL_BUFFER, DELTA_API_KEY, DELTA_API_SECRET, DELTA_TESTNET,
)

logger = logging.getLogger(__name__)

# ── WebSocket settings ────────────────────────────────────────────────────────
# Delta Exchange websocket base URL.  Adjust if using testnet.
_WS_URL_LIVE = "wss://socket.delta.exchange"
_WS_URL_TEST = "wss://testnet-socket.delta.exchange"
MAX_WS_FAILURES = 3          # fall back to REST after this many WS errors
_MIN_TICK_INTERVAL = 0.005   # 5 ms de-bounce (ignore duplicate ticks)


class TrailMonitor:
    """
    Runs as a background asyncio task while position is open.
    Feeds every exchange trade tick into the trail engine — no polling sleep.
    """

    def __init__(self, order_manager, telegram, journal):
        self.order_mgr  = order_manager
        self.telegram   = telegram
        self.journal    = journal
        self._running   = False
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

        # Try WebSocket first; REST fallback if WS fails repeatedly
        self._task = asyncio.create_task(self._run())
        logger.info(
            f"Trail monitor started (WebSocket) | "
            f"entry={risk_levels.entry_price:.2f} "
            f"sl={risk_levels.sl:.2f} tp={risk_levels.tp:.2f} "
            f"atr={risk_levels.atr:.2f}"
        )

    def stop(self) -> None:
        """Stop monitoring. Call on position close."""
        self._running = False
        if self._task:
            self._task.cancel()
        logger.info("Trail monitor stopped")

    # ── Top-level runner (WS with REST fallback) ──────────────────────────────

    async def _run(self) -> None:
        while self._running:
            if self._ws_failures >= MAX_WS_FAILURES:
                logger.warning(
                    f"WS failed {self._ws_failures} times — switching to REST fallback"
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
                    f"WS loop error (attempt {self._ws_failures}/{MAX_WS_FAILURES}): {e}",
                    exc_info=True,
                )
                if self._running:
                    await asyncio.sleep(1)

    # ── WebSocket listener (FIX 1 + FIX 2) ───────────────────────────────────

    async def _loop_ws(self) -> None:
        """
        Subscribe to Delta Exchange's trade stream for SYMBOL.
        Fires _on_tick(last_price) on every fill event — no sleep.
        """
        import websockets  # soft import so REST fallback works if not installed

        ws_url = _WS_URL_TEST if DELTA_TESTNET else _WS_URL_LIVE

        # Delta Exchange WS channel: "recent_trade.<product_symbol>"
        # product symbol for BTC/USDT perp is "BTCUSDT"
        product = SYMBOL.replace("/", "").replace(":USDT", "").replace("-PERP", "")
        channel = f"recent_trade.{product}"

        subscribe_msg = json.dumps({
            "type": "subscribe",
            "payload": {"channels": [{"name": channel}]},
        })

        logger.info(f"Connecting WS trade stream: {ws_url}  channel={channel}")

        async with websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            await ws.send(subscribe_msg)
            self._ws_failures = 0   # reset on successful connect

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    # Delta sends: {"type":"recent_trade","result":{"price":"68200.5",...}}
                    msg_type = msg.get("type", "")
                    if msg_type == "recent_trade":
                        result = msg.get("result") or {}
                        price_str = result.get("price") or result.get("p")
                        if price_str:
                            price = float(price_str)
                            await self._on_tick(price)
                    elif msg_type == "subscriptions":
                        logger.info(f"WS subscribed: {msg}")
                except Exception as e:
                    logger.debug(f"WS msg parse error: {e}")

    # ── REST fallback (original behaviour, used only if WS unavailable) ───────

    async def _loop_rest(self) -> None:
        """
        Original 1-second REST polling loop, kept as fallback.
        Used only when WebSocket is unavailable.
        """
        logger.warning(
            "Using REST fallback trail loop — "
            "install 'websockets' package for WebSocket mode"
        )
        while self._running:
            try:
                await self._tick_rest()
            except Exception as e:
                logger.error(f"REST trail loop error: {e}", exc_info=True)
            await asyncio.sleep(TRAIL_LOOP_SEC)

    async def _tick_rest(self) -> None:
        """Single REST evaluation tick (fallback only)."""
        pos = await self.order_mgr.fetch_position()
        if not pos or pos.get("contracts", 0) == 0:
            logger.info("Position closed externally — stopping trail monitor")
            self.stop()
            return

        # FIX 2: prefer lastPrice (actual traded) over markPrice (index avg)
        current_price = float(
            pos.get("lastPrice") or
            pos.get("markPrice") or
            pos.get("info", {}).get("last_price") or
            self.risk.entry_price
        )
        await self._on_tick(current_price)

    # ── Core tick handler (called on every WS trade event) ────────────────────

    async def _on_tick(self, current_price: float) -> None:
        """
        Evaluate trail logic for a single price tick.
        Called on EVERY trade event from the WS stream — no sleep.

        FIX 1: No asyncio.sleep() here. The bot reacts within milliseconds.
        FIX 2: current_price is last traded price (from WS), not mark price.
        FIX 3: SL limit buffer is ATR-dynamic (stage offset), not hardcoded.
        """
        # ── De-bounce rapid duplicate ticks ──────────────────────────────────
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
        # current_price IS last traded price — no extra wick fetch needed.
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
                logger.info(f"Breakeven triggered -> SL moved to {new_sl:.2f}")
                # Stage 0 → use flat BRACKET_SL_BUFFER for limit distance
                sl_limit_buf = BRACKET_SL_BUFFER
                await self.order_mgr.modify_sl(new_sl, sl_limit_buf)
                self.state.current_sl = new_sl
                self.state.be_done    = True
                await self.telegram.send(
                    f"\u26a1 BREAKEVEN\n"
                    f"SL moved to entry: {new_sl:.2f}"
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
                # FIX 3: use ATR-dynamic offset (trail_off) as bracket buffer
                await self.order_mgr.modify_sl(candidate_sl, trail_off)
                self.state.current_sl = candidate_sl

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _sl_improved(self, new_sl: float) -> bool:
        if self.risk.is_long:
            return new_sl > self.state.current_sl
        else:
            return new_sl < self.state.current_sl
