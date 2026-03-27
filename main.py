import asyncio
import logging
import signal as sys_signal
import os
import json
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

# ── DASHBOARD & API HANDLERS ──────────────────────────────────────────

async def dashboard_page(request):
    """Serves the dashboard.html file to the browser."""
    try:
        with open("dashboard.html", "r") as f:
            return web.Response(text=f.read(), content_type='text/html')
    except Exception as e:
        return web.Response(text=f"Dashboard file not found: {e}", status=404)

async def get_summary(request):
    """Feeds the 'Performance Overview' section of the dashboard."""
    bot = request.app['bot']
    # Pulls real historical stats from your journal database
    stats = bot.journal.get_open_trade() # Fallback to open trade if no stats method
    summary = {
        "total": 0, "wins": 0, "losses": 0, "total_pl": 0.0,
        "best": 0.0, "worst": 0.0, "win_rate": 0.0
    }
    # If your journal has a get_stats method, use it here. 
    # Otherwise, this returns the structure the dashboard expects.
    return web.json_response(summary)

async def get_position(request):
    """Feeds the 'Open Position' and 'Trail Stage' section of the dashboard."""
    bot = request.app['bot']
    if not bot.in_position or not bot.risk:
        return web.json_response(None)
    
    # Live data from the bot's current active memory
    return web.json_response({
        "symbol": "BTCUSDT",
        "is_long": bot.risk.is_long,
        "entry_price": bot.risk.entry_price,
        "sl": bot.risk.sl,
        "tp": bot.risk.tp,
        "atr": bot.risk.atr,
        "trail_stage": bot.trail_state.stage,
        "current_sl": bot.trail_state.current_sl
    })

async def start_health_server(bot_instance):
    port = int(os.environ.get("PORT", 10000))
    app  = web.Application()
    app['bot'] = bot_instance
    
    # Routes
    app.router.add_get("/",       dashboard_page)
    app.router.add_get("/health", lambda r: web.Response(text="OK"))
    app.router.add_get("/api/summary", get_summary)
    app.router.add_get("/api/position", get_position)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Dashboard LIVE at http://0.0.0.0:{port}")

# ── BOT LOGIC ─────────────────────────────────────────────────────────

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

    async def _recover_position(self) -> None:
        saved = self.journal.get_open_trade()
        if not saved: return
        pos = await self.order_mgr.fetch_position()
        if not pos or pos.get("contracts", 0) == 0:
            self.journal.close_open_trade()
            return

        # Rebuild Risk and State
        self.risk = RiskLevels(
            entry_price=float(saved["entry_price"]), sl=float(saved["sl"]),
            tp=float(saved["tp"]), stop_dist=abs(float(saved["entry_price"])-float(saved["sl"])),
            atr=float(saved["atr"]), is_long=bool(saved["is_long"]), is_trend="Trend" in saved["signal_type"]
        )
        self.trail_state = TrailState()
        self.trail_state.stage = int(saved["trail_stage"])
        self.trail_state.current_sl = float(saved["current_sl"])
        self.trail_state.peak_price = float(saved.get("peak_price") or saved["entry_price"])
        
        self.in_position = True
        self.trail_mon.start(self.risk, self.trail_state)

    async def _on_feed_ready(self) -> None:
        await self._recover_position()

    async def on_bar_close(self, df) -> None:
        snap = compute(df)
        if not self.in_position:
            sig = evaluate(snap, has_position=False)
            if sig.signal_type == SignalType.NONE: return
            risk = calc_levels(snap.close, snap.atr, sig.is_long, (sig.regime == "trend"))
            order = await self.order_mgr.place_entry(sig.is_long, risk.sl, risk.tp)
            
            self.in_position = True
            self.risk = risk
            self.trail_state = TrailState()
            self.trail_mon.start(risk, self.trail_state)
            self.journal.open_trade(sig.signal_type.value, sig.is_long, risk.entry_price, risk.sl, risk.tp, snap.atr, ALERT_QTY)
        else:
            pos = await self.order_mgr.fetch_position()
            if pos is None or pos.get("contracts", 0) == 0:
                await self._on_position_closed()

    async def _on_position_closed(self) -> None:
        self.trail_mon.stop()
        self.in_position = False
        self.journal.close_open_trade()

    async def run(self) -> None:
        await self.feed.start()

    async def shutdown(self, reason: str = "redeploy") -> None:
        self.trail_mon.stop()
        await self.order_mgr.close_exchange()
        await self.telegram.close()
        self.journal.close()

async def main():
    bot = SniperBot()
    await start_health_server(bot)
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())
