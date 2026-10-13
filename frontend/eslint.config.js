/**
 * ESLint flat config (ESLint 9+).
 *
 * Minimal scope: the only thing we ENFORCE today is the decisions
 * anti-corruption rule from spec §四. We don't ship a full TS/React
 * style guide — `tsc -b` (in the build script) handles type errors
 * already, and the rest of the codebase is fine without lint nags.
 *
 * The rule is wired via the no-deps "local-plugin" pattern so we don't
 * pull eslint-plugin-local-rules — keeps node_modules small.
 */

import tsParser from "@typescript-eslint/parser";
import noDashboardInDecisions from "./eslint-rules/no-dashboard-in-decisions.js";

const localPlugin = {
  rules: {
    "no-dashboard-in-decisions": noDashboardInDecisions,
  },
};

// Only the Decisions surface is in scope. We intentionally do NOT
// lint the rest of the codebase — `tsc -b` already catches type
// errors and we don't ship a full style guide. Keeping the surface
// narrow means CI runs in <2s and one rule doesn't accidentally
// nag the entire frontend.
export default [
  {
    files: [
      "src/pages/Decisions*.{ts,tsx}",
      "src/components/decisions/**/*.{ts,tsx}",
    ],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      decisions: localPlugin,
    },
    rules: {
      "decisions/no-dashboard-in-decisions": "error",
    },
    // Source files have legacy `// eslint-disable-next-line ...`
    // directives referencing rules from plugins (no-console,
    // react-hooks/exhaustive-deps) that we don't load — ESLint v9
    // reports these as warnings/errors. Since our scope is
    // intentionally narrow (one rule), don't fail on unknown
    // directives.
    linterOptions: {
      reportUnusedDisableDirectives: "off",
    },
  },
  {
    ignores: ["dist/**", "node_modules/**", "**/*.d.ts"],
  },
];
