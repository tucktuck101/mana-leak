import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Default `node` environment: the route handler and `sse.ts` run
// server-/stream-side, not in a DOM. Component tests opt into jsdom per file
// with a `// @vitest-environment jsdom` docblock. `esbuild.jsx: "automatic"`
// compiles `.tsx` with the React 19 automatic runtime, which
// `tsconfig.json`'s `"jsx": "preserve"` (there for Next's own compiler) does
// not do.
export default defineConfig({
  // `tsconfig.json`'s `"@/*": ["./src/*"]` paths, which vite does not read.
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  esbuild: { jsx: "automatic" },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
