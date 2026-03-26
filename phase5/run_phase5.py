"""
phase5/run_phase5.py
Phase 5 - Infrastructure test.
Verifies: Telegram, SQLite journal, systemd service file.

Usage:
    python phase5/run_phase5.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import asyncio

BANNER = """
╔══════════════════════════════════════════════════════════════╗
║       SHIVA SNIPER BOT - PHASE 5: INFRASTRUCTURE            ║
║           Telegram + Journal + systemd                       ║
╚══════════════════════════════════════════════════════════════╝
"""


async def run():
    print(BANNER)
    results = []

    # Test 1: Telegram
    print("TEST 1 - Telegram")
    try:
        from infra.telegram import Telegram
        tg = Telegram()
        await tg.send("Phase 5 test - Shiva Sniper Bot infrastructure check")
        await tg.close()
        print("  PASS - Message sent (check your Telegram)")
        results.append(("Telegram", "PASS"))
    except Exception as e:
        print(f"  FAIL - {e}")
        results.append(("Telegram", "FAIL"))

    # Test 2: SQLite Journal
    print("\nTEST 2 - Journal")
    try:
        from infra.journal import Journal
        j = Journal()
        j.log_trade("Trend Long", True, 50000, 50500, 49700, 51800, 300, 30, 147.5, "TP", 2)
        import sqlite3
        conn = sqlite3.connect("journal.db")
        count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        conn.close()
        j.close()
        print(f"  PASS - {count} trade(s) in journal.db")
        results.append(("Journal", "PASS"))
    except Exception as e:
        print(f"  FAIL - {e}")
        results.append(("Journal", "FAIL"))

    # Test 3: systemd service file
    print("\nTEST 3 - systemd service")
    svc_path = os.path.join(os.path.dirname(__file__), "..", "scripts", "shiva_sniper.service")
    if os.path.exists(svc_path):
        print(f"  PASS - {svc_path}")
        results.append(("systemd service", "PASS"))
    else:
        print(f"  WARN - Not found at {svc_path}")
        results.append(("systemd service", "WARN"))

    # Summary
    print("\n" + "=" * 50)
    for name, status in results:
        icon = "OK" if status in ("PASS", "WARN") else "FAIL"
        print(f"  [{icon}] {name}: {status}")

    passed = all(s != "FAIL" for _, s in results)
    print(f"\n  Phase 5 {'PASSED' if passed else 'FAILED'}")
    if passed:
        print("  Ready for Phase 6 (Live comparison)")


if __name__ == "__main__":
    asyncio.run(run())
