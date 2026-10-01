import { defineConfig, devices } from "@playwright/test";

// Browser regression tests (`npm run test:browser`). These drive a real
// Chromium against the running Compose stack — they are not part of
// `npm run test` (vitest, no browser, no stack) and never start a server
// themselves: the stack is brought up by `tests/e2e/run.sh` / `make e2e`.
export default defineConfig({
  testDir: "./e2e",
  // One browser, one worker: the flow under test is timing-sensitive and the
  // assertions read server state back, so parallel runs would interleave.
  workers: 1,
  fullyParallel: false,
  // A regression test for an intermittent bug must never be retried green.
  retries: 0,
  // A turn streams a real model answer (PRD: 120 s turn deadline).
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:3000",
    trace: "off",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
