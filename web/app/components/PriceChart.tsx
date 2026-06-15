"use client";

// Daily candles + volume + the coil box (box_high / box_low as dashed price lines) — the
// consolidation the squeeze breaks out of — plus the technical exit targets T1/T2 (dotted)
// and, when a research memo exists, the analyst consensus TP (orange) so both exit ceilings
// sit on one chart. lightweight-charts v5 (addSeries(SeriesDef, opts)).

import { useEffect, useRef } from "react";
import {
  createChart, CandlestickSeries, HistogramSeries, ColorType, LineStyle, CrosshairMode,
  type IChartApi,
} from "lightweight-charts";
import type { Bar } from "@/lib/types";
import { useTheme, chartTheme } from "@/lib/theme";

export default function PriceChart({
  bars, boxHigh, boxLow, t1, t2, analystTp, height = 360,
}: {
  bars: Bar[]; boxHigh?: number | null; boxLow?: number | null;
  t1?: number | null; t2?: number | null; analystTp?: number | null; height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useTheme(); // re-create the chart on theme flip (deps below)

  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const c = chartTheme();
    const chart: IChartApi = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: c.bg }, textColor: c.text, fontSize: 11 },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      rightPriceScale: { borderColor: c.border },
      timeScale: { borderColor: c.border, rightOffset: 4 },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: c.up, downColor: c.down, borderVisible: false,
      wickUpColor: c.up, wickDownColor: c.down,
    });
    candles.setData(bars.map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close })));

    const vol = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" } });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b) => ({
      time: b.date, value: b.volume, color: (b.close >= b.open ? c.up : c.down) + "44",
    })));

    if (boxHigh)
      candles.createPriceLine({ price: boxHigh, color: c.purple, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "box high" });
    if (boxLow)
      candles.createPriceLine({ price: boxLow, color: c.purple, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "box low" });
    if (t1)
      candles.createPriceLine({ price: t1, color: c.up, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: "T1" });
    if (t2)
      candles.createPriceLine({ price: t2, color: c.up, lineWidth: 1, lineStyle: LineStyle.Dotted, axisLabelVisible: true, title: "T2" });
    if (analystTp)
      candles.createPriceLine({ price: analystTp, color: c.orange, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title: "analyst TP" });

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, boxHigh, boxLow, t1, t2, analystTp, theme]);

  if (bars.length === 0)
    return <div className="caption">No price history for this code.</div>;
  return <div ref={ref} style={{ height }} />;
}
