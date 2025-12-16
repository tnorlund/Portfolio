const withBundleAnalyzer = require("@next/bundle-analyzer")({
  enabled: process.env.ANALYZE === "true",
});

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  trailingSlash: false,
  images: {
    unoptimized: true,
  },
  compress: true,
  poweredByHeader: false,

  // Only consider these file extensions as pages (excludes .test.tsx, .test.ts, etc.)
  pageExtensions: ['page.tsx', 'page.ts', 'page.jsx', 'page.js', 'tsx', 'ts', 'jsx', 'js'],

  // Use Turbopack (default in Next.js 16) for better bundle optimization
  // Turbopack automatically handles bundle splitting and tree shaking
  turbopack: {},

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

module.exports = withBundleAnalyzer(nextConfig);
