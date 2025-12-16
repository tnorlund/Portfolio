// Next.js 16 ESLint Flat Config
// Manually configures plugins that eslint-config-next provides, instead of extending eslint-config-next directly.
// This approach avoids potential issues with plugin resolution and allows for explicit control.

const js = require("@eslint/js");
const typescriptParser = require("@typescript-eslint/parser");
const typescriptPlugin = require("@typescript-eslint/eslint-plugin");
const reactPlugin = require("eslint-plugin-react");
const reactHooksPlugin = require("eslint-plugin-react-hooks");
const jsxA11yPlugin = require("eslint-plugin-jsx-a11y");
const nextPlugin = require("@next/eslint-plugin-next");
const globals = require("globals");

// Clean globals by removing entries with leading/trailing whitespace
const cleanGlobals = (globalObj) => {
  const cleaned = {};
  for (const [key, value] of Object.entries(globalObj)) {
    if (key === key.trim()) {
      cleaned[key] = value;
    }
  }
  return cleaned;
};

module.exports = [
  // Ignore patterns
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "out/**",
      "build/**",
      "dist/**",
      "next-env.d.ts",
    ],
  },

  // ESLint recommended config (with overrides for TypeScript)
  {
    ...js.configs.recommended,
    rules: {
      ...js.configs.recommended.rules,
      "no-unused-vars": "off", // Disable in favor of @typescript-eslint/no-unused-vars
    },
  },

  // TypeScript files (with browser globals for React components)
  {
    files: ["**/*.ts", "**/*.tsx"],
    languageOptions: {
      parser: typescriptParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: {
          jsx: true,
        },
      },
      globals: {
        ...cleanGlobals(globals.browser),
        ...cleanGlobals(globals.node),
        React: "readonly",
        JSX: "readonly",
        NodeJS: "readonly",
        PerformanceMarkOptions: "readonly",
        RequestInfo: "readonly",
      },
    },
    plugins: {
      "@typescript-eslint": typescriptPlugin,
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
      "jsx-a11y": jsxA11yPlugin,
      "@next/next": nextPlugin,
    },
    rules: {
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "warn",
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@next/next/no-html-link-for-pages": "warn",
      "@next/next/no-img-element": "warn",
      "jsx-a11y/alt-text": "warn",
    },
  },

  // JavaScript and JSX files (with browser globals for React)
  {
    files: ["**/*.js", "**/*.jsx"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      parserOptions: {
        ecmaFeatures: {
          jsx: true,
        },
      },
      globals: {
        ...cleanGlobals(globals.browser),
        ...cleanGlobals(globals.node),
        React: "readonly",
        JSX: "readonly",
      },
    },
    plugins: {
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
      "jsx-a11y": jsxA11yPlugin,
      "@next/next": nextPlugin,
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "warn",
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@next/next/no-html-link-for-pages": "warn",
      "@next/next/no-img-element": "warn",
      "jsx-a11y/alt-text": "warn",
    },
  },

  // Jest test files and setup files (TS variants)
  {
    files: ["**/*.test.ts", "**/*.test.tsx", "**/*.perf.test.ts", "**/*.perf.test.tsx", "**/*.integration.test.ts", "**/*.integration.test.tsx", "**/jest.setup.ts", "**/test-utils/**/*.ts", "**/test-utils/**/*.tsx"],
    languageOptions: {
      globals: {
        ...cleanGlobals(globals.browser),
        ...cleanGlobals(globals.node),
        ...cleanGlobals(globals.jest),
      },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },

  // Jest test files (JS variants)
  {
    files: ["**/*.test.js", "**/*.test.jsx", "**/*.perf.test.js", "**/*.perf.test.jsx", "**/*.integration.test.js", "**/*.integration.test.jsx", "**/jest.setup.js", "**/test-utils/**/*.js", "**/test-utils/**/*.jsx"],
    languageOptions: {
      globals: {
        ...cleanGlobals(globals.browser),
        ...cleanGlobals(globals.node),
        ...cleanGlobals(globals.jest),
      },
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },

  // Node/CommonJS files (JS)
  {
    files: ["*.config.js", "next.config.js", ".lighthouserc.js"],
    languageOptions: {
      globals: cleanGlobals(globals.node),
    },
    rules: {
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },

  // Node/CommonJS files (TS)
  {
    files: ["*.config.ts"],
    languageOptions: {
      globals: cleanGlobals(globals.node),
    },
    rules: {
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },

  // Playwright E2E test files
  {
    files: ["**/*.spec.ts", "**/*.spec.tsx", "**/*.e2e.ts"],
    languageOptions: {
      globals: {
        ...cleanGlobals(globals.browser),
        ...cleanGlobals(globals.node),
        test: "readonly",
        expect: "readonly",
        page: "readonly",
        context: "readonly",
        browser: "readonly",
        playwright: "readonly",
      },
    },
    rules: {
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },
];
