"""
feed/ws_feed.py
OHLCV feed for Delta Exchange via REST polling.

BUGS FIXED:
  BUG 1 (CRITICAL) — MIN_BARS too high:
    Delta returns 255-258 bars but MIN_BARS was 260.
    len(df) >= 260 was ALWAYS False → on_bar_close() NEVER called
    → zero signals fired ever. Fixed: MIN_BARS = EMA_TREND_LEN + 10 (210).

  BUG 2 — Poll interval 45 seconds:
    Bot was sleeping 45 seconds between polls.
    Entry was 45 seconds late after bar close.
    Fixed: all timeframes now poll every 5 seconds.

  BUG 3 — Bar confirmation logged at DEBUG level:
    logger.debug("Bar confirmed") was invisible in Render logs
    because logging level is INFO. Fixed: changed to logger.info.

  BUG 4 — Startup fetch requests same limit as MIN_BARS:
    If MIN_BARS = 210, startup fetches exactly 210 bars.
    Delta sometimes returns fewer. Added buffer: fetch MIN_BARS + 50.
"""

import asyncio
import logging
import pandas as pd
import ccxt
from config import (
    DELTA_API_KEY, DELTA_API_SECRET, DELTA_TESTNET,
    SYMBOL, CANDLE_TIMEFRAME, WS_RECONNECT_SEC, EMA_TREND_LEN,
)

logger   = logging.getLogger(__name__)
MIN_BARS = EMA_TREND_LEN + 10   # BUG 1 FIX: was +60 (260), now +10 (210)


class CandleFeed:
    def __init__(self, on_bar_close, on_feed_ready=None):
        self.on_bar_close   = on_bar_close
        async def _noop(): pass
        self.on_feed_ready  = on_feed_ready or _noop
        self._last_bar_ts   = 0
        self._df            = pd.DataFrame()
        self._exchange      = None
        self._ready_fired   = False

    async def start(self) -> None:
        """Start polling loop with auto-reconnect."""
        while True:
            try:
                await self._connect()
                await self._poll()
            except Exception as e:
                logger.error(f"Feed error: {e}", exc_info=True)
                await asyncio.sleep(WS_RECONNECT_SEC)

    async def _connect(self) -> None:
        """Init REST exchange and load historical bars."""
        params = {
            "apiKey"         : DELTA_API_KEY,
            "secret"         : DELTA_API_SECRET,
            "enableRateLimit": True,
        }
        self._exchange = ccxt.delta(params)
        if DELTA_TESTNET:
            self._exchange.set_sandbox_mode(True)

        # BUG 4 FIX: fetch MIN_BARS + 50 to ensure we always get enough bars
        fetch_limit = MIN_BARS + 50
        logger.info(f"Loading {fetch_limit} historical bars via REST for [{SYMBOL}]...")
        ohlcv = self._exchange.fetch_ohlcv(
            SYMBOL, CANDLE_TIMEFRAME, limit=fetch_limit
        )
        self._df          = self._to_df(ohlcv)
        self._last_bar_ts = int(self._df.iloc[-1]["timestamp"])
        logger.info(
            f"Feed ready - {len(self._df)} bars loaded "
            f"(need {MIN_BARS}, have {len(self._df)} — "
            f"{'OK ✅' if len(self._df) >= MIN_BARS else 'WARN ⚠️ not enough bars'})"
        )

        if not self._ready_fired:
            self._ready_fired = True
            await self.on_feed_ready()

    async def _poll(self) -> None:
        """Poll every 5 seconds — minimises entry delay after bar close."""
        # BUG 2 FIX: was 45s for 30m, now 5s for all timeframes
        intervals = {
            "1m" : 2,
            "3m" : 3,
            "5m" : 4,
            "15m": 5,
            "30m": 5,
            "1h" : 5,
            "4h" : 5,
        }
        sleep_sec = intervals.get(CANDLE_TIMEFRAME, 5)
        logger.info(f"Polling every {sleep_sec}s for {CANDLE_TIMEFRAME} candles")

        while True:
            await asyncio.sleep(sleep_sec)
            try:
                ohlcv = self._exchange.fetch_ohlcv(
                    SYMBOL, CANDLE_TIMEFRAME, limit=5
                )
                if not ohlcv:
                    continue

                for candle in ohlcv:
                    ts = int(candle[0])

                    if ts > self._last_bar_ts:
                        if len(self._df) >= MIN_BARS:
                            # BUG 3 FIX: was logger.debug — invisible in Render logs
                            logger.info(
                                f"✅ Bar confirmed | ts={self._last_bar_ts} | "
                                f"bars={len(self._df)} — evaluating signals..."
                            )
                            await self.on_bar_close(self._df.copy())
                        else:
                            logger.warning(
                                f"⚠️ Bar skipped — only {len(self._df)} bars "
                                f"(need {MIN_BARS}). Waiting for more data."
                            )

                        new_row = pd.DataFrame([{
                            "timestamp": ts,
                            "open"     : float(candle[1]),
                            "high"     : float(candle[2]),
                            "low"      : float(candle[3]),
                            "close"    : float(candle[4]),
                            "volume"   : float(candle[5]),
                        }])
                        self._df = pd.concat(
                            [self._df, new_row], ignore_index=True
                        ).tail(MIN_BARS + 50)
                        self._last_bar_ts = ts

                    else:
                        if not self._df.empty:
                            last_idx = self._df.index[-1]
                            self._df.loc[last_idx, "timestamp"] = ts
                            self._df.loc[last_idx, "open"]      = float(candle[1])
                            self._df.loc[last_idx, "high"]      = float(candle[2])
                            self._df.loc[last_idx, "low"]       = float(candle[3])
                            self._df.loc[last_idx, "close"]     = float(candle[4])
                            self._df.loc[last_idx, "volume"]    = float(candle[5])

            except ccxt.NetworkError as e:
                logger.warning(f"Network error: {e} - retrying...")
                await asyncio.sleep(WS_RECONNECT_SEC)
            except Exception as e:
                logger.error(f"Poll error: {e}", exc_info=True)
                break

    @staticmethod
    def _to_df(ohlcv: list) -> pd.DataFrame:
        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        return df.astype({
            "open": float, "high": float,
            "low": float, "close": float, "volume": float,
        })
