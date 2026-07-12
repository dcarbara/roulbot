import csv
import sqlite3
import os
import sys
from datetime import datetime, timedelta

# Ensure we can find core.utils.db_utils
# This script is located in engine/mvp2/
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.utils.db_utils import DB_PATH, init_db

def update_db():
    print(f"Target Database: {DB_PATH}")
    print("Initializing/Verifying DB schema...")
    init_db()
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    print("WARNING: Clearing ALL existing data in 'winning_numbers'...")
    c.execute("DELETE FROM winning_numbers")
    # Optional: Clear stats too if you want a totally fresh start
    # c.execute("DELETE FROM session_stats") 
    conn.commit()
    print("Database cleared.")
    
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'today.csv')
    print(f"Reading data from {csv_path}...")
    
    if not os.path.exists(csv_path):
        print(f"Error: File not found at {csv_path}")
        return

    with open(csv_path, 'r') as f:
        reader = csv.reader(f)
        rows = list(reader)
        
    print(f"Found {len(rows)} rows in CSV.")
    
    # Sort by the first column (Round ID) to ensure chronological order before assigning timestamps
    # Assuming col 0 is round index, 29, BLACK, History
    # 1,29,BLACK,History
    try:
        rows.sort(key=lambda x: int(x[0]))
    except ValueError as e:
        print(f"Error sorting rows: {e}. proceed with caution.")
    
    now = datetime.utcnow()
    
    print("Inserting new data...")
    count = 0
    
    # Logic: The last row in the sorted list is the MOST RECENT round.
    # We assign 'now' to the last row, and 'now - 1 min' to the one before it, etc.
    # so we iterate REVERSED.
    
    for i, row in enumerate(reversed(rows)):
        if len(row) < 3:
            continue
            
        try:
            # File format: Index, Number, Color, Source
            # Example: 1, 29, BLACK, History
            number = int(row[1])
            color = row[2].strip().upper()
            source = row[3].strip() if len(row) > 3 else 'imported'
            
            # Timestamp generation
            # i=0 (latest round) -> now
            # i=1 -> now - 1 min
            timestamp = (now - timedelta(minutes=i)).isoformat()
            
            c.execute('''
                INSERT INTO winning_numbers (number, color, timestamp, source)
                VALUES (?, ?, ?, ?)
            ''', (number, color, timestamp, source))
            count += 1
            
        except (ValueError, IndexError) as e:
            print(f"Skipping invalid row {row}: {e}")
            
    conn.commit()
    conn.close()
    print(f"Successfully inserted {count} records.")

if __name__ == "__main__":
    update_db()
