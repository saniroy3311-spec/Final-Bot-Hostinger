"""
config.py - Shiva Sniper v6.5 Python Bot
Secrets loaded from environment variables (Render / VPS).
Set in Render dashboard -> Environment, or export locally.
"""
import os

# ======================
# DELTA EXCHANGE CONFIG
# ======================
DELTA_API_KEY    = os.environ.get("DELTA_API_KEY",    "YOUR_API_KEY")
DELTA_API_SECRET = os.environ.get("DELTA_API_SECRET", "YOUR_API_SECRET")
DELTA_TESTNET    = os.environ.get("DELTA_TESTNET", "true").lower() == "true"
SYMBOL           = os.environ.get("SYMBOL", "BTC/USDT:USDT")
ALERT_QTY        = int(os.environ.get("ALERT_QTY", "30"))
STRATEGY_ID      = os.environ.get("STRATEGY_ID", "4e34d08f83a61dfbfa91ad47717b8ed2")

# ======================
# TELEGRAM CONFIG
# ======================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID")

# ======================
# INDICATOR LENGTHS
# ======================
EMA_TREND_LEN  = 200
EMA_FAST_LEN   = 50
ATR_LEN        = 14
DI_LEN         = 14
ADX_SMOOTH     = 14
ADX_EMA        = 5
RSI_LEN        = 14

# ======================
# REGIME THRESHOLDS
# ======================
ADX_TREND_TH = 22
ADX_RANGE_TH = 18

# ======================
# FILTERS
# FIX: All filter params are now env-configurable so you can tune
#      without redeploying code.
#
# FILTER_BODY_MULT: Pine default = 0.5. Restored to match Pine exactly.
#   If Delta 30m candles produce bodies < 0.5*ATR and no signals fire,
#   diagnose the volume filter first (set FILTER_VOL_ENABLED=false) —
#   do NOT lower body_mult as it breaks TV signal parity.
#   Override via Render env var: FILTER_BODY_MULT=0.5
#
# FILTER_VOL_ENABLED: Set to "false" if Delta REST returns 0 or
#   near-zero volume (base-asset unit mismatch), which locks
#   vol_ok=False permanently. Disable to unblock signals while
#   diagnosing the volume issue.
# ======================
FILTER_ATR_MULT    = float(os.environ.get("FILTER_ATR_MULT",    "1.4"))
FILTER_BODY_MULT   = float(os.environ.get("FILTER_BODY_MULT",   "0.5"))  # FIX R1: restored from 0.3 → 0.5 (Pine default)
FILTER_VOL_ENABLED = os.environ.get("FILTER_VOL_ENABLED", "true").lower() == "true"

# ======================
# RISK / REWARD
# ======================
TREND_RR       = 4.0
RANGE_RR       = 2.5
TREND_ATR_MULT = 0.6
RANGE_ATR_MULT = 0.5
MAX_SL_MULT    = 1.5
MAX_SL_POINTS  = 500.0

# ======================
# 5-STAGE TRAIL ENGINE
# ======================
TRAIL_STAGES = [
    (0.8,  0.5,  0.4 ),
    (1.5,  0.4,  0.3 ),
    (2.5,  0.3,  0.25),
    (4.0,  0.2,  0.15),
    (6.0,  0.15, 0.1 ),
]

# ======================
# BREAKEVEN + RSI
# ======================
BE_MULT = 0.6
RSI_OB  = 70
RSI_OS  = 30

# ======================
# COMMISSION
# ======================
COMMISSION_PCT = 0.05 / 100

# Bracket SL limit-order slippage buffer in price points.
# limit price = SL - buffer (long) / SL + buffer (short).
# Increase if getting frequent limit-miss fills on volatile moves.
BRACKET_SL_BUFFER = float(os.environ.get("BRACKET_SL_BUFFER", "10.0"))

# ======================
# BOT BEHAVIOUR
# ======================
CANDLE_TIMEFRAME = os.environ.get("CANDLE_TIMEFRAME", "30m")
TRAIL_LOOP_SEC   = float(os.environ.get("TRAIL_LOOP_SEC", "1.0"))
WS_RECONNECT_SEC = 5
LOG_FILE         = os.environ.get("LOG_FILE", "/app/journal.db")
