/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

const here = path.dirname(new URL(import.meta.url).pathname);

export default defineConfig({
  root: here,
  plugins: [react()],
  server: {
    port: 5178,
    proxy: {
      "/api": "http://localhost:5177",
      "/files": "http://localhost:5177",
    },
  },
  test: {
    root: here,
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
