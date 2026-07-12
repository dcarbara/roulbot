"""feed_collector.py — standalone live-result collector (no CDP, no browser).

Connects DIRECTLY to the live-casino provider's result endpoints the way public
trackers (CasinoScores / Tracksino) do — a WebSocket lobby feed and/or an HTTP
statistics endpoint — records every table's spins, and feeds them into the
BiasScout. Runs headless; reconnects on its own.

WHY (vs feed_tap.py / CDP): a given Evolution/Pragmatic physical wheel emits the
SAME result stream for every casino that embeds it, so trackers tap the
PROVIDER feed directly instead of scraping a browser. This module does that.

WHAT YOU CAPTURE ONCE: a provider feed is auth-gated — the endpoint URL, the
auth header/token, and the subscribe frame are private and versioned, so they
must be captured once from a logged-in session (DevTools -> Network -> WS, copy
the connection URL + the first messages) and dropped into the config. After that
this runs standalone. Use `--make-config` to emit an annotated template and
`--replay` to validate a capture offline before going live.

Two source types:
  - "ws":        open a WebSocket, send optional subscribe/keepalive frames,
                 parse every inbound frame.
  - "http_poll": GET a JSON endpoint every N seconds (statisticHistory-style),
                 parse the body. Simplest and most robust — no socket lifecycle.

The provider-agnostic result extraction (table-id / history / many-tables-in-one
-frame / 00-rejection / dedup) is shared with feed_tap.py via extract_results,
which is self-tested there.

This module NEVER places a bet. It only records results and feeds the scout.

CLI
---
    python -m core.profit.feed_collector --selftest
    python -m core.profit.feed_collector --make-config feed_endpoints.json
    python -m core.profit.feed_collector --replay capture.jsonl          # offline verify
    python -m core.profit.feed_collector --config feed_endpoints.json --probe evolution-lobby
    python -m core.profit.feed_collector --config feed_endpoints.json --run
"""
import argparse
import json
import os
import threading
import time
from typing import Callable, Dict, List, Optional, Tuple

from core.profit.feed_tap import extract_results, _table_id_from_url


# ── annotated example config ───────────────────────────────────────────────────
EXAMPLE_CONFIG = {
    "_comment": "Capture each source ONCE from DevTools -> Network. For a WS: "
                "copy the wss:// URL, the request headers (Cookie/Origin/"
                "User-Agent), and the first message(s) the page sends after "
                "connect (those go in 'subscribe'). For http_poll: copy the "
                "statistics request URL + headers. Then this runs standalone.",
    "persist": "",  # blank = default scout (~/.spinedge/bias_scout/scout.json)
    "save_every": 100,
    "sources": [
        {
            "name": "evolution-lobby",
            "type": "ws",
            "provider": "auto",          # or "reverse" if history is oldest-first
            "enabled": False,            # flip true once filled from a capture
            "url": "wss://REPLACE.evo-games.com/public/lobby/...",
            "headers": {
                "Origin": "https://REPLACE",
                "Cookie": "PASTE_THE_SESSION_COOKIES_HERE",
                "User-Agent": "Mozilla/5.0 ..."
            },
            "subscribe": [
                {"_note": "the JSON or string frame(s) the client sends on open"}
            ],
            "ping": {"interval": 20, "message": ""}   # app-level keepalive, optional
        },
        {
            "name": "pragmatic-stats-TABLEID",
            "type": "http_poll",
            "provider": "auto",
            "enabled": False,
            "url": "https://REPLACE.pragmaticplaylive.net/api/ui/statisticHistory?tableId=TABLEID&numberOfGames=50&...",
            "headers": {"Cookie": "PASTE", "Origin": "https://REPLACE"},
            "interval": 15,
            "table_id": ""               # optional explicit tag; else taken from feed/URL
        }
    ]
}


# ── shared dedup (mirrors FeedTap) ─────────────────────────────────────────────
class _Deduper:
    def __init__(self):
        self._last: Dict[str, Tuple[int, Optional[str]]] = {}
        self._lock = threading.Lock()

    def is_new(self, tid: str, num: int, rnd: Optional[str]) -> bool:
        with self._lock:
            cur = (num, rnd)
            if self._last.get(tid) == cur:
                return False
            self._last[tid] = cur
            return True


class FeedCollector:
    """Runs every enabled source in its own thread; calls on_result(table_id,
    number) once per genuinely-new spin."""

    def __init__(self, config: dict,
                 on_result: Optional[Callable[[str, int], None]] = None,
                 log: Optional[Callable[[str], None]] = None):
        self.config = config or {}
        self.on_result = on_result
        self._log = log or (lambda m: print(m))
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []
        self._dedup = _Deduper()
        self.stats = {"frames": 0, "result_frames": 0, "emitted": 0,
                      "tables": set(), "errors": 0}

    # ----- lifecycle -----
    def start(self) -> None:
        self._stop.clear()
        for src in self.config.get("sources", []):
            if not src.get("enabled"):
                continue
            typ = src.get("type", "ws")
            target = {"ws": self._run_ws, "http_poll": self._run_http}.get(typ)
            if not target:
                self._log(f"[Collector] unknown source type '{typ}' ({src.get('name')})")
                continue
            t = threading.Thread(target=self._guard, args=(target, src),
                                 name=f"collect:{src.get('name','?')}", daemon=True)
            t.start()
            self._threads.append(t)
        if not self._threads:
            self._log("[Collector] No enabled sources. Fill the config from a "
                      "capture and set \"enabled\": true.")
        else:
            self._log(f"[Collector] started {len(self._threads)} source(s).")

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def status(self) -> str:
        s = self.stats
        return (f"Collector {'RUNNING' if self.is_running() else 'stopped'} | "
                f"sources={len(self._threads)} frames={s['frames']} "
                f"result_frames={s['result_frames']} spins={s['emitted']} "
                f"tables={len(s['tables'])} errors={s['errors']}")

    # ----- per-source workers (with reconnect/backoff) -----
    def _guard(self, target, src):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                target(src)
            except Exception as e:
                self.stats["errors"] += 1
                self._log(f"[Collector:{src.get('name')}] {type(e).__name__}: {e}")
            if self._stop.is_set():
                break
            time.sleep(min(backoff, 30))
            backoff = min(backoff * 2, 30)

    def _run_ws(self, src):
        import websocket  # websocket-client (already a dependency)
        name = src.get("name", "ws")
        provider = src.get("provider", "auto")
        headers = [f"{k}: {v}" for k, v in (src.get("headers") or {}).items()]
        subs = src.get("subscribe") or []
        ping = src.get("ping") or {}
        ping_msg = ping.get("message")
        ping_iv = float(ping.get("interval") or 0)

        def on_open(ws):
            self._log(f"[Collector:{name}] connected")
            for m in subs:
                if isinstance(m, dict) and "_note" in m and len(m) == 1:
                    continue
                try:
                    ws.send(m if isinstance(m, str) else json.dumps(m))
                except Exception:
                    pass
            if ping_msg and ping_iv > 0:
                def _beat():
                    while not self._stop.is_set() and ws.sock and ws.sock.connected:
                        try:
                            ws.send(ping_msg)
                        except Exception:
                            return
                        self._stop.wait(ping_iv)
                threading.Thread(target=_beat, daemon=True).start()

        def on_message(ws, message):
            self._handle_payload(message, src.get("url", ""), provider, src.get("table_id"))

        def on_error(ws, err):
            self._log(f"[Collector:{name}] ws error: {err}")

        ws = websocket.WebSocketApp(src["url"], header=headers, on_open=on_open,
                                    on_message=on_message, on_error=on_error)
        # run_forever blocks until the socket closes; _guard reconnects.
        ws.run_forever(ping_interval=ping_iv if (ping_iv and not ping_msg) else 0)

    def _run_http(self, src):
        import requests
        name = src.get("name", "http")
        provider = src.get("provider", "auto")
        iv = float(src.get("interval") or 15)
        headers = src.get("headers") or {}
        while not self._stop.is_set():
            try:
                r = requests.get(src["url"], headers=headers, timeout=10)
                if r.status_code == 200 and r.text.strip():
                    self._handle_payload(r.text, src.get("url", ""), provider,
                                         src.get("table_id"))
                else:
                    self._log(f"[Collector:{name}] HTTP {r.status_code}")
            except Exception as e:
                self.stats["errors"] += 1
                self._log(f"[Collector:{name}] poll error: {e}")
            self._stop.wait(iv)

    # ----- shared frame handling -----
    def _handle_payload(self, raw, url, provider, forced_tid=None):
        self.stats["frames"] += 1
        if isinstance(raw, (bytes, bytearray)):
            try:
                raw = raw.decode("utf-8")
            except Exception:
                return
        if not isinstance(raw, str):
            return
        s = raw.strip()
        if not s or s[0] not in "{[":
            return
        try:
            obj = json.loads(s)
        except Exception:
            return
        try:
            cands = extract_results(obj, socket_url=url, provider=provider)
        except Exception:
            return
        if cands:
            self.stats["result_frames"] += 1
        for tid, num, rnd in cands:
            tid = str(forced_tid or tid)
            if self._dedup.is_new(tid, num, rnd):
                self.stats["emitted"] += 1
                self.stats["tables"].add(tid)
                if self.on_result:
                    try:
                        self.on_result(tid, num)
                    except Exception as e:
                        self._log(f"[Collector] on_result error: {e}")


# ── config + replay helpers ────────────────────────────────────────────────────
def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_example_config(path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(EXAMPLE_CONFIG, f, indent=2)


def replay(path: str, provider: str = "auto") -> int:
    """Feed a capture file (one JSON frame per line, or a single JSON doc/array)
    through the parser and report what would be recorded. Proves a real capture
    offline — no network."""
    emitted: List[Tuple[str, int]] = []
    col = FeedCollector({}, on_result=lambda t, n: emitted.append((t, n)))
    frames = 0
    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()
    docs: List[str] = []
    # try whole-file JSON first; else treat as JSONL
    try:
        j = json.loads(text)
        docs = [json.dumps(x) for x in j] if isinstance(j, list) else [text]
    except Exception:
        docs = [ln for ln in text.splitlines() if ln.strip()]
    for d in docs:
        frames += 1
        # a sniffer line may wrap the frame as {"url":..,"sample":"<frame>"}
        url = ""
        payload = d
        try:
            o = json.loads(d)
            if isinstance(o, dict) and "sample" in o and "url" in o:
                url, payload = o["url"], o["sample"]
        except Exception:
            pass
        col._handle_payload(payload, url, provider)
    print(f"replay: {frames} frame(s) -> {col.stats['result_frames']} result frame(s), "
          f"{col.stats['emitted']} spins across {len(col.stats['tables'])} table(s)")
    for t, n in emitted[:60]:
        print(f"  {t} -> {n}")
    return 0 if emitted else 2


def _selftest() -> int:
    """Drive the collector's frame handler with canned provider-shaped frames
    (no network) and assert it records the right spins with correct dedup."""
    emitted: List[Tuple[str, int]] = []
    col = FeedCollector({}, on_result=lambda t, n: emitted.append((t, n)))
    frames = [
        ('{"tableId":"EVO-LR-1","last20Results":[{"result":"26","time":3},'
         '{"result":"4","time":2}]}', ""),
        ('{"tableId":"EVO-LR-1","last20Results":[{"result":"26","time":3}]}', ""),  # dup
        ('{"tables":{"PR-AZ":{"tableId":"PR-AZ","results":[19,0,8]},'
         '"PR-MEGA":{"tableId":"PR-MEGA","last20Results":[{"result":"32","time":9}]}}}', ""),
        ('{"type":"ping"}', ""),
        ('{"result":"11","color":"black"}', "wss://x/game?tableId=SPEED-9"),
        ('not json at all', ""),
    ]
    for raw, url in frames:
        col._handle_payload(raw, url, "auto")
    got = sorted(emitted)
    expected = sorted([("EVO-LR-1", 26), ("PR-AZ", 19), ("PR-MEGA", 32), ("SPEED-9", 11)])
    ok = got == expected
    print(f"  emitted={got}")
    print(f"  expected={expected}")
    print(f"  result_frames={col.stats['result_frames']} (expect 4)  "
          f"errors={col.stats['errors']}")
    print("SELFTEST:", "PASS" if ok and col.stats["result_frames"] == 4 else "FAIL")
    return 0 if ok else 1


def _main() -> int:
    ap = argparse.ArgumentParser(description="Standalone live-result collector (no CDP).")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--make-config", metavar="PATH", help="write an annotated example config")
    ap.add_argument("--replay", metavar="PATH", help="parse a capture file offline and report")
    ap.add_argument("--config", metavar="PATH", help="config JSON for --run / --probe")
    ap.add_argument("--run", action="store_true", help="connect live and feed the scout")
    ap.add_argument("--probe", metavar="NAME", help="connect ONE source and print raw frames")
    ap.add_argument("--provider", default="auto", choices=["auto", "reverse"])
    ap.add_argument("--report-every", type=int, default=20)
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.make_config:
        write_example_config(args.make_config)
        print(f"wrote annotated example -> {args.make_config}")
        return 0
    if args.replay:
        return replay(args.replay, args.provider)

    if not args.config:
        ap.error("need --config for --run/--probe (or use --make-config first)")
    cfg = load_config(args.config)

    if args.probe:
        src = next((s for s in cfg.get("sources", []) if s.get("name") == args.probe), None)
        if not src:
            print(f"no source named '{args.probe}' in config")
            return 2
        src = dict(src, enabled=True)
        seen = {"n": 0}

        def show(tid, num):
            seen["n"] += 1
            print(f"  [{seen['n']}] {tid} -> {num}")
        col = FeedCollector({"sources": [src]}, on_result=show)
        col.start()
        print(f"probing '{args.probe}' — Ctrl-C to stop")
        try:
            while col.is_running():
                time.sleep(3)
                print("  " + col.status())
        except KeyboardInterrupt:
            pass
        col.stop()
        return 0

    if args.run:
        from core.profit.scout_manager import BiasScoutManager
        persist = (cfg.get("persist") or "").strip() or None
        mgr = BiasScoutManager(persist_path=persist,
                               save_every=int(cfg.get("save_every", 100)))
        counter = {"n": 0}

        def on_result(tid, num):
            mgr.on_spin(num, table_id=tid)
            counter["n"] += 1
            if counter["n"] % max(1, args.report_every) == 0:
                print("\n" + mgr.report())
        col = FeedCollector(cfg, on_result=on_result)
        col.start()
        try:
            while col.is_running():
                time.sleep(5)
                print(col.status())
        except KeyboardInterrupt:
            print("\n[Collector] stopping…")
        finally:
            col.stop()
            mgr.flush()
            print("\nFINAL:\n" + mgr.report())
        return 0

    ap.error("nothing to do — pass --run, --probe, --replay, --selftest or --make-config")


if __name__ == "__main__":
    raise SystemExit(_main())
