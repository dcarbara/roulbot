# SpinEdge Strategies — User Guide

A practical guide to building roulette strategies in SpinEdge. Covers the four
strategy modes, the four pattern detectors, the five action types, and three
end-to-end walkthroughs.

If you just want to skim, jump to [Walkthroughs](#walkthroughs) — each one is a
copy-paste recipe you can adapt.

---

## What can I build?

| You want to… | Use mode |
|---|---|
| Bet a fixed list of labels every spin | **Static** |
| Bet on neighbors of recent / hot / cold numbers on the wheel | **Neighbors** |
| Bet based on a pattern in recent history (streak, dominance, alternation, regime) | **Pattern Follower** |
| Combine multiple patterns *or* automatically pick a different strategy per regime | **Composite** |

Pattern Follower handles 95% of "if X happens, bet Y" logic. Composite is for
the rest: compound conditions ("trending color **and** choppy dozen") and
**regime routing** (auto-switch between presets based on detected regime).

---

## The four strategy modes at a glance

### Static
Pre-select labels (red, black, 1st12, etc.). Place the same bet every spin.
Pair with any progression. Existing functionality.

### Neighbors
Bet on N wheel-neighbors of "anchor" numbers. Anchors can be: the last N
spins, the hottest N numbers in a window, the coldest N. Existing functionality.

### Pattern Follower
A list of rules. Each rule has one **detector** (what pattern to look for)
and one **action** (what to bet when it fires). Each spin, the **first
matching rule** wins. No match → no bet that spin.

Rule shape (saved JSON):
```json
{"detect": "<detector>", "group": "<group>", "...detector params...",
 "action": "<follow | contra | target>", "target": "<label, only for action=target>"}
```

### Composite
Like Pattern Follower, but each rule can have **multiple conditions** (all
must match — AND), and actions include **delegate** (hand off to another
saved preset). Use this for the regime-router pattern.

Rule shape:
```json
{"when": [<condition>, <condition>, ...],
 "then": {"action": "<follow | contra | target | labels | delegate>", "...action params..."}}
```

---

## Detectors — what each one measures

All detectors operate on a **group**: `color`, `parity` (even/odd), `hilo`
(1to18 / 19to36), `dozen`, or `column`.

### `streak`
Counts how many of the last spins fall in the same group member (e.g. how
many recent spins were red).

| Param | What | Default |
|---|---|---|
| `group` | which group to track | required |
| `min_length` | streak must be at least this long to fire | 1 |

**Use when:** you believe in trend continuation — "after 3 reds, I expect a 4th."

### `dominance`
Looks at a window of recent spins and asks: does one member dominate?
Different from streak: doesn't have to be consecutive, just a high share.

| Param | What | Default |
|---|---|---|
| `group` | which group | required |
| `window` | how many past spins to count | 20 |
| `threshold` | minimum share of the window (0–1) | 0.6 |

**Use when:** you want to detect a regime that's not strict streaks — e.g.
"red showed up 14 of the last 20 times, even with a few blacks mixed in."

### `alternation`
Measures how often consecutive spins flip groups (red→black→red→black is
100% alternation; red→red→red→black is 33%).

| Param | What | Default |
|---|---|---|
| `group` | which group | required |
| `window` | window size | 10 |
| `threshold` | minimum flip rate (0–1) | 0.7 |

**Use when:** you spot chop / ping-pong patterns and want to bet the flip
continues. The detector's predicted next outcome is the **opposite** of the
last spin — `follow` bets that opposite.

### `regime`
Composite of dominance + alternation. Classifies the current state as
`TRENDING`, `CHOPPY`, or `NEUTRAL`. Pick which regime fires the rule via
`match` (composite shape) or `regime` (flat shape).

| Param | What | Default |
|---|---|---|
| `group` | which group | required |
| `window` | window for both sub-signals | 20 |
| `trend_threshold` | dominance threshold for TRENDING | 0.6 |
| `chop_threshold` | flip-rate threshold for CHOPPY | 0.7 |
| `match` / `regime` | which state fires the rule: `TRENDING`, `CHOPPY`, or `["TRENDING","CHOPPY"]` for any-active | `TRENDING` |

**Use when:** you want one rule that recognises *either* a trend *or* chop
and you'd like to bet differently per regime — pair with **delegate** in
composite mode.

---

## Actions — what each one does

When a rule fires, its action determines what to bet.

| Action | What it bets | Example |
|---|---|---|
| `follow` | The signal's predicted member of the action's group | streak on red, action follow color → bet `red` |
| `contra` | All other members of the action's group | streak on red, action contra color → bet `black` |
| `target` | A specific fixed label | always bet `1st12` when this rule fires |
| `labels` | An explicit list of labels (composite-only) | bet `["red", "1st12"]` |
| `delegate` | Hand off to another saved preset (composite-only) | route to "Trend Bot" preset |

For `follow` / `contra`: the action's `group` can differ from the condition's
group — e.g. detect on color, bet on dozen.

---

## Walkthroughs

### 1) "Bet on a 3-color streak"

**Goal:** when the last 3 spins are the same color, bet that color.

**Mode:** Pattern Follower

**Steps in the GUI:**
1. Strategy Lab → Bet Mode → **Pattern Follower**
2. Type a name: `3-Color-Streak`
3. Click **+ Add Rule**
4. Detector: `streak`, Group: `color`, streak ≥ `3`, action: `follow`
5. **Save**

**Stored JSON:**
```json
"3-Color-Streak": {
  "mode": "pattern_follower",
  "history_size": 30,
  "rules": [
    {"detect": "streak", "group": "color", "min_length": 3, "action": "follow"}
  ]
}
```

When the bot has seen `[red, red, red]` recently → bets `red`. After a black
breaks the streak → no bet that spin until another 3-color streak forms.

### 2) "Auto-route between trending and choppy regimes"

**Goal:** when the wheel is trending colors, run my "Trend Bot" preset; when
it's chopping, run my "Chop Bot" preset.

**Mode:** Composite

**Prerequisites:** save two pattern_follower presets first — `Trend Bot` and
`Chop Bot` — each with their own logic.

**Steps:**
1. Strategy Lab → Bet Mode → **Composite**
2. Name: `Regime Router`
3. **+ Add Rule** for trending:
   - Condition: detector `regime`, group `color`, match `TRENDING`, window `20`
   - Action: `delegate`, strategy: `Trend Bot` (pick from dropdown)
4. **+ Add Rule** for choppy:
   - Condition: detector `regime`, group `color`, match `CHOPPY`, window `20`
   - Action: `delegate`, strategy: `Chop Bot`
5. **Save**

**Stored JSON:**
```json
"Regime Router": {
  "mode": "composite",
  "history_size": 50,
  "rules": [
    {"when": [{"detect": "regime", "group": "color", "window": 20, "match": "TRENDING"}],
     "then": {"action": "delegate", "strategy": "Trend Bot"}},
    {"when": [{"detect": "regime", "group": "color", "window": 20, "match": "CHOPPY"}],
     "then": {"action": "delegate", "strategy": "Chop Bot"}}
  ]
}
```

**How it behaves:**
- Each spin, classifies the last 20 spins' color regime.
- Trending → delegates to Trend Bot's logic.
- Choppy → delegates to Chop Bot.
- Neutral → no rule matches → sits out that spin.
- Both sub-strategies stay "warm" — they receive every spin's history even
  while inactive, so they're current the moment they're activated.

### 3) "Compound condition: trending color AND choppy dozen"

**Goal:** only bet when *both* the color is trending *and* the dozen is
choppy. Specifically, follow the dominant color.

**Mode:** Composite

**Steps:**
1. Strategy Lab → Bet Mode → **Composite**
2. Name: `Color Trend in Dozen Chop`
3. **+ Add Rule**, then **+ AND another condition** to add a second condition:
   - Condition 1: `regime`, group `color`, match `TRENDING`
   - Condition 2: `regime`, group `dozen`, match `CHOPPY`
   - Action: `follow`, group: `color`
4. **Save**

**Stored JSON:**
```json
"Color Trend in Dozen Chop": {
  "mode": "composite",
  "history_size": 50,
  "rules": [
    {"when": [
       {"detect": "regime", "group": "color", "match": "TRENDING"},
       {"detect": "regime", "group": "dozen", "match": "CHOPPY"}
     ],
     "then": {"action": "follow", "group": "color"}}
  ]
}
```

Both conditions must hold every spin. If either drops to NEUTRAL, no bet.

---

## Building a preset — quick reference

1. **Strategy Lab** tab → custom strategy builder
2. Type a unique **name**
3. Pick a **bet mode** — the matching editor appears
4. **+ Add Rule** to start. Fields adapt to the detector you pick.
5. For Composite, **+ AND another condition** stacks conditions on the same rule.
6. Order rules with **▲ / ▼**. First-match-wins, so put your most specific
   rules first and your fallbacks later.
7. **✕** removes a rule (or condition) from the editor.
8. **Save**. Validation runs at save time — bad config triggers a clear error.
9. The saved preset appears in the strategy dropdown like any built-in.

To **edit** an existing preset: select it in the dropdown → click the load /
edit button. The form repopulates exactly. Edit, then **Save** to overwrite.

To **convert pattern_follower → composite**: select a pattern_follower preset.
The "↗ Convert to Composite" button appears in the preview panel. Click,
choose a name, save. The converted preset uses the composite shape; you can
now add compound conditions or delegate actions. The original is unchanged.

---

## Tuning thresholds — where to start

The defaults are reasonable. Tweak if backtests suggest the rule fires too
often (false positives) or rarely (misses real patterns).

### Streak `min_length`
- 2 — fires often, weak signal, noisy
- **3 — typical sweet spot for color**
- 4–5 — rare, strong signal
- 6+ — very rare; you'll skip most spins

### Dominance `threshold` × `window`
- Wide window (30–50) + low threshold (0.55–0.6) → smooths noise, reacts slowly
- Narrow window (10–15) + high threshold (0.7+) → reacts fast, more false positives
- **Start: window 20, threshold 0.6** for color

### Alternation `threshold` × `window`
- Window <5 is unreliable
- **Start: window 10, threshold 0.7**
- Higher threshold (0.8+) only fires on near-pure ABAB sequences

### Regime `trend_threshold` / `chop_threshold`
Defaults match the dominance / alternation defaults. Adjust them in tandem
with the underlying detectors if you tune those.

---

## Tips

- **Order matters.** Composite and pattern_follower are first-match-wins.
  Put narrow / specific rules at the top, broad / fallback rules at the bottom.
- **Compose with progressions freely.** Any rule-based strategy pairs with any
  progression (flat, martingale, fibonacci, dalembert, dynamic). The strategy
  decides *where* to bet; the progression decides *how much*.
- **No-match is intentional.** When no rule fires, the bot sits out the spin.
  Use this — don't force a bet on every spin.
- **Sub-strategies stay warm.** In a composite preset with delegates, every
  sub-strategy receives every spin's history, even while inactive. They're
  history-current the moment regime switches activate them.
- **Backtest before going live.** Use the Strategy Lab → Backtest tab to
  replay historical numbers against your preset. Composite presets are
  supported by the backtester out of the box.
- **Use NEUTRAL deliberately.** Regime classifies as NEUTRAL when neither
  trending nor choppy fires. You can route NEUTRAL to a "DefaultStrat"
  delegate, or omit it (sit out).

---

## FAQ

### Why didn't my rule fire?
Most common reasons:
1. Not enough history yet — detectors need at least their `min_length` /
   `window` worth of spins. Watch the engine log; it'll say `streak_length=1`
   or `window_size=3` etc.
2. Earlier rule consumed the spin. First-match-wins: walk down your rule
   list mentally and check the first one that matches.
3. The 0 spin breaks streaks (zero is group-neutral for color/parity/etc.).
4. Threshold too high. Try lowering it temporarily and watch when it fires.

### What happens when no rule matches?
The bot sits out that spin. No bet placed. The progression doesn't advance.

### Can a composite delegate to another composite?
Yes. The engine catches cycles (A → B → A) at load time and refuses with a
clear error. Depth has no hard cap, but practical usefulness drops past
2–3 levels of routing.

### Can I edit a preset's JSON directly?
Yes. Edit `~/.spinedge/config/config.json`, save, and click the refresh (↻)
button next to the strategy dropdown — or restart. The visual editor handles
all detectors and actions, but JSON gives you full control for
edge cases (advanced `match` lists, custom history sizes, etc.).

### What's the difference between `target` action and `labels` action?
- `target` is for outside bets within a group: e.g. "always bet `red` from
  the color group when this rule fires." Group-aware, validates the target
  is a valid member.
- `labels` (composite-only) is fully explicit: bet exactly these labels,
  any combination across groups: `["red", "1st12", "col2"]`.

### What detectors are coming next?
The Signal layer is plugin-extensible. Anticipated:
- Markov / n-gram (sequential transition detector)
- Wheel-bias detector (statistical bias toward specific numbers)
- ML-based regime classifier (replace heuristic thresholds with a learned model)
- Profit-momentum (route based on which sub-strategy has been profitable lately)

Each lands as a new `Signal` subclass with no architectural changes — and
they'll appear in the detector dropdown automatically once registered.

---

## Where things live

If you ever need to dig in:
- Built-in detectors: [core/signals/builtins.py](../core/signals/builtins.py)
- Decision layer (rules, conditions, actions): [core/decision/rules.py](../core/decision/rules.py)
- Composite strategy class: [core/strategies/composite.py](../core/strategies/composite.py)
- Pattern Follower wrapper: [core/strategies/pattern_follower.py](../core/strategies/pattern_follower.py)
- GUI editors: [gui/components/condition_widget.py](../gui/components/condition_widget.py),
  [gui/components/action_widget.py](../gui/components/action_widget.py),
  [gui/components/pattern_follower_editor.py](../gui/components/pattern_follower_editor.py),
  [gui/components/composite_editor.py](../gui/components/composite_editor.py)
- Saved presets: `~/.spinedge/config/config.json`, key `custom_strategies`
