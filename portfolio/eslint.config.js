const { FlatCompat } = require("@eslint/eslintrc");
const path = require("path");

const compat = new FlatCompat({
  baseDirectory: __dirname,
  recommendedConfig: {},
});

module.exports = [
  // Use the compatibility layer to extend Next.js config
  ...compat.extends("next/core-web-vitals"),

  // You can add custom rules here
  {
    rules: {
      // Add any custom rules you want
      // "no-console": "warn",
      // "prefer-const": "error",
    },
  },
];
