import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Next.js gzips responses by default under `next start`/standalone, which
  // buffers chunks before flushing (installed docs: compress.md,
  // streaming.md). The /api/* proxy streams SSE chat turns and must forward
  // bytes as the API sends them, so compression is disabled.
  compress: false,
};

export default nextConfig;
