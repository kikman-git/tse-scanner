// Server-side fetch helpers (server components hit the loopback API directly; the browser
// goes through /api/* — see app/api/[...path]/route.ts).

export const API_BASE = process.env.TSE_API_URL ?? "http://127.0.0.1:8602";

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!r.ok) throw new Error(`API ${path} -> ${r.status}`);
  return r.json() as Promise<T>;
}

/** Client-side SWR fetcher (relative /api/* paths through the Next proxy). */
export const fetcher = (url: string) =>
  fetch(url).then((r) => {
    if (!r.ok) throw new Error(`${url} -> ${r.status}`);
    return r.json();
  });

export const tradingViewUrl = (code: string) =>
  `https://www.tradingview.com/chart/?symbol=TSE%3A${code}`;

export const STAGE_BADGE: Record<string, string> = {
  IGNITING: "🔴 IGNITING — ACT",
  PRIMED: "🟣 PRIMED — watchlist",
  CROWDED: "🟠 CROWDED — background",
  CONFIRMED: "🟢 CONFIRMED",
  NONE: "⚪ NONE",
};

export const STAGE_ORDER = ["IGNITING", "PRIMED", "CROWDED", "NONE", "CONFIRMED"] as const;

export const VERDICT_EMOJI: Record<string, string> = {
  SUPPORTS: "💚",
  NEUTRAL: "⚪",
  OPPOSES: "🔴",
};

export const VERDICT_BADGE: Record<string, string> = {
  SUPPORTS: "💚 SUPPORTS — fundamentals can run the squeeze",
  NEUTRAL: "⚪ NEUTRAL — no fundamental edge; trade the technicals",
  OPPOSES: "🔴 OPPOSES — fundamentals side with the shorts; exit mechanically",
};

/** ¥ with 億 abbreviation for table cells (ADV etc.). */
export function fmtYen(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1e8) return `¥${(v / 1e8).toFixed(1)}億`;
  if (Math.abs(v) >= 1e4) return `¥${(v / 1e4).toFixed(0)}万`;
  return `¥${v.toFixed(0)}`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function fmtInt(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-US");
}

/** Days between an ISO date and now (UTC-naive — coarse staleness checks only). */
export function daysAgo(iso: string | null): number {
  if (!iso) return 0;
  return Math.floor((Date.now() - new Date(iso + "T00:00:00").getTime()) / 86_400_000);
}
