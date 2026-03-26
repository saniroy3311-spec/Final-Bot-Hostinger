"""
infra/telegram.py
Telegram notifications for entry, exit, errors, and trail events.
"""

import logging
import aiohttp
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


class Telegram:
    BASE = "https://api.telegram.org/bot"

    def __init__(self):
        self._session: aiohttp.ClientSession | None = None

    async def send(self, text: str) -> None:
        try:
            if not self._session:
                self._session = aiohttp.ClientSession()
            url = f"{self.BASE}{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = await self._session.post(url, json={
                "chat_id"    : TELEGRAM_CHAT_ID,
                "text"       : text,
                "parse_mode" : "HTML",
            })
            data = await resp.json()
            if not data.get("ok"):
                logger.error(f"Telegram API error: {data}")
            else:
                logger.info("Telegram message sent OK")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    async def notify_entry(self, signal_type: str, entry_price: float,
                           sl: float, tp: float, atr: float) -> None:
        emoji = "🟢" if "Long" in signal_type else "🔴"
        await self.send(
            f"{emoji} <b>ENTRY</b> — {signal_type}\n"
            f"Price : {entry_price:.2f}\n"
            f"SL    : {sl:.2f}\n"
            f"TP    : {tp:.2f}\n"
            f"ATR   : {atr:.2f}"
        )

    async def notify_exit(self, reason: str, entry_price: float,
                          exit_price: float, real_pl: float) -> None:
        emoji = "💰" if real_pl >= 0 else "🔻"
        await self.send(
            f"{emoji} <b>EXIT</b> — {reason}\n"
            f"Entry : {entry_price:.2f}\n"
            f"Exit  : {exit_price:.2f}\n"
            f"P/L   : {real_pl:+.2f} USDT"
        )

    async def close(self) -> None:
        if self._session:
            await self._session.close()
