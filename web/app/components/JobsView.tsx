"use client";

// Jobs tab — visibility into the long-running batch work (backtests, ingests, research runs).
// Jobs are discovered server-side from data/*.log (+ research cli logs), so anything that logs
// shows up regardless of who spawned it. SWR polls fast only while something is running, same
// convention as the scan view.

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import type { Job, JobLogPayload, JobsPayload } from "@/lib/types";
import { fetcher } from "@/lib/api";
import Nav from "./Nav";
import Term from "./Term";

const STATUS_BADGE: Record<Job["status"], string> = {
  running: "⏳ running",
  done: "✅ done",
  failed: "❌ failed",
  idle: "⚪ idle",
};

const KIND_LABEL: Record<Job["kind"], string> = {
  backtest: "backtest",
  ingest: "ingest",
  research: "research",
  service: "service",
  batch: "batch",
};

// glossary keys for the kind chips ("research" the job ≠ the research panel entry)
const KIND_GLOSS: Record<Job["kind"], string> = {
  backtest: "backtest",
  ingest: "ingest",
  research: "research_job",
  service: "service",
  batch: "batch",
};

function fmtAge(s: number): string {
  if (s < 90) return `${Math.round(s)}s ago`;
  if (s < 90 * 60) return `${Math.round(s / 60)}m ago`;
  if (s < 36 * 3600) return `${(s / 3600).toFixed(1)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

function fmtSize(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

function fmtElapsed(s: number): string {
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60 ? ` ${s % 60}s` : ""}`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

export default function JobsView({ initial }: { initial: JobsPayload }) {
  const { data } = useSWR<JobsPayload>("/api/jobs", fetcher, {
    fallbackData: initial,
    refreshInterval: (d) => (d?.jobs.some((j) => j.running) ? 4000 : 15000),
    revalidateOnFocus: true,
  });
  const payload = data ?? initial;
  const [open, setOpen] = useState<string | null>(
    payload.jobs.find((j) => j.running)?.id ?? null,
  );

  const batch = payload.jobs.filter((j) => j.kind !== "service");
  const service = payload.jobs.filter((j) => j.kind === "service");
  const nRunning = payload.jobs.filter((j) => j.running).length;

  return (
    <div className="container">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h1>Jobs</h1>
        <Nav />
      </div>
      <div className="subtitle">
        Long-running batch work — live progress and logs.{" "}
        {nRunning > 0 ? `${nRunning} running.` : "Nothing running."}
      </div>

      {payload.processes.length > 0 && (
        <div className="filters" style={{ marginBottom: 16 }}>
          {payload.processes.map((p) => (
            <span key={p.pid} className="chip on mono" title={p.cmd}>
              pid {p.pid} · {p.kind} · {fmtElapsed(p.elapsed_s)}
            </span>
          ))}
        </div>
      )}

      {batch.map((j) => (
        <JobCard key={j.id} job={j} open={open === j.id}
          onToggle={() => setOpen(open === j.id ? null : j.id)} />
      ))}
      {batch.length === 0 && (
        <div className="banner info">
          No batch logs under <span className="mono">data/</span> yet — run a scan, backtest or
          research and it shows up here.
        </div>
      )}

      {service.length > 0 && (
        <>
          <h2 style={{ fontSize: "1.05rem", margin: "26px 0 8px", color: "var(--text-dim)" }}>
            Service logs
          </h2>
          {service.map((j) => (
            <JobCard key={j.id} job={j} open={open === j.id}
              onToggle={() => setOpen(open === j.id ? null : j.id)} />
          ))}
        </>
      )}

      <p className="caption" style={{ marginTop: 14 }}>
        Status is inferred from the log tail + live <span className="mono">tse-scanner</span>{" "}
        processes: a replay can go ~25 min between progress prints, so a fresh-process /
        stale-log job still reads <b>running</b>. ❌ means a failure marker in the tail —
        open the log.
      </p>
    </div>
  );
}

function JobCard({ job, open, onToggle }: { job: Job; open: boolean; onToggle: () => void }) {
  const pct = job.progress?.pct ?? null;
  return (
    <div className={`jobcard${job.running ? " live" : ""}`}>
      <div className="jobhead" onClick={onToggle}>
        <span style={{ whiteSpace: "nowrap" }}>{STATUS_BADGE[job.status]}</span>
        <span className="mono" style={{ fontWeight: 600 }}>{job.label}</span>
        <span className="chip" style={{ cursor: "default" }}>
          <Term k={KIND_GLOSS[job.kind]}>{KIND_LABEL[job.kind]}</Term>
        </span>
        {job.pids.length > 0 && (
          <span className="mono" style={{ color: "var(--text-dim)" }}>
            pid {job.pids.join(", ")}
          </span>
        )}
        <span style={{ marginLeft: "auto", color: "var(--text-dim)", whiteSpace: "nowrap" }}>
          {fmtAge(job.age_s)} · {fmtSize(job.size)} · {open ? "▾" : "▸"}
        </span>
      </div>

      {pct != null && (
        <div className="row" style={{ marginTop: 8, gap: 10 }}>
          <span className="bar" style={{ flex: "0 0 220px" }}>
            <i style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
          </span>
          <span className="mono">{pct.toFixed(1)}%</span>
          {job.progress?.events != null && (
            <span className="mono" style={{ color: "var(--text-dim)" }}>
              {job.progress.events} events
            </span>
          )}
        </div>
      )}
      <div className="mono jobline">{job.progress?.detail ?? job.last_line ?? ""}</div>

      {open && <LogTail id={job.id} running={job.running} />}
    </div>
  );
}

function LogTail({ id, running }: { id: string; running: boolean }) {
  const { data } = useSWR<JobLogPayload>(
    `/api/jobs/log?id=${encodeURIComponent(id)}&tail=400`, fetcher,
    { refreshInterval: running ? 3500 : 0, revalidateOnFocus: true },
  );
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    // keep the newest lines in view while the job streams
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [data]);
  if (!data) return <pre className="logbox">loading…</pre>;
  return <pre ref={box} className="logbox">{data.lines.join("\n") || "(empty log)"}</pre>;
}
