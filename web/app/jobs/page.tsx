import { apiGet } from "@/lib/api";
import type { JobsPayload } from "@/lib/types";
import JobsView from "../components/JobsView";

// Always render fresh — job state moves under us.
export const dynamic = "force-dynamic";

export default async function JobsPage() {
  let jobs: JobsPayload | null = null;
  let err: string | null = null;
  try {
    jobs = await apiGet<JobsPayload>("/jobs");
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  if (err || !jobs) {
    return (
      <div className="container">
        <h1>Jobs</h1>
        <div className="banner error">
          Can&apos;t reach the scanner API. Is <span className="mono">tse-api</span> up on
          <span className="mono"> 127.0.0.1:8602</span>?
          <div className="mono" style={{ marginTop: 8, color: "var(--text-dim)" }}>{err}</div>
        </div>
      </div>
    );
  }

  return <JobsView initial={jobs} />;
}
