// In Next.js, we can use environment variables directly
// NEXT_PUBLIC_ variables are available at build time and runtime

// Environment detection
const env = process.env.NODE_ENV;
const isDev = env === "development";
const isTest = env === "test";

// In development, use local proxy to avoid CORS issues when testing from other devices
// In test/production, use direct API URLs
const devApiUrl = "/api"; // Proxied via next.config.js rewrites
const prodApiUrl =
  process.env.NEXT_PUBLIC_API_URL || "https://api.tylernorlund.com";

// Determine base URL: dev proxy only in actual development, not tests
const baseUrl = isDev && !isTest ? devApiUrl : prodApiUrl;

// Temporary console.log to verify environment variable is loaded
console.log("🔧 API Base URL:", isDev ? `${devApiUrl} (dev proxy)` : prodApiUrl);

export const API_CONFIG = {
  baseUrl,
  headers: {
    "Content-Type": "application/json",
  },
};
