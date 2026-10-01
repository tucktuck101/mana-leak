import { defineConfig } from "vitest/config";

// Scoped to the proxy route-handler test (no React component tests exist
// yet in M1 — only plain text/loading state, exercised through the browser
// per the WP6 checklist, not unit-rendered). Route Handlers run server-side,
// so `node` is the right environment, not `jsdom`.
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/app/api/__tests__/**/*.test.ts"],
  },
});
