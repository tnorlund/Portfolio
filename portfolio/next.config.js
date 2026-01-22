const { PHASE_DEVELOPMENT_SERVER } = require("next/constants");

const withBundleAnalyzer = require("@next/bundle-analyzer")({
  enabled: process.env.ANALYZE === "true",
});

/** @type {import('next').NextConfig} */
const baseConfig = {
  output: "export",
  trailingSlash: false,
  images: {
    unoptimized: true,
  },
  compress: true,
  poweredByHeader: false,

  // Allow cross-origin requests from local network devices (e.g., iPhone testing)
  allowedDevOrigins: ["192.168.*.*"],

  // Only consider these file extensions as pages (excludes .test.tsx, .test.ts, etc.)
  pageExtensions: ['page.tsx', 'page.ts', 'page.jsx', 'page.js', 'tsx', 'ts', 'jsx', 'js'],

  // Note: Testing Turbopack - Safari FOUC bug (vercel/next.js#77218) may be fixed
  // The webpack config below is ignored when using Turbopack
};

// Export phase-aware config - only add rewrites in development to avoid warning with output: "export"
module.exports = (phase) => {
  const config = { ...baseConfig };

  if (phase === PHASE_DEVELOPMENT_SERVER) {
    // Rewrites for local development - proxies API calls to avoid CORS issues
    config.rewrites = async function () {
      return [
        {
          source: "/api/:path*",
          destination: "https://dev-api.tylernorlund.com/:path*",
        },
      ];
    };
  }

  return withBundleAnalyzer(config);
};
