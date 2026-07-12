"""bias_scout.py — per-wheel bias scout for live (physical-dealer) roulette.

The ONLY honest roulette edge is a genuinely biased PHYSICAL wheel (RNG cannot
be biased). Bias is rare on well-maintained online live wheels, so the winning
move is to WATCH MANY wheels cheaply and concentrate capital only on the rare
one that shows a statistically confirmed bias. That is what an automation fleet
is uniquely good at — this layer is the manager for it.

For each distinct physical wheel/table the scout keeps its OWN discounted
Bayesian posterior (DirichletBayesStrategy). It:
  - routes each observed spin to that wheel's posterior,
  - reports, per wheel, whether a pocket/sector provably clears break-even,
  - ranks wheels and picks the single best opportunity (or sits out everywhere),
  - sizes bets with fractional Kelly on the CONSERVATIVE (lower-bound) edge,
  - persists every posterior to disk so spins accumulate across restarts
    (you need thousands of spins per wheel to confirm a small bias),
  - prunes wheels not seen in a while (rotated/maintained out).

Honesty: this does NOT make a fair wheel beatable. On unbiased wheels every
posterior sits out. It pays off only if a real, persistent bias exists; the
discount factor auto-disarms a wheel when maintenance removes its bias.
"""
import json
import os
from typing import Dict, List, Optional

from core.strategies.dirichlet_bayes import DirichletBayesStrategy
from core.profit.sports import kelly_fraction


class BiasScout:
    def __init__(self, strategy_params: Optional[dict] = None,
                 persist_path: Optional[str] = None,
                 stale_after_spins: int = 0):
        """strategy_params: kwargs forwarded to each per-wheel
        DirichletBayesStrategy (gamma, delta, min_neff, min_label_hits, margin,
        targets, ...). persist_path: JSON file to save/load all wheel posteriors.
        stale_after_spins: prune a wheel after this many TOTAL recorded spins
        elapse without seeing it (0 = never auto-prune)."""
        self.strategy_params = dict(strategy_params or {})
        self.persist_path = persist_path
        self.stale_after_spins = int(stale_after_spins)
        self.wheels: Dict[str, DirichletBayesStrategy] = {}
        self.meta: Dict[str, dict] = {}     # wheel_id -> {spins, last_global_idx}
        self._global_idx = 0                # monotonically-increasing spin counter
        if persist_path and os.path.exists(persist_path):
            try:
                self.load()
            except Exception:
                pass

    def _new_strategy(self) -> DirichletBayesStrategy:
        return DirichletBayesStrategy(base_bet=1.0, **self.strategy_params)

    # ----- ingest -----
    def record(self, wheel_id: str, winning_number: int) -> None:
        """Feed one observed spin (the winning number) for a physical wheel."""
        wid = str(wheel_id)
        if wid not in self.wheels:
            self.wheels[wid] = self._new_strategy()
            self.meta[wid] = {"spins": 0, "last_global_idx": self._global_idx}
        self.wheels[wid].record_result(False, last_number=winning_number)
        self._global_idx += 1
        m = self.meta[wid]
        m["spins"] += 1
        m["last_global_idx"] = self._global_idx
        if self.stale_after_spins:
            self.prune_stale()

    # ----- assess -----
    def assess(self, wheel_id: str) -> List[dict]:
        """The +EV labels for one wheel (empty = sit out)."""
        s = self.wheels.get(str(wheel_id))
        return s.armed_bets() if s else []

    def scan(self) -> List[dict]:
        """One summary row per tracked wheel, armed wheels first, best edge first."""
        rows = []
        for wid, s in self.wheels.items():
            bets = s.armed_bets()
            best = bets[0] if bets else None
            rows.append({
                "wheel_id": wid,
                "spins": self.meta.get(wid, {}).get("spins", 0),
                "n_eff": round(s.n_eff, 1),
                "armed": bool(bets),
                "n_armed_labels": len(bets),
                "best_label": best["label"] if best else None,
                "best_edge_lb": round(best["edge_lb"], 4) if best else None,
                "best_mean_prob": round(best["mean_prob"], 5) if best else None,
            })
        rows.sort(key=lambda r: (not r["armed"], -(r["best_edge_lb"] or -9)))
        return rows

    def best_opportunity(self) -> Optional[dict]:
        """The single best confirmed-biased wheel + its bets, or None (sit out
        everywhere). 'Best' = highest worst-case (lower-bound) edge."""
        best = None
        for wid, s in self.wheels.items():
            bets = s.armed_bets()
            if not bets:
                continue
            cand = {"wheel_id": wid, "bets": bets, "top_edge_lb": bets[0]["edge_lb"]}
            if best is None or cand["top_edge_lb"] > best["top_edge_lb"]:
                best = cand
        return best

    # ----- sizing (Kelly on the conservative lower-bound edge) -----
    def recommend_stakes(self, wheel_id: str, bankroll: float,
                         kelly_fraction_mult: float = 0.25,
                         max_total_fraction: float = 0.05,
                         top_k: Optional[int] = None) -> Dict[str, float]:
        """Fractional-Kelly stakes for a confirmed-biased wheel, sized on the
        LOWER-BOUND probability (conservative). Total exposure capped at
        max_total_fraction of bankroll. Returns {label: stake}; empty if the
        wheel isn't armed."""
        bets = self.assess(wheel_id)
        if not bets:
            return {}
        # Greedy NON-OVERLAPPING selection by edge: betting a biased sector AND
        # its member straights double-exposes the same underlying bias, so once
        # a label's pockets are taken, skip any later label that overlaps them.
        chosen: List[dict] = []
        used: set = set()
        for b in bets:
            idxs = set(b.get("idxs") or ())
            if idxs & used:
                continue
            chosen.append(b)
            used |= idxs
        if top_k:
            chosen = chosen[:top_k]
        stakes: Dict[str, float] = {}
        for b in chosen:
            f = kelly_fraction(b["decimal_odds"], b["lb_prob"], kelly_fraction_mult)
            if f > 0:
                stakes[b["label"]] = bankroll * f
        total = sum(stakes.values())
        cap = bankroll * max_total_fraction
        if total > cap and total > 0:
            scale = cap / total
            stakes = {k: v * scale for k, v in stakes.items()}
        return {k: round(v, 4) for k, v in stakes.items()}

    # ----- housekeeping -----
    def prune_stale(self) -> List[str]:
        """Drop wheels not seen within `stale_after_spins` global spins."""
        if not self.stale_after_spins:
            return []
        dropped = []
        for wid in list(self.wheels):
            if self._global_idx - self.meta[wid]["last_global_idx"] > self.stale_after_spins:
                del self.wheels[wid]
                del self.meta[wid]
                dropped.append(wid)
        return dropped

    def report(self) -> str:
        rows = self.scan()
        armed = [r for r in rows if r["armed"]]
        head = (f"BiasScout: {len(self.wheels)} wheels tracked, "
                f"{len(armed)} ARMED, {self._global_idx} spins observed.")
        lines = [head]
        for r in rows[:12]:
            tag = (f"ARMED -> {r['best_label']} (edge {r['best_edge_lb']:+.3f}, "
                   f"p={r['best_mean_prob']})") if r["armed"] else "sitting out"
            lines.append(f"  {r['wheel_id']:<18} spins={r['spins']:<6} "
                         f"N_eff={r['n_eff']:<7} {tag}")
        if not armed:
            lines.append("  -> No confirmed bias anywhere. Sit out (correct).")
        return "\n".join(lines)

    # ----- persistence -----
    def save(self, path: Optional[str] = None) -> None:
        p = path or self.persist_path
        if not p:
            raise ValueError("no persist_path set")
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        blob = {
            "version": 1,
            "strategy_params": self.strategy_params,
            "global_idx": self._global_idx,
            "wheels": {wid: {"state": s.state_dict(), "meta": self.meta[wid]}
                       for wid, s in self.wheels.items()},
        }
        # Atomic write (tmp + os.replace): with many tables saving often, a crash
        # mid-write must never leave a torn JSON that loses every wheel's history.
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(blob, f)
        os.replace(tmp, p)

    def load(self, path: Optional[str] = None) -> None:
        p = path or self.persist_path
        with open(p, "r", encoding="utf-8") as f:
            blob = json.load(f)
        self.strategy_params = dict(blob.get("strategy_params") or self.strategy_params)
        self._global_idx = int(blob.get("global_idx", 0))
        self.wheels = {}
        self.meta = {}
        for wid, w in (blob.get("wheels") or {}).items():
            s = self._new_strategy()
            s.load_state(w.get("state") or {})
            self.wheels[wid] = s
            self.meta[wid] = dict(w.get("meta") or {"spins": 0, "last_global_idx": 0})
