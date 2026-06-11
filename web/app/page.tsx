import { apiGet } from "@/lib/api";
import type { ScanPayload, Summary } from "@/lib/types";
import ScanView from "./components/ScanView";

// Always render fresh against the store; never static-cache the scan table.
export const dynamic = "force-dynamic";

export default async function Page() {
  let scan: ScanPayload | null = null;
  let summary: Summary | null = null;
  let err: string | null = null;
  try {
    [scan, summary] = await Promise.all([
      apiGet<ScanPayload>("/scan"),
      apiGet<Summary>("/summary").catch(() => null),
    ]);
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  if (err || !scan) {
    return (
      <div className="container">
        <h1>踏み上げ Scanner</h1>
        <div className="banner error">
          Can&apos;t reach the scanner API. Is <span className="mono">tse-api</span> up on
          <span className="mono"> 127.0.0.1:8602</span>?
          <div className="mono" style={{ marginTop: 8, color: "var(--text-dim)" }}>{err}</div>
        </div>
        <p className="caption">
          Start it with <span className="mono">systemctl --user start tse-api</span> (or{" "}
          <span className="mono">uvicorn tse_scanner.api:app --host 127.0.0.1 --port 8602</span>).
        </p>
      </div>
    );
  }

  return <ScanView initial={scan} summary={summary} />;
}
