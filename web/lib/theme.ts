"use client";

// Light/dark theme. The chrome is driven by CSS custom properties (globals.css); charts can't use
// CSS vars directly (lightweight-charts takes literal colors), so they read the resolved variables
// via `chartTheme()` and re-create when the theme flips. Choice persists in localStorage; the
// no-flash <script> in layout.tsx applies it before first paint.

import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
const KEY = "tse-theme";
const EVENT = "tse-themechange";

export function getTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return (document.documentElement.dataset.theme as Theme) || "light";
}

export function setTheme(t: Theme) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem(KEY, t); } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: t }));
}

export function toggleTheme() {
  setTheme(getTheme() === "dark" ? "light" : "dark");
}

/** Current theme as React state, kept in sync across components via the themechange event. */
export function useTheme(): Theme {
  const [theme, setT] = useState<Theme>("light");
  useEffect(() => {
    setT(getTheme());
    const on = () => setT(getTheme());
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return theme;
}

const cssVar = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** Resolve the current CSS palette into literal colors for lightweight-charts. */
export function chartTheme() {
  return {
    bg: cssVar("--bg-card") || "#ffffff",
    text: cssVar("--text-dim") || "#5c6675",
    grid: cssVar("--chart-grid") || "#eef1f6",
    border: cssVar("--border") || "#dde3ec",
    up: cssVar("--green") || "#1a9d56",
    down: cssVar("--red") || "#d23b3b",
    purple: cssVar("--purple") || "#7b4fd0",
    orange: cssVar("--orange") || "#cf7a1e",
  };
}
