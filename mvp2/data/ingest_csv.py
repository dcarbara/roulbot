import csv
import os
import sys
import shutil
import time
import argparse
from datetime import datetime, timedelta
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("CSVIngest")

# Path setup to import core modules
current_dir = os.path.dirname(os.path.abspath(__file__))
mvp2_dir = os.path.dirname(current_dir) # parent of data/ is mvp2/
sys.path.append(mvp2_dir)

try:
    from core.utils.db_utils import bulk_upsert_winning_numbers
except ImportError as e:
    logger.error(f"Failed to import db_utils: {e}")
    sys.exit(1)

# Configuration
DATA_DIR = current_dir
INBOX_DIR = os.path.join(DATA_DIR, "inbox")
ARCHIVE_DIR = os.path.join(DATA_DIR, "archive")

def ensure_dirs():
    os.makedirs(INBOX_DIR, exist_ok=True)
    os.makedirs(ARCHIVE_DIR, exist_ok=True)

def process_file(filepath):
    logger.info(f"Processing {filepath}...")
    records = []
    
    try:
        with open(filepath, 'r') as f:
            reader = csv.reader(f)
            rows = list(reader)
            
        # Sort by round ID (col 0) if possible to maintain order
        # Assuming format: ID, Number, Color, Source
        try:
            rows.sort(key=lambda x: int(x[0]))
        except (ValueError, IndexError):
            logger.warning("Could not sort by ID (column 0), processing in order found.")
            
        # Calculate timestamps relative to now since CSV rarely has them
        # Logic matches update_db_today.py: last row = now, previous = now - 1 min
        now = datetime.utcnow()
        total_rows = len(rows)
        
        for i, row in enumerate(reversed(rows)):
            if len(row) < 3:
                continue
                
            try:
                # 1, 29, BLACK, History
                number = int(row[1])
                color = row[2].strip().upper()
                source = row[3].strip() if len(row) > 3 else 'csv_import'
                
                # Timestamp: Start from now and go back 1 min per round
                # If the file has many thousands, this might drift far back, but consistent relative ordering is key.
                timestamp = (now - timedelta(minutes=i)).isoformat()
                
                records.append({
                    'number': number,
                    'color': color,
                    'timestamp': timestamp,
                    'source': source
                })
            except (ValueError, IndexError) as e:
                logger.warning(f"Skipping invalid row in {filepath}: {row} ({e})")
                
        if records:
            inserted = bulk_upsert_winning_numbers(records)
            logger.info(f"✅ Inserted {inserted} new records from {os.path.basename(filepath)} (Skipped {len(records) - inserted} duplicates).")
        else:
            logger.warning("No valid records found in file.")
            
        # Archive file
        filename = os.path.basename(filepath)
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{timestamp_str}_{filename}"
        shutil.move(filepath, os.path.join(ARCHIVE_DIR, archive_name))
        logger.info(f"📁 Archived to {archive_name}")
        
    except Exception as e:
        logger.error(f"❌ Failed to process {filepath}: {e}")

def scan_inbox():
    ensure_dirs()
    files = [f for f in os.listdir(INBOX_DIR) if f.lower().endswith('.csv')]
    if not files:
        return False
        
    for filename in files:
        filepath = os.path.join(INBOX_DIR, filename)
        process_file(filepath)
    return True

def watch_mode(interval=10):
    logger.info(f"👀 Watching {INBOX_DIR} for new CSV files (Ctrl+C to stop)...")
    ensure_dirs()
    try:
        while True:
            scan_inbox()
            time.sleep(interval)
    except KeyboardInterrupt:
        logger.info("Stopping watch mode.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest CSV roulette data.")
    parser.add_argument("--watch", action="store_true", help="Run in watch mode (monitor inbox)")
    args = parser.parse_args()
    
    ensure_dirs()
    
    if args.watch:
        watch_mode()
    else:
        if not scan_inbox():
            print("No CSV files found in 'inbox'.")
