#!/bin/bash
# Check bot status, last 50 log lines, open position
echo "=== SERVICE STATUS ==="
sudo systemctl status shiva_sniper --no-pager
echo ""
echo "=== LAST 50 LOG LINES ==="
sudo journalctl -u shiva_sniper -n 50 --no-pager
echo ""
echo "=== TRADE JOURNAL (last 5) ==="
sqlite3 journal.db "SELECT ts, signal_type, entry_price, exit_price, real_pl, exit_reason FROM trades ORDER BY id DESC LIMIT 5;" 2>/dev/null || echo "No trades yet"
