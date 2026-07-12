import threading
import logging
import json
from datetime import datetime
from queue import Queue
import time

# Conditional imports
try:
    from supabase import create_client
except ImportError:
    pass

logger = logging.getLogger(__name__)

class TelemetryEngine:
    """
    Handles background tracking of anonymous application events.
    Does not block the main OCR or Betting threads.
    """
    _instance = None
    
    def __new__(cls, supabase_client=None):
        if cls._instance is None:
            cls._instance = super(TelemetryEngine, cls).__new__(cls)
            cls._instance.supabase = supabase_client
            cls._instance.event_queue = Queue()
            cls._instance.is_running = True
            
            # Start background worker
            cls._instance._worker_thread = threading.Thread(target=cls._instance._worker_loop, daemon=True)
            cls._instance._worker_thread.start()
            
        elif supabase_client and not cls._instance.supabase:
            cls._instance.supabase = supabase_client
            
        return cls._instance

    def _worker_loop(self):
        """Background thread that consumes events and sends them to Supabase."""
        while self.is_running:
            try:
                # Get events from queue
                batch = []
                while not self.event_queue.empty() and len(batch) < 10:
                    batch.append(self.event_queue.get_nowait())
                
                if batch and self.supabase:
                    try:
                        # Attempt to insert batch
                        self.supabase.table('telemetry_events').insert(batch).execute()
                    except Exception as e:
                        logger.debug(f"Telemetry submission failed: {e}")
                        # Could re-queue here if we wanted strict delivery, but telemetry is best-effort
                        
                time.sleep(5)  # Rest before checking queue again
            except Exception as e:
                time.sleep(5)

    def track_event(self, event_name: str, payload: dict = None, user_id: str = None):
        """Queue an event for tracking."""
        if not payload:
            payload = {}
            
        event_data = {
            "event_type": event_name,
            "payload": payload,
            # "user_id": user_id, # Optional link to specific user 
            "created_at": datetime.utcnow().isoformat()
        }
        self.event_queue.put(event_data)

# Helper function
def track(event_name: str, payload: dict = None, user_id: str = None):
    # Retrieve instance if it exists, otherwise it will just queue anonymously until Supabase is set
    engine = TelemetryEngine()
    engine.track_event(event_name, payload, user_id)
