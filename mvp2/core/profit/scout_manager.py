"""scout_manager.py — GUI/bot-facing wrapper around BiasScout.

Owns a single PERSISTENT BiasScout for the running bot: loads accumulated
per-wheel posteriors on startup, ingests every observed winning number tagged by
the table being played, auto-saves so spins survive restarts (you need thousands
per wheel), and exposes a human report + a Kelly-sized deployment recommendation
when a wheel's bias is statistically confirmed.

The bot wires three calls:
  - set_table(table_id)      when the user selects / the bot identifies a table
  - on_spin(winning_number)  on every captured result in the live loop
  - report() / recommend()   for the HUD / Telegram / a deploy decision

It deliberately does NOT auto-place bets — a detected bias is surfaced as a
recommendation for the operator to confirm, because deploying capital on a
detected edge is a deliberate decision, not something to fire blind.
"""
import os
import threading
from typing import Optional

from core.profit.bias_scout import BiasScout


def default_persist_path() -> str:
    return os.path.join(os.path.expanduser("~"), ".spinedge", "bias_scout", "scout.json")


# Reasonable defaults: gamma=0.9995 -> effective memory ~2000 spins (enough to
# confirm a strong/persistent bias; weaker biases need a higher gamma + more
# spins — see the sample-size table). Strict gate (delta + bonferroni) so it
# never fires on noise.
DEFAULT_PARAMS = dict(gamma=0.9995, delta=0.05, min_neff=500, min_label_hits=15,
                      top_k=3, margin=0.0, targets="both", bonferroni=True)


class BiasScoutManager:
    def __init__(self, persist_path: Optional[str] = None,
                 strategy_params: Optional[dict] = None,
                 save_every: int = 100, table_id: str = "default",
                 enabled: bool = True):
        self.persist_path = persist_path or default_persist_path()
        params = dict(DEFAULT_PARAMS)
        params.update(strategy_params or {})
        self.scout = BiasScout(strategy_params=params, persist_path=self.persist_path)
        self.save_every = max(1, int(save_every))
        self.table_id = str(table_id or "default")
        self.enabled = bool(enabled)
        self._since_save = 0
        # Serializes ingest from N concurrent table feeds — record() mutates
        # shared dicts and save() rewrites the whole file.
        self._lock = threading.Lock()

    # ----- config -----
    def set_table(self, table_id: str) -> None:
        """Tag subsequent spins with this physical table/wheel identity."""
        if table_id:
            self.table_id = str(table_id)

    def set_enabled(self, on: bool) -> None:
        self.enabled = bool(on)

    # ----- ingest -----
    def on_spin(self, winning_number, table_id: Optional[str] = None) -> None:
        """Feed one captured winning number into the current (or given) wheel."""
        if not self.enabled or winning_number is None:
            return
        try:
            n = int(winning_number)
        except (TypeError, ValueError):
            return
        if not (0 <= n <= 36):
            return
        # Pass table_id EXPLICITLY for multi-table feeds so concurrent wheels
        # never cross-contaminate each other's posterior.
        with self._lock:
            self.scout.record(table_id or self.table_id, n)
            self._since_save += 1
            due = self._since_save >= self.save_every
            if due:
                try:
                    self.scout.save()
                    self._since_save = 0
                except Exception:
                    pass

    def flush(self) -> None:
        try:
            self.scout.save()
            self._since_save = 0
        except Exception:
            pass

    # ----- read -----
    def report(self) -> str:
        return self.scout.report()

    def opportunity(self):
        """The best confirmed-biased wheel (or None)."""
        return self.scout.best_opportunity()

    def recommend(self, bankroll: float, **kw) -> Optional[dict]:
        """If any wheel is armed, the Kelly-sized deploy recommendation:
        {wheel_id, stakes, top_edge_lb}. Else None (sit out everywhere)."""
        opp = self.scout.best_opportunity()
        if not opp:
            return None
        stakes = self.scout.recommend_stakes(opp["wheel_id"], bankroll, **kw)
        return {"wheel_id": opp["wheel_id"], "stakes": stakes,
                "top_edge_lb": opp["top_edge_lb"], "bets": opp["bets"]}

    def alert_line(self, bankroll: float = 0.0) -> Optional[str]:
        """One-line alert for HUD/Telegram when a wheel is confirmed biased."""
        opp = self.scout.best_opportunity()
        if not opp:
            return None
        b = opp["bets"][0]
        msg = (f"🎯 BIAS CONFIRMED on wheel '{opp['wheel_id']}': bet {b['label']} "
               f"(edge {b['edge_lb']:+.0%} worst-case, p={b['mean_prob']:.3f})")
        if bankroll > 0:
            stakes = self.scout.recommend_stakes(opp["wheel_id"], bankroll)
            if stakes:
                msg += " | stakes: " + ", ".join(f"{k}=${v:.2f}" for k, v in stakes.items())
        return msg
