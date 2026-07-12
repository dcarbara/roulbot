import csv
import sys
import os
import sqlite3
from datetime import datetime, timedelta

# Ensure we can find core.utils.db_utils
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.utils.db_utils import DB_PATH, init_db

def import_history(csv_path):
    print(f"Reading from {csv_path}")
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    print("Cleaning existing winning_numbers...")
    c.execute("DELETE FROM winning_numbers")
    # c.execute("DELETE FROM session_stats") # Optional: Uncomment to clear stats too
    conn.commit()
    
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        
    print(f"Found {len(rows)} rows.")
    
    # Sort rows by Round if needed, assuming Round 1 is oldest.
    # The user input has Round 1 at top. 
    # If Round 487 is the Latest, then timestamp for 487 should be NOW.
    # Timestamp for 1 should be NOW - 487 * 1 min.
    
    # Check if 'Round' column exists and is numeric
    try:
        rows.sort(key=lambda x: int(x['Round']))
    except ValueError:
        print("Error parsing Round numbers. Assuming order in file is correct (oldest first).")

    # Latest round (last in sorted list) is now.
    now = datetime.utcnow()
    
    inserted_count = 0
    for i, row in enumerate(reversed(rows)):
        # reversed means we start from the latest round (e.g. 487) back to 1
        # i=0 -> round 487 -> time = now
        # i=1 -> round 486 -> time = now - 1 min
        
        number = int(row['Number'])
        color = row['Color'].strip().upper()
        # Fix potential "History" or empty stuff
        
        timestamp = (now - timedelta(minutes=i)).isoformat()
        
        # Check if already exists (optional, but good for idempotency if running multiple times)
        # For now, we just insert.
        
        c.execute('''
            INSERT INTO winning_numbers (number, color, timestamp, source)
            VALUES (?, ?, ?, ?)
        ''', (number, color, timestamp, 'history_import'))
        inserted_count += 1

    conn.commit()
    conn.close()
    print(f"Successfully inserted {inserted_count} records into {DB_PATH}")

if __name__ == "__main__":
    csv_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history_data.csv")
    import_history(csv_file)
