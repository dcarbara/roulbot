import sqlite3
import os
import sys
import shutil
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

# Determine base path for persistent storage
if getattr(sys, 'frozen', False):
    # Application is frozen (running as exe)
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Running as script -> base is src/mvp2
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_NAME = "winning_numbers.db"
DB_PATH = os.path.join(BASE_DIR, DB_NAME)

def get_bundled_db_path():
    """Return path to DB inside the bundle if frozen"""
    if getattr(sys, 'frozen', False):
        if hasattr(sys, '_MEIPASS'):
            return os.path.join(sys._MEIPASS, DB_NAME)
        else:
            return os.path.join(os.path.dirname(sys.executable), "_internal", DB_NAME)
    return None

def ensure_db_exists():
    """Ensure DB exists at writable location, copying from bundle if needed."""
    if not os.path.exists(DB_PATH):
        bundled_path = get_bundled_db_path()
        if bundled_path and os.path.exists(bundled_path):
            try:
                logger.info(f"Extracting bundled database to {DB_PATH}")
                shutil.copy2(bundled_path, DB_PATH)
            except Exception as e:
                logger.error(f"Failed to extract bundled DB: {e}")

def get_db_connection():
    ensure_db_exists()
    conn = sqlite3.connect(DB_PATH)
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS winning_numbers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            number INTEGER NOT NULL,
            color TEXT,
            timestamp TEXT NOT NULL,
            source TEXT
        )
    ''')
    # Add session_stats table
    c.execute('''
        CREATE TABLE IF NOT EXISTS session_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            strategy TEXT,
            rounds_played INTEGER,
            wins INTEGER,
            losses INTEGER,
            profit REAL,
            max_win_streak INTEGER DEFAULT 0,
            max_loss_streak INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()

def save_session_stats(start_time, end_time, strategy, rounds_played, wins, losses, profit, max_win_streak=0, max_loss_streak=0):
    """Save session statistics to database"""
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO session_stats (start_time, end_time, strategy, rounds_played, wins, losses, profit, max_win_streak, max_loss_streak)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (start_time, end_time, strategy, rounds_played, wins, losses, profit, max_win_streak, max_loss_streak))
    conn.commit()
    conn.close()

def get_aggregate_stats():
    """Get aggregate statistics from all sessions"""
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    
    # Get total sessions
    c.execute('SELECT COUNT(*) FROM session_stats')
    total_sessions = c.fetchone()[0]
    
    # Get total rounds, wins, losses, profit
    c.execute('''
        SELECT 
            SUM(rounds_played) as total_rounds,
            SUM(wins) as total_wins,
            SUM(losses) as total_losses,
            SUM(profit) as total_profit,
            MAX(profit) as best_session_profit
        FROM session_stats
    ''')
    result = c.fetchone()
    
    conn.close()
    
    return {
        'total_sessions': total_sessions,
        'total_rounds': result[0] or 0,
        'total_wins': result[1] or 0,
        'total_losses': result[2] or 0,
        'total_profit': result[3] or 0.0,
        'best_session_profit': result[4] or 0.0
    }

def save_winning_number(number, color, source='table_state'):
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        INSERT INTO winning_numbers (number, color, timestamp, source)
        VALUES (?, ?, ?, ?)
    ''', (number, color, datetime.utcnow().isoformat(), source))
    conn.commit()
    conn.close()

def get_recent_winning_numbers(limit=20, max_id=None):
    """Return the most recent N rows from winning_numbers.

    Ordering tie-break: when two rows share a timestamp (common when the
    watcher batches inserts within the same millisecond), the SQL engine
    used to return them in arbitrary order — making backtests non-repeatable
    even on the same DB. We now sort by (timestamp DESC, id DESC) so
    ordering is stable across queries.

    `max_id` (optional): if provided, only rows with id <= max_id are
    returned. Lets the GUI pin a backtest to a specific DB snapshot so
    re-running on the same configuration produces the same data window
    even after the watcher thread has appended new spins.
    """
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    if max_id is not None:
        c.execute('''
            SELECT number, color, timestamp, source, id
            FROM winning_numbers
            WHERE id <= ?
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        ''', (int(max_id), limit))
    else:
        c.execute('''
            SELECT number, color, timestamp, source, id
            FROM winning_numbers
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
        ''', (limit,))
    rows = c.fetchall()
    conn.close()
    return [
        {
            'number': row[0],
            'color': row[1],
            'timestamp': row[2],
            'source': row[3],
            'id':     row[4],
        } for row in rows
    ]


def get_max_winning_number_id():
    """Return the current MAX(id) from winning_numbers — the anchor a
    'lock slice' UI captures so subsequent runs replay the same window."""
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COALESCE(MAX(id), 0) FROM winning_numbers")
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row else 0

def get_recent_sessions(limit=50):
    """Fetch recent session statistics"""
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        SELECT start_time, rounds_played, profit, wins, losses, strategy
        FROM session_stats
        ORDER BY start_time DESC
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    
    sessions = []
    for row in rows:
        sessions.append({
            'start_time': row[0],
            'rounds': row[1],
            'profit': row[2],
            'wins': row[3],
            'losses': row[4],
            'strategy': row[5]
        })
    return sessions

def get_bankroll_trend(limit=50):
    """
    Fetch cumulative profit trend from sessions.
    Since we don't store absolute bankroll, we verify the trend based on cumulative session profits.
    """
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    # Ascending order to build cumulative trend
    c.execute('''
        SELECT start_time, profit
        FROM session_stats
        ORDER BY start_time ASC
        LIMIT ?
    ''', (limit,))
    rows = c.fetchall()
    conn.close()
    
    dates = []
    cumulative_profits = []
    running_total = 0.0
    
    for row in rows:
        # Simplify date format to just HH:MM or DD/MM
        try:
            dt = datetime.fromisoformat(row[0])
            dates.append(dt.strftime("%d-%b %H:%M")) # e.g. 29-Dec 03:45
        except Exception:
            dates.append(row[0]) # Fallback
            
        running_total += row[1]
        cumulative_profits.append(running_total)
        
    return dates, cumulative_profits

def clear_all_statistics():
    """Clear all session stats and winning numbers from DB"""
    init_db()
    conn = get_db_connection()
    try:
        c = conn.cursor()
        c.execute('DELETE FROM session_stats')
        c.execute('DELETE FROM winning_numbers')
        conn.commit() # Commit deletion first
        
        # VACUUM must run outside a transaction
        # Setting isolation_level to None puts connection in autocommit mode
        old_isolation = conn.isolation_level
        conn.isolation_level = None 
        c.execute('VACUUM')
        conn.isolation_level = old_isolation # Restore (optional but good practice)
        
    except Exception as e:
        print(f"Error clearing DB: {e}")
        # If vacuum fails, at least ensure deletions are committed via the earlier commit
    finally:
        conn.close()

# Wheel Sectors
SECTORS = {
    "Voisins": [22, 18, 29, 7, 28, 12, 35, 3, 26, 0, 32, 15, 19, 4, 21, 2, 25],
    "Tiers": [27, 13, 36, 11, 30, 8, 23, 10, 5, 24, 16, 33],
    "Orphelins": [17, 34, 6, 1, 20, 14, 31, 9]
}

def get_spins_for_bias(limit: int = None, source: str = None) -> list:
    """
    Fetch spin history as a flat list of integers for bias analysis.
    Returns oldest-first ordering.
    """
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    query = 'SELECT number FROM winning_numbers'
    params = []
    if source:
        query += ' WHERE source = ?'
        params.append(source)
    query += ' ORDER BY timestamp ASC'
    if limit:
        query += ' LIMIT ?'
        params.append(limit)
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_number_frequency(limit=100):
    """
    Get frequency of each number in the last N rounds.
    Returns sorted list of (number, count, last_seen_ago)
    """
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    
    # Get recent numbers
    c.execute('SELECT number, timestamp FROM winning_numbers ORDER BY timestamp DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    
    if not rows:
        return []
        
    counts = {}
    last_seen = {}
    
    for idx, (num, timestamp) in enumerate(rows):
        counts[num] = counts.get(num, 0) + 1
        if num not in last_seen:
            last_seen[num] = idx # rounds ago (0 = most recent)
            
    # Fill in zeros for numbers that haven't hit
    for i in range(37):
        if i not in counts:
            counts[i] = 0
            last_seen[i] = limit # "At least limit ago"
            
    # Convert to list
    stats = []
    for num in range(37):
        stats.append({
            'number': num,
            'count': counts[num],
            'percentage': (counts[num] / len(rows)) * 100 if rows else 0,
            'last_seen': last_seen[num]
        })
        
    # Sort by count desc (Hot), then last_seen asc
    stats.sort(key=lambda x: (-x['count'], x['last_seen']))
    
    return stats

def get_sector_stats(limit=100):
    """Get hit percentages by wheel sector"""
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT number FROM winning_numbers ORDER BY timestamp DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()
    
    sector_counts = {"Voisins": 0, "Tiers": 0, "Orphelins": 0}
    total = 0
    
    for (num,) in rows:
        total += 1
        for sector, numbers in SECTORS.items():
            if num in numbers:
                sector_counts[sector] += 1
                break
                
    return {k: (v / total * 100 if total > 0 else 0) for k, v in sector_counts.items()}

def get_gap_stats(limit=200):
    """
    Calculate the 'gap' (rounds since last hit) for various targets.
    Returns a dict with gaps for numbers, colors, dozens, columns, and sectors.
    """
    init_db()
    conn = get_db_connection()
    c = conn.cursor()
    # Fetch recent numbers, efficiently.
    c.execute('SELECT number, color FROM winning_numbers ORDER BY timestamp DESC LIMIT ?', (limit,))
    rows = c.fetchall()
    conn.close()

    gaps = {
        "numbers": {i: limit for i in range(37)},
        "colors": {"red": limit, "black": limit, "green": limit},
        "dozens": {"1st12": limit, "2nd12": limit, "3rd12": limit},
        "columns": {"col1": limit, "col2": limit, "col3": limit},
        "sectors": {"Voisins": limit, "Tiers": limit, "Orphelins": limit},
        "even_odd": {"even": limit, "odd": limit},
        "high_low": {"1to18": limit, "19to36": limit}
    }

    if not rows:
        return gaps

    # Helper to update gap if not already found
    def update_gap(category, key, current_gap):
        if gaps[category].get(key) == limit: # Only update if it's the first time we see it (since we iterate backwards in time)
            gaps[category][key] = current_gap

    for idx, (number, color) in enumerate(rows):
        # Number Gap
        if gaps["numbers"][number] == limit:
            gaps["numbers"][number] = idx

        # Color Gap
        # Assuming db stores "red", "black". Green is 0.
        c_key = color.lower() if color else "green" 
        if number == 0: c_key = "green" # Safety
        update_gap("colors", c_key, idx)

        # Dozens
        if 1 <= number <= 12: update_gap("dozens", "1st12", idx)
        elif 13 <= number <= 24: update_gap("dozens", "2nd12", idx)
        elif 25 <= number <= 36: update_gap("dozens", "3rd12", idx)

        # Columns
        if number in [1, 4, 7, 10, 13, 16, 19, 22, 25, 28, 31, 34]: update_gap("columns", "col1", idx)
        elif number in [2, 5, 8, 11, 14, 17, 20, 23, 26, 29, 32, 35]: update_gap("columns", "col2", idx)
        elif number in [3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36]: update_gap("columns", "col3", idx)

        # Sectors
        for sector, nums in SECTORS.items():
            if number in nums:
                update_gap("sectors", sector, idx)
        
        # Even/Odd
        if number != 0:
            if number % 2 == 0: update_gap("even_odd", "even", idx)
            else: update_gap("even_odd", "odd", idx)

        # High/Low
        if 1 <= number <= 18: update_gap("high_low", "1to18", idx)
        elif 19 <= number <= 36: update_gap("high_low", "19to36", idx)

    return gaps 