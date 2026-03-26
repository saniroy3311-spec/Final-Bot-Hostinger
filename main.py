"""
main.py - Shiva Sniper v6.5 Python Bot

FIXES vs original:
  1. SIGTERM → "🔄 Redeploying..." instead of "Bot stopped"
  2. SIGINT  → "🛑 Bot stopped (manual)" for real stops
  3. Position recovery on startup — resumes open trade after redeploy
"""

import asyncio
import logging
import signal as sys_signal
import os
from aiohttp import web
from feed.ws_feed       import CandleFeed
from indicators.engine  import compute
from strategy.signal    import evaluate, SignalType
from risk.calculator    import calc_levels, TrailState, calc_real_pl, RiskLevels
from orders.manager     import OrderManager
from monitor.trail_loop import TrailMonitor
from infra.telegram     import Telegram
from infra.journal      import Journal
from config             import ALERT_QTY

logging.basicConfig(
    level  = logging.INFO,
    format = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("main")


# ── Health check server ────────────────────────────────────────────────
async def health(request):
    return web.Response(text="Shiva Sniper Bot running")

async def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    app  = web.Application()
    app.router.add_get("/",       health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health server started on port {port}")


class SniperBot:
    def __init__(self):
        self.order_mgr   = OrderManager()
        self.telegram    = Telegram()
        self.journal     = Journal()
        self.trail_mon   = TrailMonitor(self.order_mgr, self.telegram, self.journal)
        self.feed        = CandleFeed(self.on_bar_close, self._on_feed_ready)
        self.in_position = False
        self.signal_type = SignalType.NONE
        self.risk        = None
        self.trail_state = None

    # ── FIX #2: Position recovery after redeploy ──────────────────────
    async def _recover_position(self) -> None:
        """
        On startup: check DB for an open trade.
        If found, verify it is still live on the exchange, then resume
        trail monitoring from the saved trail_stage + current_sl.
        """
        saved = self.journal.get_open_trade()
        if not saved:
            logger.info("No open trade in DB — starting fresh")
            return

        logger.info(
            f"Open trade found in DB: {saved['signal_type']} "
            f"entry={saved['entry_price']:.2f} "
            f"current_sl={saved['current_sl']:.2f} "
            f"trail_stage={saved['trail_stage']}"
        )

        # Confirm position is still live on the exchange
        try:
            pos = await self.order_mgr.fetch_position()
        except Exception as e:
            logger.error(f"Recovery: could not fetch position: {e}")
            return

        if not pos or pos.get("contracts", 0) == 0:
            logger.warning("Recovery: position not found on exchange — clearing DB")
            self.journal.close_open_trade()
            await self.telegram.send(
                "⚠️ <b>Recovery alert</b>\n"
                "Position was closed while bot was offline.\n"
                "Journal cleared — watching for new signals."
            )
            return

        # Rebuild state and resume trail monitor
        is_long  = bool(saved["is_long"])
        sig_type = saved["signal_type"]
        is_trend = "Trend" in sig_type

        risk = RiskLevels(
            entry_price = float(saved["entry_price"]),
            sl          = float(saved["sl"]),
            tp          = float(saved["tp"]),
            stop_dist   = abs(float(saved["entry_price"]) - float(saved["sl"])),
            atr         = float(saved["atr"]),
            is_long     = is_long,
            is_trend    = is_trend,
        )

        trail_state            = TrailState()
        trail_state.stage      = int(saved["trail_stage"])
        trail_state.current_sl = float(saved["current_sl"])
        # FIX R3: restore peak_price from DB (was always reset to entry_price,
        # causing trail stages to lag until price made a new high/low after restart)
        trail_state.peak_price = float(saved.get("peak_price") or saved["entry_price"])

        self.in_position = True
        self.signal_type = SignalType(sig_type)
        self.risk        = risk
        self.trail_state = trail_state
        self.trail_mon.start(risk, trail_state)

        logger.info(
            f"Position RECOVERED | {sig_type} "
            f"entry={risk.entry_price:.2f} sl={trail_state.current_sl:.2f} "
            f"stage={trail_state.stage}"
        )
        await self.telegram.send(
            f"♻️ <b>Position Recovered</b> — {sig_type}\n"
            f"Entry  : {risk.entry_price:.2f}\n"
            f"SL now : {trail_state.current_sl:.2f}\n"
            f"TP     : {risk.tp:.2f}\n"
            f"Stage  : {trail_state.stage}\n"
            f"<i>Restarted mid-trade — monitoring resumed.</i>"
        )

    async def _on_feed_ready(self) -> None:
        self.journal.log_event("BOT_START", "Feed ready — watching BTC/USDT:USDT")
        await self.telegram.send(
            "🟢 <b>Shiva Sniper Bot started</b>\n"
            "Feed ready — watching BTC/USDT:USDT"
        )
        await self._recover_position()

    async def on_bar_close(self, df) -> None:
        try:
            snap = compute(df)
        except ValueError as e:
            logger.warning(f"Indicator warmup: {e}")
            return

        logger.info(
            f"Bar | ADX={snap.adx:.1f} ATR={snap.atr:.1f} "
            f"Trend={snap.trend_regime} Range={snap.range_regime} "
            f"Filters={snap.filters_ok} "
            f"[atr_ok={snap.atr_ok} vol_ok={snap.vol_ok} body_ok={snap.body_ok}] "
            f"vol={snap.volume:.0f} vol_sma={snap.vol_sma:.0f} "
            f"body={abs(snap.close - snap.open):.1f} "
            f"body_min={snap.atr * 0.3:.1f}"
        )

        if not self.in_position:
            sig = evaluate(snap, has_position=False)
            if sig.signal_type == SignalType.NONE:
                return

            logger.info(f"Signal: {sig.signal_type.value}")
            risk = calc_levels(
                entry_price = snap.close,
                atr         = snap.atr,
                is_long     = sig.is_long,
                is_trend    = (sig.regime == "trend"),
            )
            try:
                # FIX R5: wrap in timeout so a slow/hanging order call
                # cannot block the event loop and cause the next bar to be missed.
                order = await asyncio.wait_for(
                    self.order_mgr.place_entry(
                        is_long = sig.is_long,
                        sl      = risk.sl,
                        tp      = risk.tp,
                    ),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.error("Entry order timed out after 5s — skipping this signal")
                self.journal.log_event("ENTRY_TIMEOUT", "place_entry exceeded 5s")
                await self.telegram.send("⚠️ Entry timed out (>5s) — signal skipped")
                return
            except Exception as e:
                logger.error(f"Entry failed: {e}")
                self.journal.log_event("ENTRY_FAILED", str(e))
                await self.telegram.send(f"⚠️ Entry failed: {e}")
                return

            actual_fill = float(
                order.get("average") or order.get("price") or snap.close
            )
            if actual_fill and actual_fill != snap.close:
                logger.info(
                    f"Fill price {actual_fill:.2f} differs from bar close "
                    f"{snap.close:.2f} — updating risk levels"
                )
                risk = calc_levels(
                    entry_price = actual_fill,
                    atr         = snap.atr,
                    is_long     = sig.is_long,
                    is_trend    = (sig.regime == "trend"),
                )

            self.in_position = True
            self.signal_type = sig.signal_type
            self.risk        = risk
            self.trail_state = TrailState()
            self.trail_mon.start(risk, self.trail_state)

            self.journal.open_trade(
                signal_type = sig.signal_type.value,
                is_long     = sig.is_long,
                entry_price = risk.entry_price,
                sl          = risk.sl,
                tp          = risk.tp,
                atr         = snap.atr,
                qty         = ALERT_QTY,
            )
            await self.telegram.notify_entry(
                signal_type = sig.signal_type.value,
                entry_price = risk.entry_price,
                sl          = risk.sl,
                tp          = risk.tp,
                atr         = snap.atr,
            )
        else:
            pos = await self.order_mgr.fetch_position()
            if pos is None or pos.get("contracts", 0) == 0:
                await self._on_position_closed()

    async def _on_position_closed(self) -> None:
        self.trail_mon.stop()

        exit_price = await self.order_mgr.fetch_last_trade_price()
        if exit_price is None:
            exit_price = self.risk.entry_price
            logger.warning("Could not fetch exit price — using entry price as fallback")

        real_pl     = calc_real_pl(
            entry_px = self.risk.entry_price,
            exit_px  = exit_price,
            qty      = ALERT_QTY,
            is_long  = self.risk.is_long,
        )
        exit_reason = "Max SL" if self.trail_state.max_sl_fired else "TP/SL"

        self.journal.log_trade(
            signal_type = self.signal_type.value,
            is_long     = self.risk.is_long,
            entry_price = self.risk.entry_price,
            exit_price  = exit_price,
            sl          = self.risk.sl,
            tp          = self.risk.tp,
            atr         = self.risk.atr,
            qty         = ALERT_QTY,
            real_pl     = real_pl,
            exit_reason = exit_reason,
            trail_stage = self.trail_state.stage,
        )
        self.journal.close_open_trade()
        await self.telegram.notify_exit(
            reason      = exit_reason,
            entry_price = self.risk.entry_price,
            exit_price  = exit_price,
            real_pl     = real_pl,
        )
        self.in_position = False
        self.signal_type = SignalType.NONE
        self.risk        = None
        self.trail_state = None
        logger.info(f"Position closed | P/L={real_pl:.2f} USDT")

    async def run(self) -> None:
        logger.info("Shiva Sniper Bot v6.5 - Starting...")
        await self.feed.start()

    # FIX #1: separate SIGTERM (redeploy) from SIGINT (manual stop)
    async def shutdown(self, reason: str = "redeploy") -> None:
        self.trail_mon.stop()
        self.journal.log_event("BOT_STOP", f"Shutdown: {reason}")
        await self.order_mgr.close_exchange()

        if reason == "redeploy":
            # Render sends SIGTERM on every new deploy — NOT a crash
            await self.telegram.send(
                "🔄 <b>Redeploying...</b>\n"
                "<i>New version starting — back in ~30 seconds.</i>"
            )
        else:
            # Manual Ctrl+C or kill — genuinely stopped
            await self.telegram.send("🛑 <b>Bot stopped</b> (manual)")

        await self.telegram.close()
        self.journal.close()


async def main():
    await start_health_server()

    bot  = SniperBot()
    loop = asyncio.get_running_loop()

    # FIX #1: SIGTERM = Render redeploy, SIGINT = manual stop
    loop.add_signal_handler(
        sys_signal.SIGTERM,
        lambda: asyncio.create_task(bot.shutdown(reason="redeploy"))
    )
    loop.add_signal_handler(
        sys_signal.SIGINT,
        lambda: asyncio.create_task(bot.shutdown(reason="manual"))
    )

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
