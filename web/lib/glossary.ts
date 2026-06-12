// One-line definitions behind every jargon term in the UI (hover / tap any dotted-underlined
// label). Written for the learning trader; the 📕 Playbook tab's Glossary section holds the
// longer versions. Keys are stable ids — components reference them via <Term k="...">.

export interface GlossEntry {
  label: string;      // English label shown in the tooltip header
  ja?: string;        // Japanese term, when there is one
  def: string;        // 1–3 plain-language sentences
}

export const GLOSSARY: Record<string, GlossEntry> = {
  // --- the margin tape (fuel side) ---------------------------------------------------------------
  fuel: {
    label: "Fuel",
    def: "How crowded and trapped the short-sellers are (0–1), read from Japan's published weekly " +
      "margin data — days-to-cover percentile, credit ratio, 逆日歩. Every trapped short is a forced " +
      "future buyer; that buying is what powers a squeeze. Ranks names, no longer gates stages.",
  },
  dtc: {
    label: "DtC — days-to-cover",
    ja: "踏み上げ余地",
    def: "Short balance ÷ average daily volume = how many full days of buying it would take for all " +
      "shorts to exit. High = shorts are stuck in a narrow doorway. Gated by percentile within " +
      "today's universe (top 10%), not by an absolute number.",
  },
  credit_ratio: {
    label: "Credit ratio",
    ja: "信用倍率",
    def: "Margin longs ÷ margin shorts (買残 ÷ 売残). Below 1.0 means MORE shorts than longs — the " +
      "crowd that can be squeezed. The fuel gate wants < 1.0 (取り組み良好).",
  },
  short_total: {
    label: "Short balance",
    ja: "信用売残",
    def: "Outstanding margin short positions (shares sold borrowed, not yet bought back). Published " +
      "weekly per name. Every one of these shares must eventually be bought back.",
  },
  long_total: {
    label: "Long balance",
    ja: "信用買残",
    def: "Outstanding margin long positions (shares bought with borrowed money) — future sellers, " +
      "the counterweight to the short balance.",
  },
  margin_balance: {
    label: "Margin balances",
    ja: "信用残",
    def: "The weekly published tape of outstanding margin shorts (売残) and longs (買残) per name — " +
      "the data this whole scanner is built on. As-of Friday, published the following Tue–Wed.",
  },
  margin_asof: {
    label: "Margin as-of date",
    ja: "信用残 基準日",
    def: "The Friday the margin print measures. NOT when it became public — that's the published " +
      "date, 2–3 business days later. The scan only ever uses prints by their published date.",
  },
  published: {
    label: "Published date",
    def: "When the weekly margin print actually became public (Tue–Wed after its Friday as-of). " +
      "Trading on a print before this date = acting on data that didn't exist — the classic " +
      "backtest lie this project refuses to commit.",
  },
  gyakuhibu: {
    label: "Gyaku-hibu",
    ja: "逆日歩",
    def: "A daily penalty fee shorts must pay when lendable shares run out (株不足). Its presence " +
      "means shorting is genuinely crowded — and it bleeds shorts every day, pressuring them to " +
      "cover. Fires late: it confirms a squeeze, it doesn't predict one.",
  },
  taishaku: {
    label: "Loan issue",
    ja: "貸借銘柄",
    def: "A stock where institutional short-selling through the standardized margin system is " +
      "possible. Only these names can have a real short-trap (and 逆日歩) — the scanner's universe " +
      "is 貸借銘柄 only.",
  },

  // --- price / chart (timing side) ----------------------------------------------------------------
  coil: {
    label: "Coil",
    def: "How tightly price is wound (0–1): volatility compressed vs this name's own ~120-session " +
      "normal AND price stuck in a narrow box for ~14 sessions. A wound spring — the timing half " +
      "of the setup. Coil is what makes a name PRIMED.",
  },
  bbw: {
    label: "BB width percentile",
    def: "Bollinger-Band width (a volatility gauge) ranked against this name's own trailing ~120 " +
      "sessions. 0.10 = tighter than 90% of its own recent history = compressed. Low = coiled.",
  },
  box: {
    label: "Box",
    def: "The tight high–low range price has been trapped in over the setup window (~14 sessions). " +
      "Box high = the breakout trigger line; box low = where the setup is wrong (the stop region). " +
      "Drawn in purple on the candle chart.",
  },
  ignition: {
    label: "Ignition",
    def: "The trigger: the coil firing upward. Variants — break (close above box high + volume " +
      "surge + wide bar), spring (fakeout below the box first, then break up — premium), sweep " +
      "(the reversal bar at the lows itself — earliest entry, stop under the wick).",
  },
  score: {
    label: "Score",
    def: "Transparent weighted blend of fuel + coil tightness + ignition quality, ranked within " +
      "each stage. No machine learning — you can always see why a name ranks where it does.",
  },

  // --- stages -------------------------------------------------------------------------------------
  stage: {
    label: "Stage",
    def: "Where the name sits in the setup lifecycle: CROWDED (fuel, not coiled) → PRIMED (coiled " +
      "— watchlist) → IGNITING (coiled + confirmed break — act) → CONFIRMED (shorts visibly " +
      "covering — lagging validation).",
  },
  IGNITING: {
    label: "IGNITING — act",
    def: "Coiled AND a confirmed upside break (close above box high + volume surge + wide bar). " +
      "This is the actionable stage — the playbook's entry rules apply now. Spring/sweep variants " +
      "rank higher.",
  },
  PRIMED: {
    label: "PRIMED — watchlist",
    def: "The coil is on: volatility compressed + price boxed. Pre-compute the trade now (box " +
      "levels, stop, size) and pre-run 🔬 research — the break won't wait. Fuel is optional here; " +
      "high-fuel PRIMED names are the priority watches.",
  },
  CROWDED: {
    label: "CROWDED — background",
    def: "Squeeze fuel is present (top-decile days-to-cover, more shorts than longs) but price " +
      "isn't coiled yet. Background watch — no timing edge yet, just trapped positioning.",
  },
  CONFIRMED: {
    label: "CONFIRMED",
    def: "Post-break follow-through and the short balance actually dropping on the next weekly " +
      "print = shorts covering. Lagging by design — validation, not an entry.",
  },
  NONE: {
    label: "NONE",
    def: "Nothing on — neither meaningful fuel nor a coil. The bulk of the universe on any day.",
  },

  // --- table columns / misc -----------------------------------------------------------------------
  code: {
    label: "Code",
    ja: "銘柄コード",
    def: "The 4-digit TSE ticker (e.g. 7203 = Toyota). The emoji in front, when present, is a saved " +
      "deep-research verdict: 💚 supports the squeeze · ⚪ neutral · 🔴 fundamentals oppose it.",
  },
  name: {
    label: "Name",
    ja: "銘柄",
    def: "The company name. Click any row to open candles, the coil box, the margin-balance " +
      "history and deep research for that name.",
  },
  seg: {
    label: "Segment",
    def: "TSE market segment: Prime (large caps), Standard (mid), Growth (small/speculative). " +
      "Squeezes live mostly in mid/small caps — float small enough that forced covering moves price.",
  },
  close: {
    label: "Last close",
    ja: "終値",
    def: "Latest daily closing price in the store (¥).",
  },
  adv: {
    label: "ADV — avg daily value",
    ja: "売買代金",
    def: "Average daily traded value (¥). The liquidity floor: a squeeze you can't exit at size is " +
      "a trap for you. Universe requires roughly ≥¥100M/day.",
  },
  tv: {
    label: "TradingView",
    def: "Opens the chart on TradingView in a new tab.",
  },
  candidates: {
    label: "Candidates",
    def: "How many names the scan evaluated after universe filters (貸借銘柄 only, liquidity floor, " +
      "no ETF/REIT, not under exchange supervision).",
  },
  price_coverage: {
    label: "Price coverage",
    def: "How many codes have price bars in the local store — a data-completeness gauge, not a " +
      "trading signal.",
  },
  scan_date: {
    label: "Scan date",
    def: "The trading day this table reflects. Stages and scores are computed with only data " +
      "published by this date (point-in-time discipline).",
  },

  // --- research -----------------------------------------------------------------------------------
  research: {
    label: "Deep research",
    def: "An on-demand AI research memo for one name: reconstructs why the shorts are short, hunts " +
      "squeeze-killers (dilution!), maps catalysts, and returns SUPPORTS / NEUTRAL / OPPOSES. " +
      "Display-only — it informs YOUR sizing and exits, never the scanner's score.",
  },
  squeeze_killers: {
    label: "Squeeze-killers",
    def: "Fundamental events that nuke a squeeze regardless of positioning — a share offering " +
      "(公募増資), death-spiral warrants (MSワラント), big lockup expiry. New supply hands the " +
      "shorts exactly the shares they need. Any live one = pass or mechanical-only.",
  },
  catalysts: {
    label: "Catalysts",
    def: "Dated upcoming events (決算, index reviews, buyback windows…). 🎯 = inside the trade " +
      "horizon — decide BEFORE entry whether you hold through that binary print or exit in front.",
  },
  analyst_targets: {
    label: "Analyst targets",
    def: "Broker price-target cluster. Useful as the exit ceiling — squeezes often die where the " +
      "street says fair value is. ⛰️ marks the highest.",
  },

  // --- jobs ---------------------------------------------------------------------------------------
  backtest: {
    label: "Backtest (event study)",
    def: "Replays history day by day: every signal is taken with only data available that day, " +
      "labeled by first-touch (+target = win / −stop = loss / timeout = scratch), then aggregated. " +
      "The GO/NO-GO gate every setup must pass before live money.",
  },
  ingest: {
    label: "Ingest",
    def: "Data sync: weekly margin prints, daily price bars, TOPIX, listed-company master, " +
      "disclosures (J-Quants / JPX). 再スキャン = ingest fresh data, then re-run the scan.",
  },
  research_job: {
    label: "Research job",
    def: "A running deep-research memo (one per ticker) — a headless AI agent browsing public " +
      "sources; the memo lands on the name's detail page when done.",
  },
  service: {
    label: "Service",
    def: "The dashboard's own processes (API server, web server) — infrastructure logs, not " +
      "trading jobs.",
  },
  batch: {
    label: "Batch",
    def: "Any other long-running command-line run that wrote a log under data/.",
  },
  replay: {
    label: "Replay",
    def: "One simulated historical trading day inside a backtest — '432/731' means 432 of 731 " +
      "days replayed. 'Events' counts the trade signals found so far.",
  },
};
