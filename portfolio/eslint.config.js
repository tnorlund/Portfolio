import { FlatCompat } from "@eslint/eslintrc";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const compat = new FlatCompat({
  baseDirectory: __dirname,
  recommendedConfig: {},
});

export default [
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
