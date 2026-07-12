"""feed_tap.py — tap the live-casino result WebSocket and feed EVERY table's
spins into the per-wheel BiasScout, from one machine, with no OCR.

WHY this exists
---------------
OCR screen-scraping can only read the handful of browser windows you can tile
on screen (~2-3 tables), and a single misread injects PHANTOM bias into the
Bayesian model. The live-casino client (Stake's roulette is embedded
Evolution/Pragmatic) already receives every result over a WebSocket, and the
LOBBY socket carries recent results for MANY tables in ONE connection. Tapping
that feed gives dozens-to-hundreds of wheels per machine, near-zero CPU, and
zero OCR misreads — which is exactly what the bias scout needs (thousands of
CLEAN spins per wheel).

HOW it works
------------
The bot already runs Chrome with --remote-debugging-port=9222 (the playwright
driver connects there). This module attaches to that same Chrome over CDP with
Playwright, listens to every WebSocket frame on every page/iframe, extracts
(table_id, winning_number) from each result frame with a provider-agnostic
heuristic, de-duplicates repeated state snapshots, and calls `on_result(table_id,
number)` once per genuinely-new spin. Point that callback at
`BiasScoutManager.on_spin(number, table_id=...)` and every table the browser can
see starts accumulating its own posterior automatically.

The extractor is intentionally schema-agnostic: it finds a roulette-result shape
(a 0..36 number, optionally corroborated by a red/black/green colour, tagged by a
table-id field or the socket URL) anywhere in the JSON, including a lobby
snapshot that contains many tables at once. Run with `--sniff` to dump each new
frame shape so the exact provider schema can be confirmed in minutes — but the
heuristic is designed to work without that step.

This module NEVER places a bet. It only observes results and feeds the scout,
which surfaces a recommendation for the operator to confirm.

CLI
---
    python -m core.profit.feed_tap --selftest        # prove the parser (no browser)
    python -m core.profit.feed_tap --list-tabs       # show CDP tabs
    python -m core.profit.feed_tap --sniff feed.jsonl --verbose   # dump frame shapes
    python -m core.profit.feed_tap --run             # live: feed the persistent scout
"""
import argparse
import json
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

# ── key vocabulary (normalised: lowercased, '_' and '-' stripped) ──────────────
_RESULT_KEYS = {
    "result", "number", "winningnumber", "winnumber", "outcome", "winner",
    "winningpocket", "pocket", "value", "win", "resultnumber", "spinresult",
}
_TABLE_KEYS = {
    "tableid", "table", "tablename", "gameid", "wheel", "wheelid", "channel",
    "channelid", "vtid", "instance", "tablekey", "tablecode",
}
_ROUND_KEYS = {
    "gameid", "gamenumber", "round", "roundid", "gameround", "ballid",
    "spinid", "drawid", "id", "gamenum",
}
_HISTORY_KEYS = {
    "last20results", "lastresults", "last10results", "history", "results",
    "recent", "recentresults", "numbers", "resulthistory", "resultshistory",
    "last", "lastnumbers", "previousresults",
}
_TIME_KEYS = {"time", "timestamp", "ts", "createdat", "datetime", "date"}
_COLOR_KEYS = {"color", "colour"}
_COLOR_VALUES = {"red", "black", "green", "r", "b", "g", "rouge", "noir", "vert"}
# structural wrapper keys that must NEVER be mistaken for a table id when a
# dict is keyed by-table (map of tableId -> state)
_WRAPPER_KEYS = {
    "args", "data", "payload", "message", "body", "state", "info", "result",
    "results", "game", "tables", "response", "params", "event", "type", "d",
    "content", "detail", "meta", "status",
}


def _norm(k) -> str:
    return str(k).replace("_", "").replace("-", "").lower()


def _coerce_number(v) -> Optional[int]:
    """Return an int in 0..36 (single-zero European wheel) or None.

    Rejects '00' (American double-zero — not representable in the 37-pocket
    model) and anything out of range or non-numeric."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if 0 <= v <= 36 else None
    if isinstance(v, float):
        return int(v) if v.is_integer() and 0 <= v <= 36 else None
    if isinstance(v, str):
        s = v.strip()
        if not s or s == "00":
            return None
        tok = s.split()[0]          # tolerate "17 red"
        if tok.lstrip("-").isdigit():
            n = int(tok)
            return n if 0 <= n <= 36 else None
    return None


def _node_field(d: dict, keyset) -> Optional[str]:
    for k, v in d.items():
        if _norm(k) in keyset and isinstance(v, (str, int, float)) and not isinstance(v, bool):
            sv = str(v).strip()
            if sv:
                return sv
    return None


def _has_color(d: dict) -> bool:
    for k, v in d.items():
        if _norm(k) in _COLOR_KEYS and isinstance(v, str) and v.strip().lower() in _COLOR_VALUES:
            return True
    return False


def _direct_result(d: dict) -> Optional[int]:
    """A single round's winning number directly on this dict (not a history)."""
    for k, v in d.items():
        if _norm(k) in _RESULT_KEYS:
            n = _coerce_number(v)
            if n is not None:
                return n
    return None


def _plausible_table_key(k) -> bool:
    return _norm(k) not in _WRAPPER_KEYS


def _newest_from_history(arr: list, provider: str) -> Tuple[Optional[int], Optional[str]]:
    """Pick the newest result from a history array (newest-first by default;
    if elements carry a time/round, use the max)."""
    if not arr:
        return None, None
    # plain list of numbers -> assume index 0 is newest (Evolution/Pragmatic
    # convention) unless provider forces oldest-first.
    if all(not isinstance(e, (dict, list)) for e in arr):
        idx = -1 if provider == "reverse" else 0
        return _coerce_number(arr[idx]), None
    dicts = [e for e in arr if isinstance(e, dict)]
    if not dicts:
        return None, None

    def _t(e):
        for k, v in e.items():
            if _norm(k) in (_TIME_KEYS | _ROUND_KEYS):
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None

    timed = [(_t(e), e) for e in dicts]
    if any(t is not None for t, _ in timed):
        el = max((x for x in timed if x[0] is not None), key=lambda x: x[0])[1]
    else:
        el = dicts[-1] if provider == "reverse" else dicts[0]
    return _direct_result(el), (_node_field(el, _ROUND_KEYS) or _node_field(el, _TIME_KEYS))


def extract_results(obj, socket_url: Optional[str] = None,
                    provider: str = "auto") -> List[Tuple[str, int, Optional[str]]]:
    """Pull every (table_id, winning_number, round_id) from one decoded frame.

    Handles three shapes uniformly:
      - a single round result  ({... result: 17 ...})
      - a per-table state with a history array (last20Results / results)
      - a LOBBY snapshot carrying many tables at once (map or list of states)
    """
    out: List[Tuple[str, int, Optional[str]]] = []
    seen: set = set()
    url_tid = _table_id_from_url(socket_url)

    def emit(tid, num, rnd):
        if num is None:
            return
        tid = str(tid or url_tid or "default")
        key = (tid, num, rnd)
        if key in seen:
            return
        seen.add(key)
        out.append((tid, num, rnd))

    def walk(node, inherited_tid):
        if isinstance(node, dict):
            tid = _node_field(node, _TABLE_KEYS) or inherited_tid
            rnd = _node_field(node, _ROUND_KEYS)
            num = _direct_result(node)
            if num is not None:
                emit(tid, num, rnd)
            for k, v in node.items():
                nk = _norm(k)
                if nk in _HISTORY_KEYS and isinstance(v, list):
                    hnum, hrnd = _newest_from_history(v, provider)
                    emit(tid, hnum, hrnd or rnd)
                    continue  # already consumed; don't recurse as live results
                if isinstance(v, (dict, list)):
                    child = tid
                    if isinstance(v, dict) and _plausible_table_key(k) and _looks_like_state(v):
                        child = str(k)
                    walk(v, child)
        elif isinstance(node, list):
            dict_els = [e for e in node if isinstance(e, dict)]
            res_els = [e for e in dict_els if _direct_result(e) is not None]
            tids = {_node_field(e, _TABLE_KEYS) for e in res_els}
            if dict_els and len(res_els) == len(dict_els) and len(tids - {None}) <= 1:
                # homogeneous results array (a history list under an unknown key)
                hnum, hrnd = _newest_from_history(node, provider)
                emit(inherited_tid, hnum, hrnd)
            else:
                for el in node:
                    walk(el, inherited_tid)

    walk(obj, None)
    return out


def _looks_like_state(d: dict) -> bool:
    """A dict that carries a round result or a result history."""
    if _direct_result(d) is not None:
        return True
    return any(_norm(k) in _HISTORY_KEYS and isinstance(v, list) for k, v in d.items())


def _table_id_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        u = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(u.query)
        for want in ("tableid", "table_id", "table", "instance", "vtid", "tablename", "wheel"):
            for qk, qv in q.items():
                if qk.lower() == want and qv:
                    return qv[0][:48]
        segs = [s for s in u.path.split("/") if s]
        if segs:
            return segs[-1][:48]
    except Exception:
        pass
    return None


# ── live capture over CDP (Playwright) ─────────────────────────────────────────
class FeedTap:
    """Attach to the running Chrome over CDP and stream result frames.

    on_result(table_id, number) is called once per genuinely-new spin per table.
    """

    def __init__(self, on_result: Optional[Callable[[str, int], None]] = None,
                 cdp_url: str = "http://localhost:9222", provider: str = "auto",
                 url_filter: Optional[str] = None, sniff_path: Optional[str] = None,
                 verbose: bool = False, log: Optional[Callable[[str], None]] = None):
        self.on_result = on_result
        self.cdp_url = cdp_url
        self.provider = provider
        self.url_filter = url_filter            # only tap sockets whose URL contains this
        self.sniff_path = sniff_path
        self.verbose = verbose
        self._log = log or (lambda m: print(m))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last: Dict[str, Tuple[int, Optional[str]]] = {}
        self._lock = threading.Lock()
        self._sniff_seen: set = set()
        self.stats = {"frames": 0, "result_frames": 0, "emitted": 0, "sockets": 0,
                      "tables": set()}

    # ----- lifecycle -----
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="FeedTap", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> str:
        s = self.stats
        return (f"FeedTap {'RUNNING' if self.is_running() else 'stopped'} | "
                f"sockets={s['sockets']} frames={s['frames']} "
                f"result_frames={s['result_frames']} spins_emitted={s['emitted']} "
                f"tables={len(s['tables'])}")

    # ----- capture loop -----
    def _run(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except Exception as e:                              # pragma: no cover
            self._log(f"[FeedTap] Playwright unavailable: {e}")
            return
        ws = self._cdp_ws_endpoint()
        if not ws:
            self._log(f"[FeedTap] No CDP at {self.cdp_url} — start Chrome with "
                      f"--remote-debugging-port=9222 and open the live tables.")
            return
        pw = None
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp(ws)
            self._log("[FeedTap] Connected over CDP. Tapping result frames "
                      "(reload the lobby if nothing appears within a minute).")
            for ctx in browser.contexts:
                for page in list(ctx.pages):
                    self._wire_page(page)
                ctx.on("page", self._wire_page)
            while not self._stop.is_set():
                pages = [p for c in browser.contexts for p in c.pages]
                if pages:
                    try:
                        pages[0].wait_for_timeout(500)     # pumps Playwright events
                    except Exception:
                        time.sleep(0.5)
                else:
                    time.sleep(0.5)
        except Exception as e:
            self._log(f"[FeedTap] Capture loop ended: {e}")
        finally:
            try:
                if pw:
                    pw.stop()
            except Exception:
                pass

    def _wire_page(self, page) -> None:
        try:
            page.on("websocket", self._wire_ws)
        except Exception:
            pass

    def _wire_ws(self, ws) -> None:
        try:
            url = ws.url or ""
            if self.url_filter and self.url_filter not in url:
                return
            self.stats["sockets"] += 1
            if self.verbose:
                self._log(f"[FeedTap] socket #{self.stats['sockets']}: {url[:120]}")
            ws.on("framereceived", lambda payload: self._on_frame(url, payload))
        except Exception:
            pass

    def _on_frame(self, url: str, payload) -> None:
        self.stats["frames"] += 1
        if isinstance(payload, (bytes, bytearray)):
            try:
                payload = payload.decode("utf-8")
            except Exception:
                return
        if not isinstance(payload, str):
            return
        s = payload.strip()
        if not s or s[0] not in "{[":
            return
        try:
            obj = json.loads(s)
        except Exception:
            return
        if self.sniff_path:
            self._sniff(url, obj, s)
        try:
            cands = extract_results(obj, socket_url=url, provider=self.provider)
        except Exception:
            return
        if cands:
            self.stats["result_frames"] += 1
        for tid, num, rnd in cands:
            self._maybe_emit(tid, num, rnd)

    def _maybe_emit(self, tid: str, num: int, rnd: Optional[str]) -> None:
        with self._lock:
            prev = self._last.get(tid)
            cur = (num, rnd)
            if prev == cur:
                return                                      # repeated state snapshot
            self._last[tid] = cur
            self.stats["emitted"] += 1
            self.stats["tables"].add(tid)
        if self.verbose:
            self._log(f"[FeedTap] {tid} -> {num}")
        if self.on_result:
            try:
                self.on_result(tid, num)
            except Exception as e:
                self._log(f"[FeedTap] on_result error: {e}")

    def _sniff(self, url: str, obj, raw: str) -> None:
        keys = tuple(sorted(obj.keys())) if isinstance(obj, dict) else ("<list>",)
        shape = (url.split("?")[0][-44:], keys)
        if shape in self._sniff_seen:
            return
        self._sniff_seen.add(shape)
        self._log(f"[FeedTap][sniff] new frame shape on …{url.split('?')[0][-46:]} "
                  f"keys={list(keys)[:14]}")
        try:
            with open(self.sniff_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"url": url, "keys": list(keys),
                                    "sample": raw[:1500]}) + "\n")
        except Exception:
            pass

    def _cdp_ws_endpoint(self) -> Optional[str]:
        try:
            with urllib.request.urlopen(self.cdp_url.rstrip("/") + "/json/version",
                                        timeout=4) as r:
                return json.loads(r.read()).get("webSocketDebuggerUrl")
        except Exception:
            return None


def list_tabs(cdp_url: str = "http://localhost:9222") -> List[dict]:
    try:
        with urllib.request.urlopen(cdp_url.rstrip("/") + "/json", timeout=4) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[FeedTap] CDP not reachable at {cdp_url}: {e}")
        return []


# ── self-test: prove the parser on representative provider frames ──────────────
def _selftest() -> int:
    cases = [
        # (name, frame, socket_url, expected {(tid, num), ...})
        ("evolution-game-result",
         {"type": "roulette.result",
          "args": {"tableId": "LightningRoulette001", "gameId": "g-9931",
                   "result": 17, "color": "black"}},
         "wss://x/ws?tableId=LightningRoulette001",
         {("LightningRoulette001", 17)}),
        ("pragmatic-last20",
         {"tableId": "mrbcasinoroul0001",
          "last20Results": [{"result": "0", "color": "green", "time": 1700000200},
                            {"result": "5", "color": "red", "time": 1700000100}]},
         "wss://gs.pragmaticplaylive.net/game?tableId=mrbcasinoroul0001",
         {("mrbcasinoroul0001", 0)}),
        ("lobby-snapshot-many-tables",
         {"tables": {"LR1": {"tableId": "LR1", "results": [7, 7, 32, 15]},
                     "LR2": {"tableId": "LR2",
                             "last20Results": [{"result": "31", "time": 2},
                                               {"result": "00", "time": 1}]}}},
         "wss://x/lobby",
         {("LR1", 7), ("LR2", 31)}),
        ("array-of-per-table-latest",
         [{"tableId": "A", "result": 5}, {"tableId": "B", "result": 12}],
         "wss://x/lobby",
         {("A", 5), ("B", 12)}),
        ("table-id-from-url-only",
         {"result": 22, "color": "black"},
         "wss://x/game?vtid=SpeedRoulette7",
         {("SpeedRoulette7", 22)}),
        ("american-double-zero-rejected",
         {"tableId": "AM1", "result": "00", "color": "green"},
         None, set()),
        ("heartbeat-no-result", {"type": "ping", "seq": 4}, None, set()),
        ("chat-noise", {"type": "chat", "message": "gl all", "user": "bob"}, None, set()),
    ]
    ok = True
    for name, frame, url, expected in cases:
        got = {(t, n) for t, n, _ in extract_results(frame, socket_url=url)}
        status = "ok " if got == expected else "FAIL"
        if got != expected:
            ok = False
        print(f"  [{status}] {name:<32} expected={sorted(expected)} got={sorted(got)}")
    # de-dup behaviour: repeated snapshot of the same newest result emits once.
    tap = FeedTap(on_result=lambda t, n: emitted.append((t, n)))
    emitted: List[Tuple[str, int]] = []
    snap = {"tableId": "D1", "last20Results": [{"result": "9", "time": 5}]}
    for _ in range(3):
        tap._on_frame("wss://x", json.dumps(snap))           # same spin 3x
    snap2 = {"tableId": "D1", "last20Results": [{"result": "14", "time": 6},
                                                {"result": "9", "time": 5}]}
    tap._on_frame("wss://x", json.dumps(snap2))               # new spin
    dedup_ok = emitted == [("D1", 9), ("D1", 14)]
    print(f"  [{'ok ' if dedup_ok else 'FAIL'}] dedup-across-snapshots          "
          f"emitted={emitted}")
    ok = ok and dedup_ok
    print("SELFTEST:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def _main() -> int:
    ap = argparse.ArgumentParser(description="Tap the live-casino result WebSocket.")
    ap.add_argument("--selftest", action="store_true", help="run the parser self-test (no browser)")
    ap.add_argument("--list-tabs", action="store_true", help="list CDP tabs and exit")
    ap.add_argument("--sniff", metavar="PATH", help="dump each new frame shape to PATH (jsonl)")
    ap.add_argument("--run", action="store_true", help="feed the persistent BiasScout live")
    ap.add_argument("--cdp", default="http://localhost:9222", help="CDP base URL")
    ap.add_argument("--provider", default="auto", choices=["auto", "reverse"],
                    help="'reverse' if the history array is oldest-first")
    ap.add_argument("--filter", dest="url_filter", default=None,
                    help="only tap sockets whose URL contains this substring")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--report-every", type=int, default=20, help="--run: report cadence (spins)")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    if args.list_tabs:
        for t in list_tabs(args.cdp):
            print(f"  {t.get('type','?'):<8} {t.get('title','')[:50]:<52} {t.get('url','')[:80]}")
        return 0

    on_result = None
    mgr = None
    if args.run:
        from core.profit.scout_manager import BiasScoutManager
        mgr = BiasScoutManager()
        counter = {"n": 0}

        def on_result(tid, num):
            mgr.on_spin(num, table_id=tid)
            counter["n"] += 1
            if counter["n"] % max(1, args.report_every) == 0:
                print("\n" + mgr.report())

    tap = FeedTap(on_result=on_result, cdp_url=args.cdp, provider=args.provider,
                  url_filter=args.url_filter, sniff_path=args.sniff, verbose=args.verbose)
    tap.start()
    print(tap.status() if tap.is_running() else "[FeedTap] starting…")
    try:
        while tap.is_running():
            time.sleep(5)
            print(tap.status())
    except KeyboardInterrupt:
        print("\n[FeedTap] stopping…")
    finally:
        tap.stop()
        if mgr:
            mgr.flush()
            print("\nFINAL:\n" + mgr.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
