import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    rules: {
      // Pages fetch on mount with plain fetch + local state (no data-fetching library yet). This rule flags
      // that pattern; revisit when TanStack Query/SWR is adopted.
      "react-hooks/set-state-in-effect": "off",
    },
  },
  {
    // Config-driven master-data screens render arbitrary API rows by column key, so rows are intentionally loose.
    files: ["components/ResourcePage.tsx", "lib/resources.ts"],
    rules: { "@typescript-eslint/no-explicit-any": "off" },
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    ".next-e2e/**",
    "e2e/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;
