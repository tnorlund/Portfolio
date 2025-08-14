module.exports = {
  ci: {
    collect: {
      // Where to run Lighthouse
      url: ['http://localhost:3000/', 'http://localhost:3000/receipt'],
      numberOfRuns: process.env.CI ? 3 : 1, // Run 3 times in CI, 1 locally
      
      // Collect settings
      settings: {
        preset: 'desktop', // or 'mobile'
        // Override specific settings
        throttling: {
          rttMs: 40,
          throughputKbps: 10240,
          cpuSlowdownMultiplier: 1,
        },
        // Emulate different devices
        screenEmulation: {
          mobile: false,
          width: 1350,
          height: 940,
          deviceScaleFactor: 1,
        },
        // Skip some audits in development
        skipAudits: process.env.NODE_ENV === 'development' ? [
          'is-on-https',
          'uses-http2',
          'uses-text-compression',
        ] : [],
      },
    },
    
    assert: {
      preset: 'lighthouse:recommended',
      assertions: {
        // Core Web Vitals
        'first-contentful-paint': ['error', { maxNumericValue: 1800 }],
        'largest-contentful-paint': ['error', { maxNumericValue: 2500 }],
        'cumulative-layout-shift': ['error', { maxNumericValue: 0.1 }],
        'total-blocking-time': ['warn', { maxNumericValue: 300 }],
        
        // Performance
        'speed-index': ['warn', { maxNumericValue: 3400 }],
        'interactive': ['warn', { maxNumericValue: 3800 }],
        
        // Best practices
        'uses-http2': 'off', // Local dev doesn't use HTTP/2
        'uses-long-cache-ttl': 'off', // Not applicable for dev
        'uses-text-compression': 'off', // Dev server doesn't compress
        'unminified-javascript': 'off', // Dev code is unminified
        'unminified-css': 'off', // Dev CSS is unminified
        'valid-source-maps': 'off', // Source maps are for dev
        
        // Bundle sizes - more lenient for dev
        'total-byte-weight': ['warn', { maxNumericValue: 2000000 }], // 2MB for dev
        'unused-javascript': ['warn', { maxNumericValue: 200000 }], // 200KB
        'unused-css-rules': ['warn', { maxNumericValue: 100000 }], // 100KB
        
        // Accessibility
        'categories:accessibility': ['warn', { minScore: 0.9 }],
        
        // PWA - disabled for portfolio site
        'categories:pwa': 'off',
      },
    },
    
    upload: {
      // For storing results (requires server setup)
      target: 'temporary-public-storage',
    },
  },
};