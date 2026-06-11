# TSE Trading Playbook

**My personal playbook for trading the Tokyo Stock Exchange.** Living document — one entry per
setup, grown over time. This is the *human execution* layer; the scanner (`DESIGN.md`) is the
*detection* layer. The scanner finds candidates and buckets them by lifecycle stage; this
playbook says what I actually *do* with them — sizing, entry tactics, stops, targets, exits, and
the post-mortem. (`DESIGN.md` §11.6 deliberately scopes execution *out* — that gap is this doc.)

Last updated: 2026-06-10.

---

## 0. How to use this playbook

- **One setup = one entry**, built from the template in the appendix. Each entry is self-contained:
  I should be able to trade a name end-to-end from its entry alone.
- **Detection lives in `DESIGN.md`; decisions live here.** Where a field is *measured* (days-to-cover,
  BB-width percentile, box, ignition), I link to `DESIGN.md` rather than redefine it, so the two never
  drift. Where a *trade* is decided (size, where to buy, where to bail), it lives here.
- **`〔…〕` = I fill this in** (account size, risk %, my broker's quirks — nobody else can).
- **"Phase-1 tunable" = the backtest sets the number**, not my gut. Starting guesses are mirrored
  from `DESIGN.md` §10 so there's a single source of truth. Until the `DESIGN.md` §6 event study
  passes its GO/NO-GO gate, every number here is a *hypothesis*.
- **Honesty rule (inherited from `DESIGN.md` §11):** if I don't know it yet, it says `〔TODO〕` — I do
  not pretend a number is proven.

### Setup lifecycle (graduation — a setup earns size, it isn't given it)

```
Idea  →  Building  →  Backtest-validated  →  Paper  →  Small size  →  Full size
                         ▲ DESIGN.md §6 GO/NO-GO gate         ▲ my own live-trade review
```

A setup only moves right by clearing the gate to its left. Nothing trades at **Full size** before it
has survived the point-in-time event study *and* a paper + small-size live sample. No exceptions —
this is the rule that protects the account from a backtest that lied (`DESIGN.md` §11.4).

### Status legend

| Badge | Meaning |
|---|---|
| ⚪ Idea | Sketched, not specified |
| 🔵 Building | Specified, detection being built; **not tradeable yet** |
| 🟣 Backtest-validated | Passed the `DESIGN.md` §6 event study |
| 🟡 Paper / Small | Live but on paper or minimum size, gathering my own sample |
| 🟢 Live | Cleared to full size |
| 🔴 Retired | Edge decayed or disproven — kept for the lesson |

---

## Setup index

| ID | Name | Dir. | Scanner stage → act on | Status | Edge in one line |
|---|---|---|---|---|---|
| **F-01** | Fumiage (踏み上げ) short squeeze | Long | `Igniting` (watch from `Primed`) | 🔵 Building | Crowded, trapped shorts read off the margin tape + a wound price coil → catch the upside break that runs them. |
| F-02 | _next setup_ | — | — | ⚪ Idea | _e.g. price-limit (ストップ高) follow-through, PBR<1 reform, index rebalance…_ |

---

## Shared conventions

- **All times JST.** TSE cash session 09:00–11:30 / 12:30–15:00 (post-2024 close). Margin/JSF prints
  land **after** the close — see freshness below.
- **Risk is measured in R.** `1R = 〔my fixed risk per trade in ¥〕 = 〔X%〕 of account`. Every position is
  sized so that hitting the initial stop loses ~1R. P/L is journaled in R, not yen, so setups compare
  apples-to-apples across account sizes.
- **Account base:** `〔account size ¥〕` · **max risk live at once:** `〔N×R〕` · **max per name:** `〔1R〕`
  (squeeze names gap — see §F-06).
- **Data freshness is a first-class risk.** Weekly margin (信用取引残高) is *as-of Friday* but
  *published the following Tue–Wed* (`DESIGN.md` §6.1). I trade on the **published** number, never the
  as-of one. Fuel is therefore always stale by design; the **price trigger carries the freshness.**
- **Stages** (`DESIGN.md` §2): `Crowded` → `Primed` → `Igniting` → `Confirmed`. I *watch* from Primed
  and *act* on Igniting. "Confirmed" (売残 actually dropping) is lagging — it's post-trade validation,
  not an entry.

---

## Setup F-01 — Fumiage (踏み上げ) short squeeze  ·  LONG  ·  🔵 Building

> **Not tradeable yet.** Detection is being built and the edge is unproven until `DESIGN.md` §6 passes.
> This entry is the trading plan I'll execute *once it graduates* — written now so it's ready, and so
> building the scanner and planning the trade stay coherent.

### A. Thesis & edge
Shorts are **crowded and trapped** — I read that directly off Japan's published margin tape instead of
*inferring* it from chart shape the way SMC/ICT traders do (and are often wrong about). When price is
also **wound into a tight coil** and then **breaks up**, the shorts are forced to cover; covering is
forced buying, which is self-reinforcing → 踏み上げ. Long-only: in a squeeze the trapped party is the
shorts, so the move is upside. (Full rationale: `DESIGN.md` §1.)

**Why this is an edge and not just momentum:** the *fuel* (trapped positioning) is observable and
quantified before the move, not guessed. That's the whole reason this beats a generic breakout.

### B. When it works / regime
- Best in **risk-on / rotational tapes** where small-mid caps run; squeezes **cluster** (regime-dependent,
  lumpy — `DESIGN.md` §11.5). Expect droughts.
- **Mid/small caps**, not mega Prime — float has to be small enough that covering actually moves price.
- Tailwinds: a hot sector/theme, broad short-covering days, quarter-end / 信用期日 clustering (see §F-12).
- Headwind regime: heavy-tape selloffs (everything offered, breakouts fail) and dead-quiet drift (no
  trigger). When in doubt about regime, **demand more fuel**, not less.

### C. Watchlist criteria — graduate `Crowded` → `Primed`
A name earns the active watchlist when **fuel + coil** are both present (detection: `DESIGN.md` §3.1–3.2):

- **Eligibility (hard gates):**
  - **貸借銘柄 only** — thesis requirement; 逆日歩 and a 制度信用 short-trap exist *only* here (`DESIGN.md` §5).
  - Turnover ≥ `〔¥100M/day, 20d avg〕` and price ≥ `〔¥100–300〕` — must be exitable (Phase-1 tunable).
  - Not 監理・整理, not ETF/REIT/ETN, has ≥120-session baseline.
- **Fuel (the edge):**
  - **Days-to-cover** (信用売残 ÷ ADV) ≥ `〔~5〕` — primary gauge of "trapped" (Phase-1 tunable).
  - **信用倍率 < 1.0** gate (買残 ÷ 売残 < 1 → more shorts than longs; 取り組み良好).
  - Boosters (weights *earned* in the backtest, not assumed — `DESIGN.md` §3.1): 逆日歩 present/rising;
    信用売残 rising across the ~3 weekly prints in the setup window.
- **Coil (the timing):** BB-width ≤ `〔~20th pct〕` of the 120d baseline **and** price boxed over the
  ~14-session window (`DESIGN.md` §3.2). The spring is wound.

If fuel + coil are both on → **Primed**. I put it on the live watchlist and pre-compute the trade
(box high/low, intended stop, size) so I'm not doing math when it triggers.

**Also pre-run the fundamental memo at Primed** (`tse-scanner research <code>` or the dashboard 🔬
button — `DESIGN.md` §15): the bear case, dilution risk and catalyst calendar take ~10 minutes to
research and zero minutes to read at the trigger. Research at Primed, not at Igniting — the break
won't wait for a memo. The verdict is a **discretionary overlay**: it never changes the scanner's
stage/score, it changes *my* size, hold and exit rules (§D, §H, §O).

### D. Trigger / entry — `Igniting`
Act only on a **confirmed** ignition (detection: `DESIGN.md` §3.3). Two variants:

1. **Break (standard):** **close** above the box high **+** volume surge `〔≥2× 20d〕` **+** wide,
   band-expanding bar. Close — not an intraday poke; volume + width reject fakeouts.
2. **Spring (premium tag):** price first **dips below the box low** (sweeps sell-side stops → validates
   shorts, who *add*), then **reclaims and breaks up**. Maximal trap; this is the cleanest version and
   ranks higher (`DESIGN.md` §3.3, §7).

I do **not** anticipate the break (no buying inside the box hoping). The whole point is that the trap
is *sprung*, not *forecast*.

**Fundamental gate at the trigger** (memo from §C, `DESIGN.md` §15):
- **🔴 OPPOSES** — intact bear thesis or a live squeeze-killer (公募増資/MSワラント/CB overhang):
  either pass, or take it **mechanically only** — reduced size, sell the *full* position at target,
  no runner. A cash-hungry company sells into its own squeeze.
- **⚪ NEUTRAL** — trade the technicals exactly as written; fundamentals neither pay nor charge.
- **💚 SUPPORTS** — broken bear case / upward-revision streak / buyback: full size within §F caps,
  and §H's runner rules get more rope.
- **No memo / stale memo** = NEUTRAL, not "skip the check next time."

### E. Entry tactics — the stop-high reality
The best squeezes lock **ストップ高 (limit-up)** and **you cannot buy a locked board** (`DESIGN.md`
§11.3, §6.2). So the trigger firing ≠ a fill. My rules, in order:

- **If it triggers and is still trading with room** (not locked): buy the break — `〔market / marketable
  limit〕`, `〔full size at once / half-on-break + half-on-hold-above-box-high〕`. **Decide the lot before
  it fires** (pre-computed at Primed).
- **If it locks ストップ高 before I'm in:** I have *missed the clean entry.* Options, by preference —
  pick one and write it as a rule, don't improvise:
  1. **Stand down** — accept the miss. No chasing locked boards. *(Default — protects expectancy.)*
  2. **比例配分 lottery** — leave the buy in for proportional allocation at the close; expect tiny,
     partial fills only.
  3. **Next-session 寄り成り** (market-on-open) — only if fuel is still building and I accept gap risk;
     size **down** for the gap.
  4. **Buy the first pullback** to the breakout retest (box high holds as support) — cleanest re-entry.
- **Never** market into an illiquid, already-extended print (slippage + 提灯 exhaustion — see §F-11).

> `〔Pick my default among 1–4 above and commit to it. Starting default: 1 (stand down) + 4 (retest).〕`

### F. Position sizing & risk
- **Size from the stop, not the conviction:** `shares = 1R ÷ (entry − initial_stop)`, rounded to a
  TSE lot (`〔100-share units〕`).
- **Gap down-size:** squeeze names gap *both* ways and stops can blow through (`DESIGN.md` §11.3).
  When the entry→stop distance is small (tight box), the formula wants a *big* position — **cap it** at
  `〔max ¥ per name / max % of ADV so I can actually exit〕`. Liquidity caps size before the math does.
- **One squeeze ≈ 1R of risk.** Don't let three correlated squeezes in one hot theme = 3R of the same bet.
- `〔My exact risk-per-trade %, account size, and per-name ¥ cap — fill in.〕`

### G. Initial stop — thesis invalidation
The stop is **where the setup is wrong**, not a round number:
- **Break variant:** below the **box low** (the coil failed / break was a fakeout). Starting guess
  −`〔6%〕` (Phase-1 tunable; `DESIGN.md` §10).
- **Spring variant:** below the **spring low** (the sweep low) — if that breaks, it wasn't a sweep, it
  was a breakdown.
- **Reality:** in a gap-through, my fill is worse than my stop. I size (§F) assuming the stop can slip;
  I never assume a clean exit at the line.

### H. Targets & scaling out
Squeezes **spike and fade** — I plan to *sell into strength*, not round-trip it:
- **First-touch target** +`〔12%〕` within ~`〔10〕` sessions is the backtest's win label (first-touch
  triple-barrier, `DESIGN.md` §6). I trade to match the label: **scale out into the spike.**
- Plan: `〔e.g. sell ⅓ at +1R, ⅓ into the first ストップ高 / target tag, trail the last ⅓〕`.
- **Memo modulates the runner, not the target:** 🔴 OPPOSES → no runner, full exit at target (§D).
  💚 SUPPORTS with a dated catalyst still ahead (決算 beat streak, buyback window) → trail the last
  ⅓ wider. A memo **catalyst inside the horizon** (flagged in the dashboard) = know *before* entry
  whether I'm holding through a binary print or exiting in front of it (§L's earnings disqualifier).
- **ストップ高 mechanics work *for* me on exit:** strength is where liquidity is — feed the offer into
  the spike rather than waiting for the fade.

### I. Trade management
- **売残 dropping on the next weekly print = shorts covering = thesis confirming** (`Confirmed` stage).
  Good — but it's *lagging*; I don't wait for it to take initial profit.
- **逆日歩 spiking while I'm in** = acute lending scarcity = squeeze pressure rising. Bullish to hold a
  runner; but it *fires late* (`DESIGN.md` §3.1) — it confirms, it doesn't initiate.
- **増担保規制 / 日々公表 announced mid-trade** (see §F-11) = exchange is cooling it; fuel-adders get
  margin-called, and this often marks the local top. **De-risk / take the rest.**
- **Trail** behind higher box / prior-bar lows once it's run `〔+1R〕`; never let a winner round-trip to a loss.

### J. Time stop
Squeezes work **fast or not at all.** If price hasn't moved meaningfully within `〔~10〕` sessions of entry
(the first-touch horizon), the ignition failed → **exit at market**, take the scratch, recycle the risk.
A squeeze that sits is a squeeze that isn't.

### K. Exit checklist (any one → act)
- ☐ +target tag → scale out per §H
- ☐ Initial stop / box-low (or spring-low) breached → out
- ☐ Time stop hit (no move in `〔~10〕` sessions) → out
- ☐ 増担保規制 / 日々公表 imposed → de-risk to runner or flat
- ☐ Parabolic / multi-ストップ高 exhaustion + 提灯 crowd → sell into it, don't hold for "more"

### L. Disqualifiers — do not touch
- **一般信用-only (not 貸借銘柄)** → no 逆日歩, no system short-trap → **no thesis** (`DESIGN.md` §5).
- **増担保規制 already on / 日々公表 flagged** → fuel is being actively drained; the easy part is over.
- **監理・整理 post** → delisting risk, not a squeeze.
- **Too liquid / huge float** → covering can't move it; this is a Prime-megacap mistake.
- **Earnings / binary event inside the horizon** → unless *that's* the catalyst, the gap is a coin-flip, not a squeeze.
- **Already extended / 提灯 exhausted** → several ストップ高 already, parabolic, retail piled in → I'm late; this is exit liquidity.
- **Turnover below the floor** → I can't get out; a squeeze I can't exit is a trap *for me*.

### M. Catalysts that supercharge the cover
The fuel is potential; a catalyst lights it. Bigger edge when the ignition coincides with:
- **逆日歩 spike** (株不足 — lendable shares run out) — shorts bleed daily carry.
- **信用期日 approaching** — 制度信用 settles in **6 months**; positions opened ~6mo ago *must* cover (§F-12).
- **Earnings beat / 上方修正 (guidance raise) / 自社株買い (buyback) / index inclusion / activist** — a reason for the first leg up that makes shorts blink.

### N. Where I read each field
| Field | Source |
|---|---|
| Bear case, dilution risk, catalysts, float | `tse-scanner research <code>` memo (DESIGN.md §15) · 株探 · IR BANK · karauri.net |
| 信用売残 / 買残 / 倍率, days-to-cover | Scanner (J-Quants weekly margin) · broker screen · 株探 |
| 逆日歩, daily 貸借残, 株不足 | 日証金 (JSF) · トレーダーズ・ウェブ · 株探 |
| Coil / box / volume / break | Scanner + chart |
| 増担保規制 / 日々公表 / 監理・整理 | JPX/TSE notices · broker alerts |
| 信用期日 | Computed from open date (制度=6mo) · broker position screen |

### O. Pre-trade checklist (paste before every entry)
```
Code / name:                         Stage: Primed → Igniting?  [ ]
貸借銘柄?                       [ ]   Variant: break / spring
Days-to-cover ≥ thresh?        [ ]   信用倍率 < 1?              [ ]
逆日歩 present/rising?          [ ]   売残 rising 3 prints?      [ ]
BB-width ≤ pctile? boxed?      [ ]   Confirmed close > box high + vol + wide bar? [ ]
NO 増担保 / 日々公表 / 監理?    [ ]   No earnings in horizon?    [ ]
Research memo (≤5d old): verdict 💚/⚪/🔴 ___  squeeze-killers? ___  catalyst in horizon? ___
Box high ___  box low ___  entry ___  stop ___  size(1R) ___ shares
Stop-high plan if it locks: ___ (default: stand down + retest)
Target ___  time stop ___ sessions   Risk = 1R = ¥___ (___% acct)
```

### P. Journal & review
Log **every** trade (and every *passed* trigger — the misses teach the most). One row per trade:

| Date | Code | Name | Variant | Stage@entry | 売残 / 倍率 / DtC / 逆日歩 | BBpct / box H-L | Entry | Stop | Size (R) | Exits | P/L (R) | Label (win/loss/scratch) | Lesson |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Label = first-touch** (matches `DESIGN.md` §6): +target first = win, −stop first = loss, neither = scratch.

**Weekly review prompts:**
- Did `Primed` names actually progress to `Igniting`? (this *is* the live validation by-product — `DESIGN.md` §2)
- Win-rate × payoff in R — is realized expectancy tracking the backtest, or diverging? (divergence = the backtest lied or regime shifted)
- Which disqualifier did I ignore when I lost?
- Stop-high: how often did I miss the clean fill, and did my rule (§E) cost or save R?

### Q. Worked examples
`〔Add 2–3 real, dated examples once Phase 1 surfaces them — one clean break, one spring, one failure.
Screenshot the coil + the 売残 trend + the break; annotate entry/stop/exit and the realized R.〕`

### R. Setup-specific open questions to calibrate
- Best **days-to-cover** and **BB-width** cutoffs (Phase-1 event study).
- Does the **spring** variant actually out-earn the plain break enough to wait for it?
- Real **publication latency** of weekly margin, and whether **日証金 daily** fuel is worth wiring in
  for fresher edge (`DESIGN.md` §12, §4-Phase 4).
- My personal **stop-high rule** (§E) — which of options 1–4 keeps the most R live.

---

## Appendix — blank setup template (copy for the next setup)

```markdown
## Setup 〔ID〕 — 〔Name〕  ·  〔LONG/SHORT〕  ·  〔status badge〕

### A. Thesis & edge          — why this works; why it's edge, not noise. Link DESIGN.md if detected there.
### B. When it works / regime — conditions that favor / kill it.
### C. Watchlist criteria      — what graduates a name onto the watchlist (gates + signals).
### D. Trigger / entry         — the exact, confirmed condition to act on.
### E. Entry tactics           — order types, fill reality, how I actually get in.
### F. Position sizing & risk  — R-based; liquidity caps; correlation cap.
### G. Initial stop            — where the thesis is wrong (not a round number).
### H. Targets & scaling        — how I take profit; matches the backtest's success label.
### I. Trade management         — what changes my mind while in; trailing.
### J. Time stop                — how long it gets to work.
### K. Exit checklist           — any-one-triggers list.
### L. Disqualifiers            — do-not-touch list.
### M. Catalysts                — what supercharges it.
### N. Data sources             — where I read each field.
### O. Pre-trade checklist      — paste-before-entry block.
### P. Journal & review         — per-trade row + weekly prompts.
### Q. Worked examples          — real, dated, annotated.
### R. Open questions           — what to calibrate.
```
