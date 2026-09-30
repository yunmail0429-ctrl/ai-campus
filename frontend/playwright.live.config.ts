import { randomBytes } from "node:crypto";
import { resolve } from "node:path";
import { defineConfig } from "@playwright/test";
process.env.NO_PROXY = [process.env.NO_PROXY, "127.0.0.1", "localhost"]
  .filter(Boolean)
  .join(",");
process.env.XFLJ_E2E_PASSWORD ||= randomBytes(24).toString("hex");
const python = resolve(
  process.platform === "win32"
    ? "../backend/.venv/Scripts/python.exe"
    : "../backend/.venv/bin/python",
);
export default defineConfig({
  testDir: "./tests",
  testMatch: "live*.spec.ts",
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:3218",
    channel: "msedge",
    reducedMotion: "reduce",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: [
    {
      command: `"${python}" ../backend/tests/serve_e2e.py`,
      url: "http://127.0.0.1:8018/health/live",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command:
        "node node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3218",
      url: "http://127.0.0.1:3218/login",
      env: {
        BACKEND_URL: "http://127.0.0.1:8018",
        APP_ORIGIN: "http://127.0.0.1:3218",
      },
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
