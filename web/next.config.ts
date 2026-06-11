import type { NextConfig } from "next";

// The FastAPI layer is loopback-only (127.0.0.1:8602); the browser reaches it through
// app/api/[...path]/route.ts. Nothing else to configure.
const nextConfig: NextConfig = {};

export default nextConfig;
