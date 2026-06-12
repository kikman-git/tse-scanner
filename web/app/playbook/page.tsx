import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { apiGet } from "@/lib/api";
import type { PlaybookPayload } from "@/lib/types";
import Nav from "../components/Nav";

// The tab renders PLAYBOOK.md straight from the repo (via the API) — editing the file ships to
// the UI on reload, no rebuild. Includes the Glossary section behind the UI's tooltips.
export const dynamic = "force-dynamic";

export default async function PlaybookPage() {
  let pb: PlaybookPayload | null = null;
  let err: string | null = null;
  try {
    pb = await apiGet<PlaybookPayload>("/playbook");
  } catch (e) {
    err = e instanceof Error ? e.message : String(e);
  }

  if (err || !pb) {
    return (
      <div className="container">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h1>📕 Playbook</h1>
          <Nav />
        </div>
        <div className="banner error">
          Can&apos;t load the playbook from the API.
          <div className="mono" style={{ marginTop: 8, color: "var(--text-dim)" }}>{err}</div>
        </div>
      </div>
    );
  }

  return (
    <div className="container" style={{ maxWidth: 980 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h1>📕 Playbook</h1>
        <Nav />
      </div>
      <div className="subtitle">
        The human execution layer — what to actually <i>do</i> with what the scanner finds.
        Glossary included. Source: <span className="mono">PLAYBOOK.md</span> · updated {pb.updated}.
      </div>
      <article className="memo playbook">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{pb.markdown}</ReactMarkdown>
      </article>
    </div>
  );
}
