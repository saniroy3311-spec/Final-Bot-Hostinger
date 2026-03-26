# Shiva Sniper Bot v6.5

Python bot — exact replica of Shiva Sniper Pine Script v6.5 for Delta Exchange India.

## Architecture

```
shiva_sniper_bot/
├── config.py              ← All settings (mirrors Pine inputs)
├── main.py                ← Entry point
├── feed/ws_feed.py        ← WebSocket OHLCV stream
├── indicators/engine.py   ← EMA/ATR/RSI/ADX/DMI (pandas_ta)
├── strategy/signal.py     ← Entry conditions (exact Pine replica)
├── risk/calculator.py     ← SL/TP/trail/BE/maxSL
├── orders/manager.py      ← Delta Exchange via ccxt
├── monitor/trail_loop.py  ← 5-stage trail + BE async loop
├── infra/telegram.py      ← Entry/exit/error alerts
├── infra/journal.py       ← SQLite trade log
├── phase1/                ← Feed + Indicator verification
├── phase2/                ← Signal engine + paper trading
├── phase3/                ← Order manager testnet
├── phase4/                ← Trail monitor live test
├── phase5/                ← Infrastructure test
├── phase6/                ← Live vs TV comparison
├── tests/                 ← Unit + integration tests
└── scripts/               ← Deploy + status scripts
```

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/shiva_sniper_bot
cd shiva_sniper_bot
pip install -r requirements.txt
```

Edit `config.py`:
```python
DELTA_API_KEY    = "your_key"
DELTA_API_SECRET = "your_secret"
DELTA_TESTNET    = True          # Always start on testnet
TELEGRAM_BOT_TOKEN = "your_token"
TELEGRAM_CHAT_ID   = "your_chat_id"
```

## Build Phases (run in order)

### Phase 1 — Verify indicators match TradingView
```bash
python phase1/run_phase1.py
# Then add tv_exporter.pine to TV, export CSV, run:
python phase1/run_phase1.py --tv phase1/data/tv_export.csv
```

### Phase 2 — Paper trade + compare signal bars
```bash
python phase2/run_phase2.py
# With TV comparison:
python phase2/run_phase2.py --tv phase2/data/tv_signals.csv \
    --tv-pl 18347 --tv-trades 1362 --tv-winrate 59.1 --tv-pf 2.94
```

### Phase 3 — Test order manager on testnet
```bash
python phase3/run_phase3.py
```

### Phase 4 — Trail monitor live test
```bash
python phase4/run_phase4.py --bars 20
```

### Phase 5 — Infrastructure test
```bash
python phase5/run_phase5.py
```

### Phase 6 — Live comparison bot vs TradingView
```bash
python phase6/run_phase6.py          # Run bot (testnet)
python phase6/run_phase6.py --compare phase6/data/tv_trades.csv
```

## Run Tests
```bash
pytest tests/ -v
pytest tests/ -v --cov=. --cov-report=term-missing
```

## Deploy to VPS
```bash
bash scripts/deploy.sh ubuntu@your-vps-ip
ssh ubuntu@your-vps-ip "sudo systemctl start shiva_sniper"
ssh ubuntu@your-vps-ip "bash shiva_sniper_bot/scripts/status.sh"
```

## TradingView Pine Scripts
| File | Purpose |
|------|---------|
| `phase1/tv_exporter.pine` | Export indicator values for Phase 1 comparison |
| `phase2/tv_signal_exporter.pine` | Export signal bars for Phase 2 comparison |

## Replication Accuracy vs TradingView

| Component | Match |
|-----------|-------|
| Indicator values | ~100% (pandas_ta matches TV math) |
| Entry bar | ~99% (fires on confirmed bar close) |
| Signal direction | 100% |
| SL/TP levels | ~100% (same ATR formula) |
| Trail ratchet | ~90% (1s loop vs TV tick) |
| Points captured | 90-95% |

## Key Config (mirrors Pine inputs exactly)

```python
# Risk
TREND_RR       = 4.0    # trendRR
RANGE_RR       = 2.5    # rangeRR
TREND_ATR_MULT = 0.6    # trendATRmul
MAX_SL_POINTS  = 500.0  # Hard max SL

# 5-stage trail
TRAIL_STAGES = [
    (0.8,  0.5,  0.4 ),  # Stage 1
    (1.5,  0.4,  0.3 ),  # Stage 2
    (2.5,  0.3,  0.25),  # Stage 3
    (4.0,  0.2,  0.15),  # Stage 4
    (6.0,  0.15, 0.1 ),  # Stage 5
]
```
