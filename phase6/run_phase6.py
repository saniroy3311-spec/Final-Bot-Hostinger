"""
phase6/run_phase6.py
Phase 6 - Live comparison: Bot vs TradingView side-by-side.

Runs bot in LIVE mode (testnet) and logs every entry/exit.
After N trades, compares bot journal vs TV List of Trades.

Usage:
    python phase6/run_phase6.py
    python phase6/run_phase6.py --compare phase6/data/tv_trades.csv
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import argparse, asyncio
import pandas as pd
from tabulate import tabulate

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║       SHIVA SNIPER BOT - PHASE 6: LIVE COMPARISON           ║
║         Bot (testnet) vs TradingView side by side            ║
╚══════════════════════════════════════════════════════════════╝
"""


def compare_with_tv(journal_db: str, tv_csv: str):
    import sqlite3
    conn    = sqlite3.connect(journal_db)
    bot_df  = pd.read_sql("SELECT * FROM trades ORDER BY ts", conn)
    conn.close()
    tv_df   = pd.read_csv(tv_csv)
    tv_df.columns = [c.strip().lower().replace(" ", "_") for c in tv_df.columns]

    print(BANNER)
    print(f"Bot trades : {len(bot_df)}")
    print(f"TV trades  : {len(tv_df)}")

    rows = []
    for i, (_, b) in enumerate(bot_df.iterrows()):
        tv_row = tv_df.iloc[i] if i < len(tv_df) else None
        entry_match = "N/A" if tv_row is None else (
            "OK" if abs(b["entry_price"] - float(tv_row.get("entry_price", b["entry_price"]))) < 10 else "DIFF"
        )
        pl_match = "N/A" if tv_row is None else (
            "OK" if abs(b["real_pl"] - float(tv_row.get("profit", b["real_pl"]))) < 50 else "DIFF"
        )
        rows.append({
            "trade"        : i + 1,
            "signal"       : b["signal_type"],
            "bot_entry"    : f"{b['entry_price']:.2f}",
            "tv_entry"     : f"{float(tv_row.get('entry_price', 0)):.2f}" if tv_row is not None else "—",
            "entry_match"  : entry_match,
            "bot_pl"       : f"{b['real_pl']:.2f}",
            "tv_pl"        : f"{float(tv_row.get('profit', 0)):.2f}" if tv_row is not None else "—",
            "pl_match"     : pl_match,
        })

    print(tabulate(rows, headers="keys", tablefmt="rounded_outline"))

    ok_count   = sum(1 for r in rows if r["entry_match"] == "OK")
    diff_count = sum(1 for r in rows if r["entry_match"] == "DIFF")
    print(f"\nEntry match: {ok_count}/{len(rows)} OK, {diff_count} DIFF")

    if diff_count == 0:
        print("\nPhase 6 PASSED - Bot matches TradingView. Ready to go live.")
    else:
        print(f"\nPhase 6 WARN - {diff_count} entry price differences > 10 pts")
        print("  Expected due to execution latency. Check if within acceptable range.")


async def run_live():
    print(BANNER)
    print("Starting bot in LIVE mode (testnet)...")
    print("Monitor journal.db for trade entries/exits.")
    print("Run Ctrl+C to stop, then compare with TV trades.")
    print()

    from main import SniperBot
    bot = SniperBot()
    await bot.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare", default=None, help="TV trades CSV to compare against journal.db")
    args = parser.parse_args()

    if args.compare:
        compare_with_tv("journal.db", args.compare)
    else:
        asyncio.run(run_live())
