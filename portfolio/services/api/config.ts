// In Next.js, we can use environment variables directly
// NEXT_PUBLIC_ variables are available at build time and runtime

// Temporary console.log to verify environment variable is loaded
console.log(
  "🔧 API Base URL:",
  process.env.NEXT_PUBLIC_API_URL || "https://api.tylernorlund.com (fallback)"
);

export const API_CONFIG = {
  baseUrl: process.env.NEXT_PUBLIC_API_URL || "https://api.tylernorlund.com",
  headers: {
    "Content-Type": "application/json",
  },
};
