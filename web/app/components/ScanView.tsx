"use client";

// Main scan view (PLAN.md Track A parity): freshness header + §6.1 publication-lag caption +
// stale-data banner; stage buckets; ranked table (TradingView links, verdict emoji, progress
// bars); rescan control with live tail + failure banner. SWR refreshes at ~5s ONLY while a
// rescan is running (otherwise the table is static — the data only moves when a scan runs).

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR from "swr";
import type { ScanPayload, ScanRow, RescanStatus, Stage, Summary, Verdict } from "@/lib/types";
import {
  fetcher, tradingViewUrl, STAGE_ORDER, STAGE_BADGE, VERDICT_EMOJI,
  fmtYen, fmtNum, fmtInt, daysAgo,
} from "@/lib/api";
import Nav from "./Nav";
import Term from "./Term";

const STAGE_LABEL: Record<Stage, string> = {
  IGNITING: "🔴 IGNITING", PRIMED: "🟣 PRIMED", CROWDED: "🟠 CROWDED",
  NONE: "⚪ NONE", CONFIRMED: "🟢 CONFIRMED",
};
// Actionable stages on by default; NONE/CONFIRMED are background noise (toggle to reveal).
const DEFAULT_ON: Stage[] = ["IGNITING", "PRIMED", "CROWDED"];

function Stat({ label, value }: { label: React.ReactNode; value: React.ReactNode }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

/** 0..1 → filled bar. `tone` picks the accent colour. */
function Bar({ v, tone }: { v: number | null | undefined; tone?: "fuel" | "coil" }) {
  const pct = Math.max(0, Math.min(100, (v ?? 0) * 100));
  return (
    <>
      <span className={`bar${tone ? " " + tone : ""}`}><i style={{ width: `${pct}%` }} /></span>
      <span className="mono">{v == null ? "—" : v.toFixed(2)}</span>
    </>
  );
}

export default function ScanView({ initial, summary }: { initial: ScanPayload; summary: Summary | null }) {
  const router = useRouter();

  const { data: status, mutate: mutateStatus } = useSWR<RescanStatus>(
    "/api/rescan/status", fetcher,
    { refreshInterval: (d) => (d?.running ? 3500 : 0), revalidateOnFocus: true },
  );
  const running = !!status?.running;

  const { data: scan, mutate: mutateScan } = useSWR<ScanPayload>(
    "/api/scan", fetcher,
    { fallbackData: initial, refreshInterval: running ? 5000 : 0 },
  );

  // when a rescan finishes (running true→false) pull the fresh table once
  const prevRunning = useRef(running);
  useEffect(() => {
    if (prevRunning.current && !running) mutateScan();
    prevRunning.current = running;
  }, [running, mutateScan]);

  const [visible, setVisible] = useState<Set<Stage>>(new Set(DEFAULT_ON));
  const [query, setQuery] = useState("");

  const payload = scan ?? initial;
  const rows = payload.rows ?? [];
  const verdicts: Record<string, Verdict> = payload.verdicts ?? {};
  const counts = payload.counts ?? {};

  async function doRescan() {
    await fetch("/api/rescan", { method: "POST" }).catch(() => {});
    mutateStatus(); // flip running=true now → status poll starts
  }

  // --- empty state -------------------------------------------------------------------------------
  if (!payload.scan_date) {
    return (
      <div className="container">
        <h1>踏み上げ Scanner</h1>
        <div className="banner info">
          No scan in the store yet. Run <span className="mono">tse-scanner scan</span> or press 再スキャン.
        </div>
        <RescanBar running={running} status={status} onRescan={doRescan} />
      </div>
    );
  }

  const lag = payload.margin_as_of && payload.margin_published
    ? daysAgo(payload.margin_as_of) : null;
  const scanAge = daysAgo(payload.scan_date);
  const stale = scanAge >= 4; // a fresh scan should be today/yesterday (weekend = 3d)

  const q = query.trim().toLowerCase();
  const filtered = rows.filter((r) =>
    visible.has(r.stage) &&
    (!q || r.code.toLowerCase().includes(q) || (r.name ?? "").toLowerCase().includes(q)));

  // group into stage buckets, each sorted by score desc
  const buckets = STAGE_ORDER
    .filter((s) => visible.has(s))
    .map((s) => ({ stage: s, rows: filtered.filter((r) => r.stage === s).sort((a, b) => b.score - a.score) }))
    .filter((b) => b.rows.length > 0);

  return (
    <div className="container">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h1>踏み上げ Scanner</h1>
        <Nav />
      </div>
      <div className="subtitle">
        Crowded, trapped shorts + a wound coil → the upside break that runs them.
      </div>

      {/* freshness header */}
      <div className="metrics">
        <Stat label={<Term k="scan_date">Scan date</Term>} value={payload.scan_date} />
        <Stat label={<Term k="margin_asof">信用残 as of (Fri)</Term>} value={payload.margin_as_of ?? "—"} />
        <Stat label={<Term k="published">Published</Term>} value={payload.margin_published ?? "—"} />
        <Stat label={<Term k="candidates">Candidates</Term>} value={fmtInt(rows.length)} />
        {summary && <Stat label={<Term k="price_coverage">Price coverage</Term>} value={`${fmtInt(summary.price_codes)} codes`} />}
      </div>

      {/* §6.1 publication-lag caption */}
      {payload.margin_as_of && payload.margin_published && (
        <p className="caption">
          信用残 is the {payload.margin_as_of} (Fri close) print, first public {payload.margin_published}
          {lag != null && ` (${lag}-day lag)`}. The scan is point-in-time: it only consumes margin data
          available by its publish date — no look-ahead (§6.1).
        </p>
      )}

      {/* stale-data banner */}
      {stale && (
        <div className="banner warn">
          ⚠ Latest scan is <b>{scanAge} days</b> old ({payload.scan_date}). Press 再スキャン to refresh
          against the newest prices &amp; margin.
        </div>
      )}

      <RescanBar running={running} status={status} onRescan={doRescan} />

      {/* stage filter chips (= buckets, with live counts) */}
      <div className="filters">
        {STAGE_ORDER.filter((s) => (counts[s] ?? 0) > 0).map((s) => (
          <span
            key={s}
            className={`chip${visible.has(s) ? " on" : ""}`}
            onClick={() => setVisible((prev) => {
              const next = new Set(prev);
              next.has(s) ? next.delete(s) : next.add(s);
              return next;
            })}
          >
            {STAGE_LABEL[s]} {counts[s] ?? 0}
          </span>
        ))}
        <input
          type="text"
          placeholder="search code / 銘柄名"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ marginLeft: "auto", minWidth: 200 }}
        />
      </div>

      {/* ranked table, bucketed */}
      <div className="tablewrap">
        <table>
          <thead>
            <tr>
              <th className="l"><Term k="code">Code</Term></th>
              <th className="l"><Term k="name">銘柄</Term></th>
              <th className="l"><Term k="seg">Seg</Term></th>
              <th><Term k="score">Score</Term></th>
              <th><Term k="fuel">Fuel</Term></th>
              <th><Term k="coil">Coil</Term></th>
              <th><Term k="dtc">DTC</Term></th>
              <th><Term k="credit_ratio">信用倍率</Term></th>
              <th><Term k="short_total">売残</Term></th>
              <th><Term k="long_total">買残</Term></th>
              <th><Term k="bbw">BBw</Term></th>
              <th><Term k="close">終値</Term></th>
              <th><Term k="adv">ADV</Term></th>
              <th><Term k="tv">TV</Term></th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((b) => (
              <BucketRows key={b.stage} stage={b.stage} rows={b.rows} verdicts={verdicts} router={router} />
            ))}
            {buckets.length === 0 && (
              <tr><td className="l" colSpan={14} style={{ color: "var(--text-dim)", padding: 18 }}>
                No names match the current filters.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <p className="caption" style={{ marginTop: 14 }}>
        Click a row for candles, the coil box and the 信用残 history. Verdict emoji (💚/⚪/🔴) =
        a saved deep-research call. Not investment advice — a research surface, mechanical exits.
      </p>
    </div>
  );
}

function BucketRows({
  stage, rows, verdicts, router,
}: { stage: Stage; rows: ScanRow[]; verdicts: Record<string, Verdict>; router: ReturnType<typeof useRouter> }) {
  return (
    <>
      <tr>
        <td className={`l stagecell stage-${stage}`} colSpan={14}>
          <Term k={stage}>{STAGE_BADGE[stage]}</Term> · {rows.length}
        </td>
      </tr>
      {rows.map((r) => {
        const v = verdicts[r.code];
        const creditRatio = r.long_total && r.short_total ? r.long_total / r.short_total : r.credit_ratio;
        return (
          <tr key={r.code} onClick={() => router.push(`/${r.code}`)}>
            <td className="l mono">
              {v && <span title={v.one_liner ?? v.verdict}>{VERDICT_EMOJI[v.verdict] ?? ""} </span>}
              {r.code}
            </td>
            <td className="l">{r.name}</td>
            <td className="l" style={{ color: "var(--text-dim)" }}>{r.segment ?? "—"}</td>
            <td><Bar v={r.score} /></td>
            <td><Bar v={r.fuel} tone="fuel" /></td>
            <td><Bar v={r.coil} tone="coil" /></td>
            <td className="mono">{fmtNum(r.days_to_cover, 1)}</td>
            <td className="mono">{fmtNum(creditRatio, 2)}</td>
            <td className="mono">{fmtInt(r.short_total)}</td>
            <td className="mono">{fmtInt(r.long_total)}</td>
            <td className="mono">{fmtNum(r.bb_width_pct, 2)}</td>
            <td className="mono">{fmtInt(r.last_close)}</td>
            <td className="mono">{fmtYen(r.adv_yen)}</td>
            <td onClick={(e) => e.stopPropagation()}>
              <a href={tradingViewUrl(r.code)} target="_blank" rel="noreferrer">↗</a>
            </td>
          </tr>
        );
      })}
    </>
  );
}

function RescanBar({
  running, status, onRescan,
}: { running: boolean; status: RescanStatus | undefined; onRescan: () => void }) {
  const failed = !running && status?.failed_rc != null;
  return (
    <div style={{ margin: "14px 0" }}>
      <div className="row">
        <button onClick={onRescan} disabled={running}>
          {running ? <><span className="spin">⏳</span> 再スキャン中…</> : "🔄 再スキャン"}
        </button>
        {running && status?.last_log_line && (
          <span className="mono" style={{ color: "var(--text-dim)" }}>{status.last_log_line}</span>
        )}
      </div>
      {failed && (
        <div className="banner error" style={{ marginTop: 8 }}>
          再スキャン failed (rc={status?.failed_rc}). Last line:{" "}
          <span className="mono">{status?.last_log_line || "—"}</span> · full log:{" "}
          <span className="mono">data/rescan.log</span>
        </div>
      )}
    </div>
  );
}
