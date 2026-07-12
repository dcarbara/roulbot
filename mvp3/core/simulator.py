# core/simulator.py

import time
import threading
import random
from core.strategy_engine import StrategyEngine

class StrategySimulator:
    def __init__(self, config, on_log=None):
        self.config = config
        self.on_log = on_log or print
        self.engine = StrategyEngine(config["strategy"], config["base_bet"])
        self.coordinates = config.get("coordinates", {})
        self.running = False

    def log(self, message):
        self.on_log(message)

    def simulate_outcome(self):
        # Temporary mock: 48% win chance
        return random.random() < 0.48

    def start(self):
        if self.running:
            return
        self.running = True
        thread = threading.Thread(target=self.run_simulation, daemon=True)
        thread.start()

    def stop(self):
        self.running = False

    def run_simulation(self):
        duration = self.config.get("session_duration_minutes", 15)
        end_time = time.time() + duration * 60

        while self.running and time.time() < end_time:
            bet = self.engine.get_next_bet()
            bet_color = self.config.get("bet_color", "red")
            coord = self.coordinates.get(bet_color)

            if not coord:
                self.log(f"❌ No coordinate found for color '{bet_color}'")
                break

            self.log(f"🎯 Would place ${bet:.2f} on '{bet_color}' at {coord}")

            win = self.simulate_outcome()
            self.log(f"🎲 Result: {'WIN' if win else 'LOSS'}")

            self.engine.record_result(win)

            time.sleep(3)  # wait before next simulated bet

        self.log("✅ Simulation complete.")
        self.running = False
