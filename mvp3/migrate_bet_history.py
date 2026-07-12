"""
migrate_bet_history.py — one-time import of old bot_history.json into
bet_history.db. Reconstructs session_num and win/loss streaks; flags
implausible (OCR-corrupted) rows as source='migrated_suspect' so they can
be filtered out. Safe to re-run (skips if already migrated).
"""
import json, os, sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
HIST  = os.path.join(_HERE, "bot_history.json")
DB    = os.path.join(_HERE, "bet_history.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS bet_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT, session_num INTEGER, round INTEGER, strategy TEXT,
    number INTEGER, color TEXT, won INTEGER, pnl REAL,
    balance_before REAL, balance_after REAL, base_bet REAL, bet_amount REAL,
    fib_idx INTEGER, fib_mult INTEGER, win_streak INTEGER, loss_streak INTEGER,
    tp REAL, sl REAL, cum_sl REAL, source TEXT)
"""

def main():
    conn = sqlite3.connect(DB)
    conn.execute(SCHEMA)

    already = conn.execute(
        "SELECT COUNT(*) FROM bet_history WHERE source LIKE 'migrated%'").fetchone()[0]
    if already:
        print(f"Already {already} migrated rows present — skipping. "
              f"(DELETE FROM bet_history WHERE source LIKE 'migrated%' to redo.)")
        conn.close(); return

    try:
        data = json.load(open(HIST, encoding="utf-8"))
    except Exception as e:
        print(f"No readable bot_history.json ({e}) — nothing to migrate.")
        conn.close(); return

    session_num, ws, ls, suspect = 1, 0, 0, 0
    for i, e in enumerate(data):
        rnd = e.get("round")
        if rnd == 1 and i > 0:
            session_num += 1; ws = ls = 0
        won = bool(e.get("won"))
        pnl = e.get("pnl", 0.0) or 0.0
        bal = e.get("balance")
        if won: ws += 1; ls = 0
        else:   ls += 1; ws = 0
        bal_before = round(bal - pnl, 2) if bal is not None else None
        src = "migrated_json"
        if (bal is not None and bal > 50000) or abs(pnl) > 5000:
            src = "migrated_suspect"; suspect += 1
        conn.execute(
            "INSERT INTO bet_history (session_num, round, number, color, won, pnl, "
            "balance_before, balance_after, win_streak, loss_streak, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (session_num, rnd, e.get("number"), e.get("color"), 1 if won else 0, pnl,
             bal_before, bal, ws, ls, src))
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM bet_history").fetchone()[0]
    print(f"Migrated {len(data)} rows from bot_history.json "
          f"({suspect} flagged 'migrated_suspect'). bet_history.db now has {total} rows.")
    conn.close()

if __name__ == "__main__":
    main()
