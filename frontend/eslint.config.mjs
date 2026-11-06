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
  {
    // Number presentation is centralised: lib/format.ts turns values into text and the .t-* classes in globals.css set sizes. Pages and components
    // may not format numbers themselves or pick large font sizes, so a new module cannot introduce another number style.
    files: ["app/**/*.{ts,tsx}", "components/**/*.{ts,tsx}"],
    ignores: ["app/login/**", "app/layout.tsx"],
    rules: {
      "no-restricted-syntax": ["error",
        { selector: "CallExpression[callee.property.name='toLocaleString']", message: "Format numbers with fmtNum/fmtRupees/fmtMoney/fmtPct from lib/format.ts." },
        { selector: "CallExpression[callee.property.name='toFixed']", message: "Format numbers with fmtNum/fmtPct from lib/format.ts." },
        { selector: "MemberExpression[object.name='Intl']", message: "Use the shared formatters in lib/format.ts." },
        { selector: "Literal[value=/text-.(2[3-9]|[3-9][0-9])px/]", message: "Numbers use the .t-kpi class (28px max); headings use .t-page / .t-section. No larger arbitrary sizes." },
        { selector: "TemplateElement[value.raw=/text-.(2[3-9]|[3-9][0-9])px/]", message: "Numbers use the .t-kpi class (28px max); headings use .t-page / .t-section. No larger arbitrary sizes." },
      ],
    },
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
