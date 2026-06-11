import { NextRequest } from "next/server";

// Browser → loopback-FastAPI proxy. The API binds 127.0.0.1:8602 (no auth — PLAN.md Track A);
// only this Next server (and Tailscale users hitting it) can reach it.
const API = process.env.TSE_API_URL ?? "http://127.0.0.1:8602";

async function proxy(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path } = await params;
  const url = `${API}/${path.join("/")}${req.nextUrl.search}`;
  const r = await fetch(url, { method: req.method, cache: "no-store" });
  return new Response(await r.text(), {
    status: r.status,
    headers: { "content-type": r.headers.get("content-type") ?? "application/json" },
  });
}

export { proxy as GET, proxy as POST };
