// Mirrors the FastAPI payloads (src/tse_scanner/api.py) — keep the two in sync by hand;
// the API is ours and one process away.

export type Stage = "IGNITING" | "PRIMED" | "CROWDED" | "CONFIRMED" | "NONE";

export interface ScanRow {
  scan_date: string;
  code: string;
  name: string;
  segment: string | null;
  stage: Stage;
  score: number;
  fuel: number;
  coil: number;
  ignition: string;
  days_to_cover: number | null;
  credit_ratio: number | null;
  short_total: number | null;
  long_total: number | null;
  bb_width_pct: number | null;
  box_high: number | null;
  box_low: number | null;
  // technical exit map (signals/targets.py) — display-only overlay, never a score input
  t1: number | null;
  t2: number | null;
  atr: number | null;
  rr_t1: number | null;
  next_resistance: number | null;
  high_52w: number | null;
  blue_sky: boolean | null;
  last_close: number | null;
  adv_yen: number | null;
  margin_as_of: string | null;
  margin_published: string | null;
}

export interface Verdict {
  code: string;
  research_date: string;
  verdict: "SUPPORTS" | "NEUTRAL" | "OPPOSES";
  confidence: string | null;
  one_liner: string | null;
}

export interface ScanPayload {
  scan_date: string | null;
  margin_as_of: string | null;
  margin_published: string | null;
  counts: Partial<Record<Stage, number>>;
  rows: ScanRow[];
  verdicts: Record<string, Verdict>;
}

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  turnover: number | null;
}

export interface MarginPrint {
  code: string;
  as_of_date: string;
  published_date: string | null;
  short_total: number | null;
  long_total: number | null;
  neg_short: number | null;
  sys_short: number | null;
  neg_long: number | null;
  sys_long: number | null;
}

export interface RescanStatus {
  running: boolean;
  started_at: number | null;
  last_log_line: string;
  failed_rc: number | null;
}

export interface ResearchRow {
  code: string;
  research_date: string;
  status: string;
  verdict: string | null;
  confidence: string | null;
  one_liner: string | null;
  squeeze_killers: string | null; // JSON array (text)
  catalysts: string | null;       // JSON array of {date, event, inside_horizon} (text)
  analyst_targets: string | null; // JSON array of {house, target, rating, date} (text)
  sector_view: string | null;     // JSON {trend: tailwind|neutral|headwind, one_liner} (text)
  sources: string | null;         // JSON array (text)
  memo_md: string | null;
  model: string | null;
  cost_usd: number | null;
  num_turns: number | null;
  duration_s: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  pid: number | null;             // wrapper PID while running (server-side liveness check)
}

export interface ResearchPayload {
  code: string;
  state: "none" | "running" | "failed" | "done";
  row: ResearchRow | null;
}

export interface Summary {
  prices: number;
  price_codes: number;
  margin_rows: number;
  listed: number;
  scans: number;
  margin_as_of: string | null;
  margin_published: string | null;
}

export interface PlaybookPayload {
  markdown: string;
  mtime: number;
  updated: string; // YYYY-MM-DD of the file's last edit
}

// --- jobs (GET /jobs, /jobs/log) — long-running batch visibility ---

export interface JobProgress {
  done: number;
  total: number;
  pct: number | null;
  events?: number;   // backtest replays report a running event count
  detail: string;    // the raw progress line
}

export interface Job {
  id: string;        // log path relative to data/ — pass to /jobs/log?id=
  label: string;
  kind: "backtest" | "ingest" | "research" | "service" | "batch";
  running: boolean;
  status: "running" | "done" | "failed" | "idle";
  progress: JobProgress | null;
  last_line: string;
  mtime: number;     // unix seconds
  age_s: number;
  size: number;      // bytes
  pids: number[];
}

export interface JobProcess {
  pid: number;
  elapsed_s: number;
  cmd: string;
  kind: string;
}

export interface JobsPayload {
  jobs: Job[];
  processes: JobProcess[];
  ts: number;
}

export interface JobLogPayload {
  id: string;
  lines: string[];
  size: number;
  mtime: number;
  running: boolean;
}
