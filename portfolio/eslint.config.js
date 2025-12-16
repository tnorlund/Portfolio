// Next.js 16 ESLint configuration with Flat Config
const reactPlugin = require("eslint-plugin-react");
const typescriptPlugin = require("@typescript-eslint/eslint-plugin");
const typescriptParser = require("@typescript-eslint/parser");

module.exports = [
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
      react: reactPlugin,
      "@typescript-eslint": typescriptPlugin,
    },
    rules: {
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "warn",
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },
  {
    files: ["**/*.js", "**/*.jsx"],
    plugins: {
      react: reactPlugin,
    },
    rules: {
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "warn",
    },
  },
];
