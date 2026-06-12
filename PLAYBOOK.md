# TSE Trading Playbook

**My personal playbook for trading the Tokyo Stock Exchange.** Living document — one entry per
setup, grown over time. This is the *human execution* layer; the scanner (`CLAUDE.md` + the code)
is the *detection* layer. The scanner finds candidates and buckets them by lifecycle stage; this
playbook says what I actually *do* with them — sizing, entry tactics, stops, targets, exits, and
the post-mortem.

Last updated: 2026-06-12 *(de-gated ladder, sweep trigger, F-02 revision setup, F-03 期日 idea,
glossary for the learning phase; references updated from the retired DESIGN.md to `CLAUDE.md`)*.

---

## 0. How to use this playbook

- **One setup = one entry**, built from the template in the appendix. Each entry is self-contained:
  I should be able to trade a name end-to-end from its entry alone.
- **Detection lives in `CLAUDE.md` (and ultimately the code); decisions live here.** Where a field
  is *measured* (days-to-cover, BB-width percentile, box, ignition), I link to `CLAUDE.md` rather
  than redefine it, so the two never drift. Where a *trade* is decided (size, where to buy, where
  to bail), it lives here.
- **`〔…〕` = I fill this in** (account size, risk %, my broker's quirks — nobody else can).
- **"Phase-1 tunable" = the backtest sets the number**, not my gut. Starting guesses mirror
  `config.py` (`Params` — the single source of truth). Until the event study passes its GO/NO-GO
  gate, every number here is a *hypothesis*.
- **Honesty rule:** if I don't know it yet, it says `〔TODO〕` — I do not pretend a number is proven.

### Setup lifecycle (graduation — a setup earns size, it isn't given it)

```
Idea  →  Building  →  Backtest-validated  →  Paper  →  Small size  →  Full size
                         ▲ event-study GO/NO-GO gate         ▲ my own live-trade review
```

A setup only moves right by clearing the gate to its left. Nothing trades at **Full size** before it
has survived the point-in-time event study *and* a paper + small-size live sample. No exceptions —
this is the rule that protects the account from a backtest that lied.

### Status legend

| Badge | Meaning |
|---|---|
| ⚪ Idea | Sketched, not specified |
| 🔵 Building | Specified, detection being built; **not tradeable yet** |
| 🟣 Backtest-validated | Passed the event study GO/NO-GO |
| 🟡 Paper / Small | Live but on paper or minimum size, gathering my own sample |
| 🟢 Live | Cleared to full size |
| 🔴 Retired | Edge decayed or disproven — kept for the lesson |

---

## 1. Glossary — the language of this playbook (and the dashboard)

I'm learning this domain; every term I had to look up goes here, in plain language. The dashboard
shows the same definitions as hover/tap tooltips.

### The margin tape (信用取引) — where the *fuel* is read

| Term | Meaning |
|---|---|
| **信用取引** (margin trading) | Buying or short-selling with money/shares borrowed through a broker. Japan **publishes the outstanding balances** — that public tape is this whole project's edge. |
| **制度信用** (system margin) | The exchange-standardised margin scheme: borrowable names are fixed, **positions must close within 6 months**, and shorting routes through JSF. Its data is public and reliable. |
| **一般信用** (general margin) | Broker-by-broker margin scheme, flexible terms, often no 逆日歩. Shorts here are *not* trapped the same way — a reason a "crowded" name can be fake fuel (see 優待クロス). |
| **貸借銘柄** (loan/taishaku issue) | A name where **institutional short-selling via 制度信用 is possible** (shares are borrowable through JSF). Only these can have a real squeeze trap — the scanner's universe is 貸借銘柄 only. |
| **売残 / 信用売残** (short balance) | Outstanding *short* margin positions — shares sold borrowed and not yet bought back. **Every one of them is a forced future buyer.** Published weekly, per name. |
| **買残 / 信用買残** (long balance) | Outstanding *long* margin positions — shares bought with borrowed money, i.e. future sellers. The counterweight to 売残. |
| **信用倍率** (credit ratio) | 買残 ÷ 売残. **Below 1.0 = more shorts than longs** (取り組み良好) — the crowd that can be squeezed. The scanner gates fuel on this. |
| **DtC / days-to-cover** | 売残 ÷ average daily volume = **how many full trading days of buying it would take for all shorts to exit**. High DtC = shorts are stuck in a narrow doorway. The scanner ranks it as a percentile within today's universe (`dtc_pctile`). |
| **逆日歩** (gyaku-hibu) | A **daily penalty fee shorts pay** when lendable shares run out (株不足). It appears/rises when shorting is genuinely crowded — and it bleeds shorts every day they hold, pressuring them to cover. Fires late: confirms, doesn't predict. |
| **株不足** | "Share shortage" — more shares borrowed than the lending pool holds; the condition that triggers 逆日歩. |
| **信用期日** (margin deadline) | The 6-month forced settlement date of a 制度信用 position. Shorts opened ~6 months ago **must** buy back around it — a calendar-predictable covering wave (Setup F-03 idea). |
| **優待クロス** | A retail arbitrage: short (usually 一般信用) + hold the stock to capture a shareholder perk (優待) with no price risk. Inflates 売残 **without trapped conviction shorts** — fake fuel. High 逆日歩-free / 一般-heavy short balance is the tell. |
| **publication lag** | The weekly margin print is **as-of Friday but published Tue–Wed**. I only ever act on the *published* number — trading Monday on Friday's print is acting on data that didn't exist yet (the backtest's silent killer). |

### Price / chart terms — where the *timing* is read

| Term | Meaning |
|---|---|
| **Coil** | Price wound tight: volatility compressed vs the name's own ~120-session normal **and** price boxed in a narrow range. A wound spring — the *when* of the trade. (Scanner column `Coil` = 0–1 tightness score.) |
| **BB width / BBw** | Bollinger-Band width — a volatility gauge. The scanner uses its **percentile vs the name's own 120-session baseline**; low percentile (e.g. ≤0.20) = unusually compressed = coiled. |
| **Box** | The tight high–low range price has been trapped in over the ~14-session setup window. **Box high = the breakout line; box low = where the setup is wrong.** Drawn on the detail chart. |
| **Ignition** | The trigger: the coil firing **upward**. Variants: **break** (close above box high + volume surge + wide bar), **spring** (fakeout below the box low first, then break up — premium), **sweep** (the reversal bar at the lows itself — earliest entry). |
| **Sweep** | Price pokes *below* the box low (running sell-stops, emboldening shorts) and **closes back inside with a long lower wick**. The trap snapping shut — entry at the reversal, stop under the wick low. |
| **ストップ高 / ストップ安** (limit-up/-down) | TSE daily price limits. A locked ストップ高 has buyers queued with **no sellers — you cannot buy it**. Best squeezes lock; the backtest assumes those fills are missed, and §F-01-E is my plan for it. |
| **比例配分** | Proportional lottery allocation of the few shares that do print on a locked limit board. Expect tiny partial fills. |
| **寄り成り** | Market-on-open order — the next-session entry option after a missed locked board (gap risk is mine). |
| **ADV** | Average daily volume/turnover (the scanner shows ¥ turnover). My liquidity floor: a squeeze I can't exit at size is a trap *for me*. |
| **WRB / wide-range bar** | A bar with unusually large high-low range — with volume, the signature of a real break vs a drift-over. |

### Scanner terms — the dashboard itself

| Term | Meaning |
|---|---|
| **Stage** | The lifecycle bucket: **CROWDED** (fuel present, not coiled — background watch) → **PRIMED** (coiled — active watchlist, pre-compute the trade) → **IGNITING** (coiled + confirmed upside break — **act**) → **CONFIRMED** (follow-through + 売残 dropping — lagging validation). NONE = nothing on. |
| **Fuel** | 0–1 score of *how crowded/trapped the shorts are* (DtC percentile, 倍率, 逆日歩, 売残 trend). **Since the 2026-06-12 de-gating it ranks names but doesn't gate stages** — whether fuel actually adds edge is what the event study measures. |
| **Score** | Transparent weighted blend of fuel + coil tightness + ignition quality, ranked **within** stage. No ML — I can always see *why* a name ranks. |
| **Seg** | TSE market segment: Prime (large), Standard (mid), Growth (small/speculative). Squeezes live mostly in mid/small caps. |
| **Code** | The 4-digit TSE ticker (銘柄コード), e.g. 7203 = Toyota. |
| **TV** | TradingView chart link. |
| **Verdict (💚/⚪/🔴)** | The deep-research memo's call on the *fundamentals*: SUPPORTS / NEUTRAL / OPPOSES the squeeze. **Display-only overlay** — it changes *my* size and exits, never the scanner's score. |
| **Squeeze-killer** | A fundamental event that nukes a squeeze regardless of positioning — 公募増資 (share offering), MSワラント (death-spiral CB/warrant), big lockup expiry. New supply feeds the shorts exactly what they need. |
| **増担保規制 / 日々公表** | Exchange cooling measures on overheated margin names (raised collateral / daily disclosure). Fuel-adders get squeezed out; often marks the local top — de-risk signal, and a disqualifier at entry. |
| **Event study** | The backtest: replay history, take every signal with only point-in-time data, label each by first-touch (+target/−stop/horizon), aggregate. **The GO/NO-GO gate every setup must pass.** |
| **First-touch triple-barrier** | The outcome label: from entry, whichever comes first — +target% (win), −stop% (loss), or the time horizon (scratch). Gap-aware: a bar that *opens* through a barrier fills at the open, not the barrier. |
| **Cohort split** | The event study's A/B answer: e.g. do fuel-rich breakouts beat fuel-less ones? Spring vs break vs sweep? That's how a signal *earns* its weight. |
| **R** | My risk unit: 1R = the ¥ lost if the initial stop is hit. Sizing, targets and the journal are all measured in R so trades compare across account sizes. |

---

## Setup index

| ID | Name | Dir. | Scanner stage → act on | Status | Edge in one line |
|---|---|---|---|---|---|
| **F-01** | Fumiage (踏み上げ) short squeeze / coil breakout | Long | `Igniting` (watch from `Primed`) | 🔵 Building — event study running | Crowded, trapped shorts read off the margin tape + a wound price coil → catch the upside break that runs them. |
| **F-02** | 上方修正先回り (guidance-raise anticipation) | Long | revision overlay on `Primed`/coil names | 🔵 Building — features built, study not run | Chronic sandbaggers with anomalous progress vs guidance, bought *before* the revision IR gaps the stock. |
| **F-03** | 期日スイング (margin-deadline swing) | Long | `Crowded` + deadline window | ⚪ Idea — not green-lit | 制度信用 shorts *must* close within 6 months — deadline waves are calendar-predictable forced buying. |

---

## Shared conventions

- **All times JST.** TSE cash session 09:00–11:30 / 12:30–15:00 (post-2024 close). Margin/JSF prints
  land **after** the close — see freshness below.
- **Risk is measured in R.** `1R = 〔my fixed risk per trade in ¥〕 = 〔X%〕 of account`. Every position is
  sized so that hitting the initial stop loses ~1R. P/L is journaled in R, not yen, so setups compare
  apples-to-apples across account sizes.
- **Account base:** `〔account size ¥〕` · **max risk live at once:** `〔N×R〕` · **max per name:** `〔1R〕`
  (squeeze names gap — see §F-01-F).
- **Data freshness is a first-class risk.** Weekly margin (信用取引残高) is *as-of Friday* but
  *published the following Tue–Wed* (`CLAUDE.md` → *Data & universe*, trap #1). I trade on the
  **published** number, never the as-of one. Fuel is therefore always stale by design; the **price
  trigger carries the freshness.**
- **Stages — de-gated ladder (2026-06-12, `CLAUDE.md` → *Architecture*):** `Crowded` (fuel, no coil)
  / `Primed` (coiled — fuel now *optional*, it ranks within the stage) / `Igniting` (coiled +
  confirmed break) / `Confirmed` (lagging — 売残 actually dropping). I *watch* from Primed and *act*
  on Igniting. **Why de-gated:** the original fuel∧coil∧break AND fired **once in 10 years** —
  untestable. Now every coil breakout is tradeable-in-principle and the event study's cohort splits
  measure whether the fuel-rich ones are *better* (the A/B that decides if fuel earns its weight).

---

## Setup F-01 — Fumiage (踏み上げ) short squeeze / coil breakout  ·  LONG  ·  🔵 Building

> **Not tradeable yet.** Detection is built; the 3y+10y daily event study is running as of
> 2026-06-12. First verdict at the original gates was **UNTESTABLE (n=1 in 10y)** — hence the
> de-gated ladder above. This entry is the trading plan I'll execute *once a cohort graduates* —
> written now so building the scanner and planning the trade stay coherent.

### A. Thesis & edge
Shorts are **crowded and trapped** — I read that directly off Japan's published margin tape instead of
*inferring* it from chart shape the way SMC/ICT traders do (and are often wrong about). When price is
also **wound into a tight coil** and then **breaks up**, the shorts are forced to cover; covering is
forced buying, which is self-reinforcing → 踏み上げ. Long-only: in a squeeze the trapped party is the
shorts, so the move is upside. (Full rationale: `CLAUDE.md` → *The thesis*.)

**Why this is an edge and not just momentum:** the *fuel* (trapped positioning) is observable and
quantified before the move, not guessed. **Honesty (2026-06-12):** whether fuel actually improves
the coil breakout is now an *open measured question* (cohort splits), not an assumption. If
fuel-rich breakouts don't outperform, the squeeze overlay dies and what remains is a plain
coil-breakout setup standing on its own numbers.

### B. When it works / regime
- Best in **risk-on / rotational tapes** where small-mid caps run; squeezes **cluster** (regime-dependent,
  lumpy). Expect droughts.
- **Mid/small caps**, not mega Prime — float has to be small enough that covering actually moves price.
- Tailwinds: a hot sector/theme, broad short-covering days, quarter-end / 信用期日 clustering (→ F-03).
- Headwind regime: heavy-tape selloffs (everything offered, breakouts fail) and dead-quiet drift (no
  trigger). When in doubt about regime, **demand more fuel**, not less.

### C. Watchlist criteria — what graduates a name to `Primed`
**Primed = coiled.** Fuel no longer gates the stage — it ranks names within it (de-gated ladder).
A name earns the active watchlist when the **coil** is on; the fuel block below tells me *which*
primed names carry squeeze potential (detection: `CLAUDE.md` → *Signals*):

- **Eligibility (hard gates):**
  - **貸借銘柄 only** — thesis requirement; 逆日歩 and a 制度信用 short-trap exist *only* here.
  - Turnover ≥ `〔¥100M/day, 20d avg〕` and price ≥ `〔¥100–300〕` — must be exitable (Phase-1 tunable).
  - Not 監理・整理, not ETF/REIT/ETN, has ≥120-session baseline.
- **Coil (the timing — this is what makes Primed):** BB-width ≤ `〔~20th pct〕` of the 120d baseline
  **and** price boxed over the ~14-session window. The spring is wound.
- **Fuel (the squeeze overlay — ranks, under test):**
  - **DtC percentile ≥ 0.90** within today's universe + absolute floor DtC ≥ 1.0 (replaced the old
    absolute DtC≥5, which sat above its own pool's 90th percentile and starved the sample).
  - **信用倍率 < 1.0** (more shorts than longs; 取り組み良好).
  - Boosters (weights *earned* in the backtest, not assumed): 逆日歩 present/rising; 信用売残 rising
    across the ~3 weekly prints in the setup window.
  - **優待クロス check:** 一般信用-heavy 売残 with no 逆日歩 = fake fuel — discount it.

If coiled → **Primed**: on the live watchlist, pre-compute the trade (box high/low, intended stop,
size) so I'm not doing math when it triggers. High-fuel Primed names are the priority watches.

**Also pre-run the fundamental memo at Primed** (`tse-scanner research <code>` or the dashboard 🔬
button): the bear case, dilution risk and catalyst calendar take ~10 minutes to research and zero
minutes to read at the trigger. Research at Primed, not at Igniting — the break won't wait for a
memo. The verdict is a **discretionary overlay**: it never changes the scanner's stage/score, it
changes *my* size, hold and exit rules (§D, §H, §O).

### D. Trigger / entry — `Igniting`
Act only on a **confirmed** ignition (detection: `CLAUDE.md` → *Signals → Ignition*). Three
variants, ordered by confirmation (and inversely by entry price):

1. **Break (standard):** **close** above the box high **+** volume surge `〔≥2× 20d〕` **+** wide,
   band-expanding bar. Close — not an intraday poke; volume + width reject fakeouts.
2. **Spring (premium tag):** price first **dips below the box low** (sweeps sell-side stops →
   validates shorts, who *add*), then **reclaims and breaks up**. Maximal trap; cleanest version,
   ranks higher.
3. **Sweep (early entry — added 2026-06-12):** the sweep-**reversal bar itself** — low takes out
   the box low, close reclaims it, long lower wick (≥50% of range, close in the upper 40%). Entry
   at the reversal instead of waiting for the box-high break: **better price, less confirmation.**
   Whether the earlier entry pays for its lower confirmation is an event-study cohort question
   (sweep vs break vs spring) — until that answer is in, sweep trades are `〔TODO: paper-only /
   half-size of a break entry〕`. A same-bar sweep-and-break counts as a **break**.

I do **not** anticipate the break (no buying inside the box hoping). Sweep entry is *not*
anticipation — the reversal bar is itself a confirmed event (the trap snapping shut), just an
earlier and weaker one than the break.

**Fundamental gate at the trigger** (memo from §C):
- **🔴 OPPOSES** — intact bear thesis or a live squeeze-killer (公募増資/MSワラント/CB overhang):
  either pass, or take it **mechanically only** — reduced size, sell the *full* position at target,
  no runner. A cash-hungry company sells into its own squeeze.
- **⚪ NEUTRAL** — trade the technicals exactly as written; fundamentals neither pay nor charge.
- **💚 SUPPORTS** — broken bear case / upward-revision streak / buyback: full size within §F caps,
  and §H's runner rules get more rope.
- **No memo / stale memo** = NEUTRAL, not "skip the check next time."

### E. Entry tactics — the stop-high reality
The best squeezes lock **ストップ高 (limit-up)** and **you cannot buy a locked board**. So the
trigger firing ≠ a fill. My rules, in order:

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
- **Never** market into an illiquid, already-extended print (slippage + 提灯 exhaustion — see §L).

> `〔Pick my default among 1–4 above and commit to it. Starting default: 1 (stand down) + 4 (retest).〕`

### F. Position sizing & risk
- **Size from the stop, not the conviction:** `shares = 1R ÷ (entry − initial_stop)`, rounded to a
  TSE lot (`〔100-share units〕`).
- **Gap down-size:** squeeze names gap *both* ways and stops can blow through. When the entry→stop
  distance is small (tight box, or a sweep's structural stop), the formula wants a *big* position —
  **cap it** at `〔max ¥ per name / max % of ADV so I can actually exit〕`. Liquidity caps size
  before the math does.
- **One squeeze ≈ 1R of risk.** Don't let three correlated squeezes in one hot theme = 3R of the same bet.
- `〔My exact risk-per-trade %, account size, and per-name ¥ cap — fill in.〕`

### G. Initial stop — thesis invalidation
The stop is **where the setup is wrong**, not a round number:
- **Break variant:** below the **box low** (the coil failed / break was a fakeout). Starting guess
  −`〔6%〕` (Phase-1 tunable).
- **Spring variant:** below the **spring low** (the sweep low) — if that breaks, it wasn't a sweep, it
  was a breakdown.
- **Sweep variant:** **structural stop under the reversal bar's wick low** (floored ~2% so a
  hairline wick doesn't produce silly size). Same logic: a close back under the wick low means the
  "trap" was actually a breakdown in progress. The backtest's labels use this same per-event stop,
  with the target scaled to keep the R:R — live trades match the label.
- **Reality:** in a gap-through, my fill is worse than my stop. I size (§F) assuming the stop can slip;
  I never assume a clean exit at the line.

### H. Targets & scaling out
Squeezes **spike and fade** — I plan to *sell into strength*, not round-trip it:
- **First-touch target** +`〔12%〕` within ~`〔10〕` sessions is the backtest's win label (first-touch
  triple-barrier; sweep events use the per-event R:R-scaled equivalent). I trade to match the label:
  **scale out into the spike.**
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
  runner; but it *fires late* — it confirms, it doesn't initiate.
- **増担保規制 / 日々公表 announced mid-trade** = exchange is cooling it; fuel-adders get
  margin-called, and this often marks the local top. **De-risk / take the rest.**
- **Trail** behind higher box / prior-bar lows once it's run `〔+1R〕`; never let a winner round-trip to a loss.

### J. Time stop
Squeezes work **fast or not at all.** If price hasn't moved meaningfully within `〔~10〕` sessions of entry
(the first-touch horizon), the ignition failed → **exit at market**, take the scratch, recycle the risk.
A squeeze that sits is a squeeze that isn't.

### K. Exit checklist (any one → act)
- ☐ +target tag → scale out per §H
- ☐ Initial stop / box-low (spring-low / sweep-wick-low) breached → out
- ☐ Time stop hit (no move in `〔~10〕` sessions) → out
- ☐ 増担保規制 / 日々公表 imposed → de-risk to runner or flat
- ☐ Parabolic / multi-ストップ高 exhaustion + 提灯 crowd → sell into it, don't hold for "more"

### L. Disqualifiers — do not touch
- **一般信用-only (not 貸借銘柄)** → no 逆日歩, no system short-trap → **no thesis**.
- **増担保規制 already on / 日々公表 flagged** → fuel is being actively drained; the easy part is over.
- **監理・整理 post** → delisting risk, not a squeeze.
- **Too liquid / huge float** → covering can't move it; this is a Prime-megacap mistake.
- **Earnings / binary event inside the horizon** → unless *that's* the catalyst (→ F-02), the gap is
  a coin-flip, not a squeeze.
- **Already extended / 提灯 exhausted** → several ストップ高 already, parabolic, retail piled in → I'm late; this is exit liquidity.
- **優待クロス-inflated 売残** → the "shorts" are hedged perk-farmers who feel no pain; fake fuel.
- **Turnover below the floor** → I can't get out; a squeeze I can't exit is a trap *for me*.

### M. Catalysts that supercharge the cover
The fuel is potential; a catalyst lights it. Bigger edge when the ignition coincides with:
- **逆日歩 spike** (株不足 — lendable shares run out) — shorts bleed daily carry.
- **信用期日 approaching** — 制度信用 settles in **6 months**; positions opened ~6mo ago *must* cover (→ F-03).
- **Earnings beat / 上方修正 (guidance raise — → F-02) / 自社株買い (buyback) / index inclusion / activist** — a reason for the first leg up that makes shorts blink.

### N. Where I read each field
| Field | Source |
|---|---|
| Bear case, dilution risk, catalysts, float | `tse-scanner research <code>` memo (🔬 button) · 株探 · IR BANK · karauri.net |
| 信用売残 / 買残 / 倍率, days-to-cover | Scanner (J-Quants weekly margin) · broker screen · 株探 |
| 逆日歩, daily 貸借残, 株不足 | 日証金 (JSF) · トレーダーズ・ウェブ · 株探 |
| Coil / box / volume / break | Scanner + chart |
| 増担保規制 / 日々公表 / 監理・整理 | JPX/TSE notices · broker alerts |
| 信用期日 | Computed from open date (制度=6mo) · broker position screen |

### O. Pre-trade checklist (paste before every entry)
```
Code / name:                         Stage: Primed → Igniting?  [ ]
貸借銘柄?                       [ ]   Variant: break / spring / sweep
DtC pctile ≥ 0.90 (floor ≥1)?  [ ]   信用倍率 < 1?              [ ]
逆日歩 present/rising?          [ ]   売残 rising 3 prints?      [ ]
優待クロス discount applied?    [ ]   BB-width ≤ pctile? boxed?  [ ]
Confirmed trigger? (break: close>box high+vol+wide · sweep: reclaim+wick) [ ]
NO 増担保 / 日々公表 / 監理?    [ ]   No earnings in horizon?    [ ]
Research memo (≤5d old): verdict 💚/⚪/🔴 ___  squeeze-killers? ___  catalyst in horizon? ___
Box high ___  box low ___  entry ___  stop ___ (sweep: wick low ___)  size(1R) ___ shares
Stop-high plan if it locks: ___ (default: stand down + retest)
Target ___  time stop ___ sessions   Risk = 1R = ¥___ (___% acct)
```

### P. Journal & review
Log **every** trade (and every *passed* trigger — the misses teach the most). One row per trade:

| Date | Code | Name | Variant | Stage@entry | 売残 / 倍率 / DtC pctile / 逆日歩 | BBpct / box H-L | Entry | Stop | Size (R) | Exits | P/L (R) | Label (win/loss/scratch) | Lesson |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

**Label = first-touch** (matches the backtest): +target first = win, −stop first = loss, neither = scratch.

**Weekly review prompts:**
- Did `Primed` names actually progress to `Igniting`? (this *is* the live validation by-product)
- Win-rate × payoff in R — is realized expectancy tracking the backtest, or diverging? (divergence = the backtest lied or regime shifted)
- Did the *fuel-rich* names do better than the fuel-less ones? (live echo of the cohort A/B)
- Which disqualifier did I ignore when I lost?
- Stop-high: how often did I miss the clean fill, and did my rule (§E) cost or save R?

### Q. Worked examples
`〔Add 2–3 real, dated examples once the study/live surfaces them — one clean break, one spring,
one sweep, one failure. Screenshot the coil + the 売残 trend + the break; annotate entry/stop/exit
and the realized R.〕`

### R. Setup-specific open questions to calibrate
- **The cohort A/B (the GO/NO-GO):** do fuel-rich coil breakouts outperform fuel-less ones? If not,
  fuel dies as an overlay and this becomes a plain coil-breakout setup (or dies entirely).
- **Sweep vs break vs spring:** does the sweep's better entry price beat its weaker confirmation?
  Does spring out-earn break enough to wait for it?
- Best **DtC-percentile** and **BB-width** cutoffs (threshold grid sweep over the events parquet).
- Real **publication latency** of weekly margin, and whether **日証金 daily** fuel is worth wiring in
  for fresher edge.
- My personal **stop-high rule** (§E) — which of options 1–4 keeps the most R live.

---

## Setup F-02 — 上方修正先回り (guidance-raise anticipation)  ·  LONG  ·  🔵 Building

> **Not tradeable yet — detection features exist (`signals/revision.py`), the event study hasn't
> run.** Foundation built 2026-06-12; the fins backfill and the two study arms are queued.

### A. Thesis & edge
Japanese companies guide conservatively and **revise upward mid-year** (上方修正). The IR lands
**after the close → the stock gaps** → reacting is too late; the gap eats the move. The edge is
**anticipation**: identify chronic sandbaggers whose *interim progress* (進捗率 — % of full-year
guidance already earned) is running anomalously hot vs their own seasonal norm, inside the calendar
window where they historically file — and be long *before* the print. Squeeze tie-in: an 上方修正
landing on a fuel-rich coil is exactly the §F-01-M catalyst that lights the cover.

### B. When it works / regime
- `〔TODO — event study. Prior: works best mid-fiscal-year (Q2/Q3 disclosure windows), in names
  with a multi-year lowball-guidance habit.〕`

### C. Watchlist criteria
Probability features (built, weights TBD): **sandbagger score** (history of beating own guidance),
**進捗率 anomaly** vs the name's own seasonality, **lowball guidance** vs trailing actuals, inside
the **announcement-anniversary window** (the name's own historical filing dates ±buffer — forward
calendars don't exist point-in-time, so anniversaries proxy them).

### D–K. Trigger / entry / sizing / stops / exits
Two candidate arms, to be settled by the event study:
1. **Reactive baseline:** buy next open *after* the 修正 — measures how much gap eats the move
   (the strawman the anticipatory arm must beat).
2. **Anticipatory:** revision-probability high + coil (or sweep) trigger *before* the window,
   **time-stop if no filing lands** within it `〔sessions TODO〕`.
`〔Everything else TODO until the study runs — honesty rule.〕`

### L. Disqualifiers
- Already revised this period (spent catalyst) · guidance already aggressive (no sandbag) ·
  進捗率 hot because of a one-off (asset sale) — read the memo, not just the ratio.

### R. Open questions
- Does anticipation beat the reactive baseline after the gap? By enough to carry the time-stop scratches?
- Do revision-probability quartiles split coil-breakout outcomes (i.e. is this also an F-01 ranking feature)?
- Right anniversary-window width; op→odp fallback handling for names that guide oddly.

---

## Setup F-03 — 期日スイング (margin-deadline swing)  ·  LONG  ·  ⚪ Idea

> **Sketch only — not specified, not green-lit.** Parked here so the thesis isn't lost.

**Thesis:** 制度信用 positions must settle within **6 months** (信用期日). A wave of shorts opened
into a peak therefore *must* buy back ~6 months later — **forced covering on a calendar**. Two
variants: **K1 期日向かい** (long into an approaching short-deadline wave, composing deadline-DtC
with a coil) and **K2 期日明け** (the relief after a *long*-side deadline bulge expires — the
overhang clears).

**Mechanics:** FIFO vintage decomposition of weekly sys_short/sys_long deltas → estimate the
maturity calendar per name. Verified feasible on the stored tape (sys/neg split populated all 507
weeks).

**Honest caveats (why this is still ⚪):** rolls reset the clock (close + reopen = new 6 months),
現渡し (delivery against stock held) settles without market buying, and the weekly print blurs
open-dates by ±1 week — all three degrade the vintage estimate. Quiet right now (2026-06: only 32
names with meaningful due-soon loads); the **Mar-2026 short build-up — #2 of the decade — matures
~late Sep 2026**, which is the natural live test window *if* the event study validates the idea
first.

---

## Appendix — blank setup template (copy for the next setup)

```markdown
## Setup 〔ID〕 — 〔Name〕  ·  〔LONG/SHORT〕  ·  〔status badge〕

### A. Thesis & edge          — why this works; why it's edge, not noise. Link CLAUDE.md if detected there.
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
