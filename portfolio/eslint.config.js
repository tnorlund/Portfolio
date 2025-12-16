// Next.js 16 ESLint Flat Config
// Workaround for circular reference in eslint-config-next@16.0.10
// Manually configures plugins that eslint-config-next provides

const js = require("@eslint/js");
const typescriptParser = require("@typescript-eslint/parser");
const typescriptPlugin = require("@typescript-eslint/eslint-plugin");
const reactPlugin = require("eslint-plugin-react");
const reactHooksPlugin = require("eslint-plugin-react-hooks");
const jsxA11yPlugin = require("eslint-plugin-jsx-a11y");
const nextPlugin = require("@next/eslint-plugin-next");

module.exports = [
  // Ignore patterns
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "out/**",
      "build/**",
      "dist/**",
      "*.config.js",
      "*.config.ts",
      "next-env.d.ts",
    ],
  },

  // ESLint recommended config
  js.configs.recommended,

  // TypeScript files
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
    },
    plugins: {
      "@typescript-eslint": typescriptPlugin,
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
      "jsx-a11y": jsxA11yPlugin,
      "@next/next": nextPlugin,
    },
    rules: {
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

  // JavaScript and JSX files
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
    },
    plugins: {
      react: reactPlugin,
      "react-hooks": reactHooksPlugin,
      "jsx-a11y": jsxA11yPlugin,
      "@next/next": nextPlugin,
    },
    rules: {
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "warn",
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "@next/next/no-html-link-for-pages": "warn",
      "@next/next/no-img-element": "warn",
      "jsx-a11y/alt-text": "warn",
    },
  },

  // Jest test files
  {
    files: ["**/*.test.ts", "**/*.test.tsx", "**/*.test.js", "**/*.test.jsx"],
    languageOptions: {
      globals: {
        describe: "readonly",
        test: "readonly",
        it: "readonly",
        expect: "readonly",
        beforeEach: "readonly",
        afterEach: "readonly",
        beforeAll: "readonly",
        afterAll: "readonly",
        jest: "readonly",
      },
    },
  },

  // Node/CommonJS files
  {
    files: ["*.config.js", "*.config.ts", "next.config.js"],
    languageOptions: {
      globals: {
        require: "readonly",
        module: "readonly",
        __dirname: "readonly",
        __filename: "readonly",
        process: "readonly",
      },
    },
  },
];
