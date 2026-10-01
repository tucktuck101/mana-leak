import { defineConfig } from "vitest/config";

// `node` environment: route handlers and `sse.ts` run server-/stream-side,
// not in a DOM (no React component tests exist yet in M1 — only plain
// text/loading state, exercised through the browser per the WP6 checklist).
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
