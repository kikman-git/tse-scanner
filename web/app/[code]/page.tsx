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
        </div>
        <PriceChart bars={bars} boxHigh={row?.box_high} boxLow={row?.box_low} />
      </div>

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
