"use client";

// Daily candles + volume + the coil box (box_high / box_low as dashed price lines) — the
// consolidation the squeeze breaks out of. lightweight-charts v5 (addSeries(SeriesDef, opts)).

import { useEffect, useRef } from "react";
import {
  createChart, CandlestickSeries, HistogramSeries, ColorType, LineStyle, CrosshairMode,
  type IChartApi,
} from "lightweight-charts";
import type { Bar } from "@/lib/types";
import { useTheme, chartTheme } from "@/lib/theme";

export default function PriceChart({
  bars, boxHigh, boxLow, height = 360,
}: { bars: Bar[]; boxHigh?: number | null; boxLow?: number | null; height?: number }) {
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

    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, boxHigh, boxLow, theme]);

  if (bars.length === 0)
    return <div className="caption">No price history for this code.</div>;
  return <div ref={ref} style={{ height }} />;
}
