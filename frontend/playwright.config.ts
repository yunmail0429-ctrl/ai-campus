import { defineConfig } from "@playwright/test";
const port = process.env.PLAYWRIGHT_PORT || "3217";
process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
export default defineConfig({
  testDir: "./tests",
  testMatch: ["migration.spec.ts", "draft-content.spec.ts"],
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: "msedge",
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    screenshot: "only-on-failure",
  },
  webServer: {
    command:
      `node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port ${port}`,
    url: `http://127.0.0.1:${port}/login`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
