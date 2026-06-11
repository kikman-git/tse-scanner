"use client";

// Weekly 信用残 history: 売り残 (short) vs 買い残 (long), with 一般売り (neg_short) — the trapped,
// non-arb short cohort whose squeeze the scanner is hunting. One shares-count scale, three lines.

import { useEffect, useRef } from "react";
import {
  createChart, LineSeries, ColorType, CrosshairMode, type IChartApi,
} from "lightweight-charts";
import type { MarginPrint } from "@/lib/types";
import { useTheme, chartTheme } from "@/lib/theme";

const SERIES = [
  { key: "short_total", label: "売り残 short", tone: "down" },
  { key: "long_total", label: "買い残 long", tone: "up" },
  { key: "neg_short", label: "一般売り (trapped)", tone: "orange" },
] as const;

export default function MarginChart({ prints, height = 240 }: { prints: MarginPrint[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme();

  useEffect(() => {
    if (!ref.current || prints.length === 0) return;
    const c = chartTheme();
    const asc = [...prints].sort((a, b) => a.as_of_date.localeCompare(b.as_of_date));
    const chart: IChartApi = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: c.bg }, textColor: c.text, fontSize: 11 },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border },
      timeScale: { borderColor: c.border },
      crosshair: { mode: CrosshairMode.Normal },
    });
    for (const s of SERIES) {
      const line = chart.addSeries(LineSeries, { color: c[s.tone], lineWidth: 2, priceFormat: { type: "volume" } });
      line.setData(
        asc
          .filter((p) => p[s.key] != null)
          .map((p) => ({ time: p.as_of_date, value: p[s.key] as number })),
      );
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [prints, theme]);

  if (prints.length === 0) return <div className="caption">No 信用残 history for this code.</div>;
  return (
    <>
      <div className="row" style={{ gap: 14, marginBottom: 6 }}>
        {SERIES.map((s) => (
          <span key={s.key} className="caption" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span className={`legendswatch ${s.tone}`} />
            {s.label}
          </span>
        ))}
      </div>
      <div ref={ref} style={{ height }} />
    </>
  );
}
