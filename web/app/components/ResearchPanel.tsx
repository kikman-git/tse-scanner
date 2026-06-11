"use client";

// Deep-research panel — DESIGN.md §15 lifecycle in 4 states: none / running / failed / done.
// Mutations POST to the API (which spawns `tse-scanner research <code>` detached); we poll the
// research row every 5s while it's running. The verdict is a decision aid for the human's
// entry/exit — SUPPORTS / NEUTRAL / OPPOSES, never an auto-trade.

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import ReactMarkdown from "react-markdown";
import type { ResearchPayload, ResearchRow } from "@/lib/types";
import { fetcher, VERDICT_BADGE } from "@/lib/api";

function parseList(s: string | null): unknown[] {
  if (!s) return [];
  try { const v = JSON.parse(s); return Array.isArray(v) ? v : []; } catch { return []; }
}

export default function ResearchPanel({ code, initial }: { code: string; initial: ResearchPayload }) {
  const { data, mutate } = useSWR<ResearchPayload>(
    `/api/research/${code}`, fetcher,
    { fallbackData: initial, refreshInterval: (d) => (d?.state === "running" ? 5000 : 0) },
  );
  const [busy, setBusy] = useState(false);
  const state = data?.state ?? "none";
  const row = data?.row ?? null;

  // a running poll that just turned done/failed: stop the busy spinner
  const prev = useRef(state);
  useEffect(() => { if (prev.current === "running" && state !== "running") setBusy(false); prev.current = state; }, [state]);

  async function run(force = false) {
    setBusy(true);
    await fetch(`/api/research/${code}${force ? "?force=true" : ""}`, { method: "POST" }).catch(() => {});
    mutate();
  }

  return (
    <div className="chartbox">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="title" style={{ margin: 0 }}>Deep research</div>
        {(state === "done" || state === "failed") && (
          <button className="secondary" onClick={() => run(true)} disabled={busy}>
            ↻ Force re-run
          </button>
        )}
      </div>

      {state === "none" && (
        <div style={{ marginTop: 10 }}>
          <p className="caption" style={{ marginTop: 0 }}>
            No saved memo. Reconstruct the bear case, hunt squeeze-killers (dilution, lockups),
            map catalysts, and get a SUPPORTS / NEUTRAL / OPPOSES call.
          </p>
          <button onClick={() => run(false)} disabled={busy}>
            {busy ? <><span className="spin">⏳</span> launching…</> : "🔬 Run deep research"}
          </button>
        </div>
      )}

      {state === "running" && (
        <div style={{ marginTop: 10 }} className="caption">
          <span className="spin">⏳</span> Researching {code}… this takes a few minutes (multi-turn
          web research). The panel refreshes itself when the memo lands.
          {row?.started_at && <div className="mono">started {row.started_at}</div>}
        </div>
      )}

      {state === "failed" && (
        <div className="banner error" style={{ marginTop: 10 }}>
          Research run failed. <div className="mono" style={{ marginTop: 6 }}>{row?.error ?? "unknown error"}</div>
        </div>
      )}

      {state === "done" && row && <ResearchResult row={row} />}
    </div>
  );
}

function ResearchResult({ row }: { row: ResearchRow }) {
  const killers = parseList(row.squeeze_killers) as string[];
  const catalysts = parseList(row.catalysts) as Array<{ date?: string; event?: string; inside_horizon?: boolean }>;
  const sources = parseList(row.sources) as Array<string | { url?: string; title?: string }>;

  return (
    <div style={{ marginTop: 10 }}>
      {row.verdict && (
        <div className="banner info" style={{ fontWeight: 600 }}>
          {VERDICT_BADGE[row.verdict] ?? row.verdict}
          {row.confidence && <span style={{ fontWeight: 400 }}> · confidence {row.confidence}</span>}
        </div>
      )}
      {row.one_liner && <p style={{ marginTop: 6 }}>{row.one_liner}</p>}

      {killers.length > 0 && (
        <>
          <div className="title" style={{ marginTop: 12 }}>Squeeze-killers</div>
          <ul style={{ margin: "4px 0" }}>{killers.map((k, i) => <li key={i}>{k}</li>)}</ul>
        </>
      )}

      {catalysts.length > 0 && (
        <>
          <div className="title" style={{ marginTop: 12 }}>Catalysts</div>
          <ul style={{ margin: "4px 0" }}>
            {catalysts.map((c, i) => (
              <li key={i}>
                {c.inside_horizon ? "🎯 " : "· "}
                <span className="mono">{c.date ?? "—"}</span> {c.event ?? ""}
              </li>
            ))}
          </ul>
        </>
      )}

      {row.memo_md && (
        <details open>
          <summary>Full memo</summary>
          <div className="memo"><ReactMarkdown>{row.memo_md}</ReactMarkdown></div>
        </details>
      )}

      {sources.length > 0 && (
        <details>
          <summary>Sources ({sources.length})</summary>
          <ul style={{ margin: "4px 0" }}>
            {sources.map((s, i) => {
              const url = typeof s === "string" ? s : s.url;
              const title = typeof s === "string" ? s : s.title ?? s.url;
              return <li key={i}>{url ? <a href={url} target="_blank" rel="noreferrer">{title}</a> : String(title)}</li>;
            })}
          </ul>
        </details>
      )}

      <p className="caption" style={{ marginTop: 10 }}>
        {row.model && <>model {row.model} · </>}
        {row.num_turns != null && <>{row.num_turns} turns · </>}
        {row.cost_usd != null && <>${row.cost_usd.toFixed(2)} · </>}
        {row.duration_s != null && <>{Math.round(row.duration_s)}s · </>}
        {row.finished_at && <>finished {row.finished_at}</>}
      </p>
    </div>
  );
}
