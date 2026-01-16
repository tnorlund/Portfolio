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

  // Note: Using Webpack instead of Turbopack due to Safari FOUC bug
  // https://github.com/vercel/next.js/issues/77218
  // Turbopack's body{display:none} FOUC prevention doesn't get removed in Safari

  webpack: (config, { dev, isServer }) => {
    // Webpack config for backward compatibility if webpack is explicitly used
    // Note: Turbopack is now the default and recommended bundler in Next.js 16+
    if (!dev && !isServer) {
      config.optimization.splitChunks = {
        chunks: "all",
        minSize: 20000,
        maxSize: 244000,
        cacheGroups: {
          vendor: {
            test: /[\\/]node_modules[\\/]/,
            name: "vendors",
            chunks: "all",
            priority: 10,
            reuseExistingChunk: true,
          },
          common: {
            minChunks: 2,
            chunks: "all",
            name: "common",
            priority: 5,
            reuseExistingChunk: true,
          },
        },
      };

      // Enhanced tree shaking
      config.optimization.usedExports = true;
      config.optimization.sideEffects = false;

      // Remove unused modules
      config.resolve.alias = {
        ...config.resolve.alias,
        // Remove moment.js if it exists (heavy date library)
        moment: false,
      };
    }
    return config;
  },
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
