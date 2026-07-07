import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 90_000,
  use: {
    baseURL: process.env.BASE_URL ?? "http://localhost:8321",
    viewport: { width: 1280, height: 900 },
  },
  webServer: process.env.BASE_URL
    ? undefined
    : {
        command:
          "python3 -m http.server 8321 --directory ../out",
        port: 8321,
        reuseExistingServer: true,
      },
});
