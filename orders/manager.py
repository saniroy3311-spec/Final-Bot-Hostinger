"""
orders/manager.py
Delta Exchange India order execution via ccxt.
Handles: entry, OCO bracket, SL modify, emergency close.
All with retry + exponential backoff.

FIXES (vs original):
  1. BRACKET SL ORDER ID (Critical):
     Old: modify_sl() called edit_order(entry_order_id, ...) — WRONG.
          Delta creates a separate stop order for the bracket SL leg.
          Editing the entry order ID does nothing / throws an error.
     New: On entry fill, we extract the bracket SL order ID from the
          response (info.bracket_stop_loss_order_id). If not present in
          the response, we fetch open orders and locate the stop leg.
          modify_sl() uses self.sl_order_id for all edits.

  2. fetch_my_trades SYMBOL ARG (Bug):
     Old: fetch_my_trades(ALERT_QTY, limit=1) — first arg is symbol, not qty.
     New: fetch_my_trades(SYMBOL, limit=1)

  3. BRACKET SL BUFFER (Minor):
     bracket_stop_loss_limit_price buffer is now configurable via
     BRACKET_SL_BUFFER env var (default 10 pts) instead of hardcoded 5.
"""

import asyncio
import logging
from typing import Optional
import ccxt.async_support as ccxt
from config import (
    DELTA_API_KEY, DELTA_API_SECRET, DELTA_TESTNET,
    SYMBOL, ALERT_QTY, BRACKET_SL_BUFFER,
)

logger = logging.getLogger(__name__)


def build_exchange() -> ccxt.delta:
    """Initialise ccxt Delta Exchange client."""
    params = {
        "apiKey"         : DELTA_API_KEY,
        "secret"         : DELTA_API_SECRET,
        "enableRateLimit": True,
    }
    exchange = ccxt.delta(params)
    if DELTA_TESTNET:
        exchange.set_sandbox_mode(True)
    return exchange


async def _retry(coro_fn, retries: int = 3, delay: float = 1.0):
    """Retry coroutine with exponential backoff."""
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn()
        except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
            if attempt == retries:
                raise
            wait = delay * (2 ** (attempt - 1))
            logger.warning(f"Attempt {attempt} failed ({e}), retry in {wait}s")
            await asyncio.sleep(wait)


class OrderManager:
    def __init__(self):
        self.exchange      = build_exchange()
        self.position      : Optional[dict] = None
        self.entry_order   : Optional[dict] = None
        self.sl_order_id   : Optional[str]  = None   # FIX #1: bracket SL leg ID
        self.tp_order_id   : Optional[str]  = None

    # ── Entry ─────────────────────────────────────────────────────────
    async def place_entry(self, is_long: bool,
                          sl: float, tp: float) -> dict:
        """
        Market entry + OCO bracket (TP limit + SL stop).
        Delta Exchange supports bracket orders natively.

        After fill, extracts the bracket SL order ID for later modification.
        """
        side = "buy" if is_long else "sell"
        sl_limit = sl - BRACKET_SL_BUFFER if is_long else sl + BRACKET_SL_BUFFER
        logger.info(
            f"Placing {side.upper()} entry | "
            f"SL={sl:.2f} SL_limit={sl_limit:.2f} TP={tp:.2f}"
        )

        # ── Market entry with bracket ─────────────────────────────────
        order = await _retry(lambda: self.exchange.create_order(
            symbol = SYMBOL,
            type   = "market",
            side   = side,
            amount = ALERT_QTY,
            params = {
                "bracket_stop_loss_price"       : sl,
                "bracket_stop_loss_limit_price" : sl_limit,
                "bracket_take_profit_price"     : tp,
            }
        ))

        logger.info(f"Entry order response: {order}")

        # ── FIX #1: Extract bracket SL order ID ──────────────────────
        # Delta returns the stop-loss leg ID in info. Try multiple paths
        # as the exact field name can vary by API version / ccxt build.
        info = order.get("info", {})
        sl_id = (
            info.get("bracket_stop_loss_order_id") or
            info.get("stop_loss_order_id") or
            info.get("sl_order_id")
        )

        if sl_id:
            self.sl_order_id = str(sl_id)
            logger.info(f"Bracket SL order ID captured: {self.sl_order_id}")
        else:
            # Fallback: fetch open orders and find the stop leg
            logger.warning(
                "Bracket SL order ID not in entry response — "
                "fetching open orders to locate stop leg"
            )
            self.sl_order_id = await self._find_sl_order_id(is_long, sl)

        self.position = {
            "entry_order_id": order["id"],
            "is_long"       : is_long,
            "entry_price"   : float(order.get("average") or order.get("price") or 0),
        }
        logger.info(
            f"Entry filled | price={self.position['entry_price']:.2f} "
            f"sl_order_id={self.sl_order_id}"
        )
        return order

    async def _find_sl_order_id(self, is_long: bool,
                                 sl_price: float) -> Optional[str]:
        """
        Fallback: fetch open orders for SYMBOL and find the stop leg
        by matching side (opposite of entry) + stop type.
        """
        try:
            open_orders = await _retry(
                lambda: self.exchange.fetch_open_orders(SYMBOL)
            )
            sl_side = "sell" if is_long else "buy"
            for o in open_orders:
                o_type = (o.get("type") or "").lower()
                o_side = (o.get("side") or "").lower()
                o_price = float(o.get("stopPrice") or o.get("price") or 0)
                if (o_side == sl_side and
                        "stop" in o_type and
                        abs(o_price - sl_price) < 200):  # FIX R4: was 50 → 200 pts (volatile BTC safe margin)
                    logger.info(f"Found SL order by scan: {o['id']}")
                    return str(o["id"])
            logger.error("Could not find bracket SL order — SL modify will be skipped")
            return None
        except Exception as e:
            logger.error(f"Failed to scan for SL order: {e}")
            return None

    # ── Modify SL (for BE and trail ratchet) ─────────────────────────
    async def modify_sl(self, new_sl: float) -> None:
        """
        Modify the bracket stop loss on Delta Exchange.
        FIX #1: Uses self.sl_order_id (bracket SL leg), not the entry order ID.
        """
        if not self.position:
            logger.warning("modify_sl called but no position tracked")
            return

        if not self.sl_order_id:
            logger.warning("modify_sl: no sl_order_id — cannot modify SL")
            return

        is_long   = self.position["is_long"]
        sl_limit  = new_sl - BRACKET_SL_BUFFER if is_long else new_sl + BRACKET_SL_BUFFER
        sl_side   = "sell" if is_long else "buy"

        logger.info(
            f"Modifying SL | id={self.sl_order_id} "
            f"new_sl={new_sl:.2f} limit={sl_limit:.2f}"
        )

        try:
            await _retry(lambda: self.exchange.edit_order(
                id     = self.sl_order_id,
                symbol = SYMBOL,
                type   = "stop",
                side   = sl_side,
                amount = ALERT_QTY,
                price  = sl_limit,
                params = {
                    "stopPrice"   : new_sl,
                    "reduce_only" : True,
                }
            ))
        except Exception as e:
            logger.error(
                f"SL modify failed (id={self.sl_order_id}): {e} — "
                f"attempting to re-locate SL order"
            )
            # If the order ID is stale (e.g. SL was partially filled),
            # try to find it again
            self.sl_order_id = await self._find_sl_order_id(is_long, new_sl)

    # ── Emergency close (Max SL hit, manual override) ─────────────────
    async def close_position(self, reason: str = "Max SL Hit") -> dict:
        """Market close all — mirrors Pine strategy.close_all()."""
        if not self.position:
            logger.warning("close_position called but no position tracked")
            return {}

        is_long = self.position["is_long"]
        side    = "sell" if is_long else "buy"
        logger.info(f"Emergency close ({reason}) | side={side}")

        order = await _retry(lambda: self.exchange.create_order(
            symbol = SYMBOL,
            type   = "market",
            side   = side,
            amount = ALERT_QTY,
            params = {"reduce_only": True}
        ))

        self.position    = None
        self.sl_order_id = None
        self.tp_order_id = None
        return order

    # ── Fetch current position from exchange ──────────────────────────
    async def fetch_position(self) -> Optional[dict]:
        """
        Fetch live position from Delta Exchange.
        Used to reconcile bot state vs exchange state.
        """
        positions = await _retry(
            lambda: self.exchange.fetch_positions([SYMBOL])
        )
        for pos in positions:
            if pos.get("symbol") == SYMBOL and pos.get("contracts", 0) != 0:
                return pos
        return None

    # ── Fetch last trade for exit price ──────────────────────────────
    async def fetch_last_trade_price(self) -> Optional[float]:
        """
        FIX #2: fetch_my_trades(SYMBOL, limit=1) — first arg is SYMBOL not ALERT_QTY.
        Returns exit price of the most recent closed trade.
        """
        try:
            trades = await _retry(
                lambda: self.exchange.fetch_my_trades(SYMBOL, limit=1)
            )
            if trades:
                return float(trades[-1]["price"])
        except Exception as e:
            logger.warning(f"fetch_last_trade_price failed: {e}")
        return None

    # ── Cleanup ───────────────────────────────────────────────────────
    async def close_exchange(self) -> None:
        await self.exchange.close()
