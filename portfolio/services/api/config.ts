// In Next.js, we can use environment variables directly
// NEXT_PUBLIC_ variables are available at build time and runtime

// In development, use local proxy to avoid CORS issues when testing from other devices
const isDev = process.env.NODE_ENV === "development";
const devApiUrl = "/api"; // Proxied via next.config.js rewrites
const prodApiUrl =
  process.env.NEXT_PUBLIC_API_URL || "https://api.tylernorlund.com";

// Temporary console.log to verify environment variable is loaded
console.log(
  "🔧 API Base URL:",
  isDev ? `${devApiUrl} (dev proxy)` : prodApiUrl
);

export const API_CONFIG = {
  baseUrl: isDev ? devApiUrl : prodApiUrl,
  headers: {
    "Content-Type": "application/json",
  },
};
