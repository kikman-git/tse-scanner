import Link from "next/link";
import { apiGet, tradingViewUrl, STAGE_BADGE, fmtYen, fmtNum, fmtInt } from "@/lib/api";
import type { ScanPayload, ScanRow, ResearchPayload, Bar, MarginPrint } from "@/lib/types";
import PriceChart from "../components/PriceChart";
import MarginChart from "../components/MarginChart";
import ResearchPanel from "../components/ResearchPanel";
import Term from "../components/Term";

export const dynamic = "force-dynamic";

export default async function CodePage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;

  const [scan, prices, margin, research] = await Promise.all([
    apiGet<ScanPayload>("/scan").catch(() => null),
    apiGet<{ code: string; bars: Bar[] }>(`/prices/${code}?sessions=300`).catch(() => ({ code, bars: [] })),
    apiGet<{ code: string; prints: MarginPrint[] }>(`/margin/${code}?prints=30`).catch(() => ({ code, prints: [] })),
    apiGet<ResearchPayload>(`/research/${code}`).catch(() => ({ code, state: "none" as const, row: null })),
  ]);

  const row: ScanRow | undefined = scan?.rows.find((r) => r.code === code);
  const bars = prices.bars ?? [];
  const last = bars[bars.length - 1];
  const analystTp = research.state === "done" ? consensusTp(research.row?.analyst_targets ?? null) : null;

  return (
    <div className="container">
      <Link href="/" className="caption">← all candidates</Link>

      <div className="row" style={{ justifyContent: "space-between", marginTop: 6 }}>
        <div>
          <h1 style={{ marginBottom: 2 }}>
            <span className="mono">{code}</span> {row?.name ?? ""}
          </h1>
          <div className="subtitle" style={{ marginBottom: 0 }}>
            {row ? (
              <span className={`stage-${row.stage}`} style={{ fontWeight: 600 }}>
                <Term k={row.stage}>{STAGE_BADGE[row.stage]}</Term>
              </span>
            ) : "not in the latest scan"}
            {row?.segment && <span style={{ color: "var(--text-dim)" }}> · {row.segment}</span>}
            {last && <span style={{ color: "var(--text-dim)" }}> · 終値 {fmtInt(last.close)} ({last.date})</span>}
          </div>
        </div>
        <a href={tradingViewUrl(code)} target="_blank" rel="noreferrer">TradingView ↗</a>
      </div>

      {row && (
        <div className="metrics">
          <Metric label={<Term k="score">Score</Term>} value={fmtNum(row.score, 3)} />
          <Metric label={<Term k="fuel">Fuel</Term>} value={fmtNum(row.fuel, 2)} />
          <Metric label={<Term k="coil">Coil</Term>} value={fmtNum(row.coil, 2)} />
          <Metric label={<Term k="dtc">Days-to-cover</Term>} value={fmtNum(row.days_to_cover, 1)} />
          <Metric label={<Term k="credit_ratio">信用倍率</Term>} value={fmtNum(row.credit_ratio, 2)} />
          <Metric label={<Term k="bbw">BB width</Term>} value={fmtNum(row.bb_width_pct, 2)} />
          <Metric label={<Term k="adv">ADV</Term>} value={fmtYen(row.adv_yen)} />
        </div>
      )}

      <div className="chartbox">
        <div className="title">
          Daily candles · volume · <Term k="box">coil box</Term>
          {(row?.box_low || row?.box_high) && (
            <span style={{ color: "var(--purple)" }}> ({fmtInt(row?.box_low)} – {fmtInt(row?.box_high)})</span>
          )}
          {analystTp != null && (
            <span style={{ color: "var(--orange)" }}> · <Term k="analyst_targets">analyst TP</Term> {fmtInt(analystTp)}</span>
          )}
        </div>
        <PriceChart bars={bars} boxHigh={row?.box_high} boxLow={row?.box_low} t1={row?.t1} t2={row?.t2} analystTp={analystTp} />
      </div>

      {row?.t1 != null && (
        <div className="chartbox">
          <div className="title">
            <Term k="exit_map">Technical exit map</Term>{" "}
            <span className="caption">· price-structure ceilings — reconcile with the analyst cluster below</span>
          </div>
          <div className="metrics" style={{ marginTop: 8 }}>
            <Metric label={<Term k="t1">T1 · measured move</Term>}
                    value={<>{fmtInt(row.t1)}{pctFrom(row.t1, row.last_close)}</>} />
            <Metric label="T2 · stretch"
                    value={<>{fmtInt(row.t2)}{pctFrom(row.t2, row.last_close)}</>} />
            <Metric label="Stop · box low" value={fmtInt(row.box_low)} />
            <Metric label={<Term k="rr">R:R at T1</Term>} value={fmtNum(row.rr_t1, 1)} />
            <Metric label="Next resistance" value={fmtInt(row.next_resistance)} />
            <Metric label="52w high" value={fmtInt(row.high_52w)} />
            <Metric label={<Term k="blue_sky">青天井</Term>}
                    value={row.blue_sky ? "YES — trail, don't cap" : "no"} />
          </div>
        </div>
      )}

      <div className="chartbox">
        <div className="title"><Term k="margin_balance">信用残</Term> (weekly, point-in-time)</div>
        <MarginChart prints={margin.prints ?? []} />
      </div>

      <ResearchPanel code={code} initial={research} />
    </div>
  );
}

function Metric({ label, value }: { label: React.ReactNode; value: React.ReactNode }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}

/** One number from the memo's analyst TP cluster — the chart line wants a consensus, not a fan
 * of five. An explicit コンセンサス(平均) row wins; else the mean of the published targets with
 * the 高値/安値 aggregate rows dropped (they'd double-count the extremes around the mean). */
function consensusTp(json: string | null): number | null {
  if (!json) return null;
  let rows: Array<{ house?: string; target?: number }>;
  try {
    const v = JSON.parse(json);
    rows = Array.isArray(v) ? v : [];
  } catch { return null; }
  const valid = rows.filter((r) => typeof r.target === "number" && r.target! > 0);
  if (!valid.length) return null;
  const isHiLo = (h?: string) => /高値|安値/.test(h ?? "");
  const avg = valid.find((r) => /平均|コンセンサス|consensus|average/i.test(r.house ?? "") && !isHiLo(r.house));
  if (avg) return Math.round(avg.target!);
  const pool = valid.filter((r) => !isHiLo(r.house));
  const use = pool.length ? pool : valid;
  return Math.round(use.reduce((a, r) => a + r.target!, 0) / use.length);
}

/** "+13.0%" suffix for a target vs the last close — null-safe. */
function pctFrom(target: number | null, close: number | null): React.ReactNode {
  if (target == null || close == null || close <= 0) return null;
  const v = (target / close - 1) * 100;
  return (
    <span className="caption" style={{ marginLeft: 4 }}>
      {v >= 0 ? "+" : ""}{v.toFixed(1)}%
    </span>
  );
}
