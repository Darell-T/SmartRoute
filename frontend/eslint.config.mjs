import next from "eslint-config-next";

const nextConfigs = Array.isArray(next) ? next : [next];

const eslintConfig = [
  {
    // The build pipeline (.mjs scripts, generated artifacts, build output) is
    // not application code and is not linted here.
    ignores: [
      ".next/**",
      "node_modules/**",
      "public/**",
      "scripts/**",
      "next-env.d.ts",
      "**/*.check.mjs",
      "**/*.test.mjs",
    ],
  },
  ...nextConfigs,
  {
    // Pragmatic gate for a large existing codebase: keep genuine correctness
    // errors failing, but demote opinionated React-19 hook rules and a few
    // stylistic rules to warnings so they surface without blocking the build.
    files: ["**/*.{ts,tsx}"],
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-explicit-any": "warn",
      "@next/next/no-img-element": "warn",
      "no-console": "warn",
    },
  },
  {
    // SR-REVIEW-013: these lifecycle owners are verified independently and
    // must not regress to hook/ref/dependency warnings while unrelated legacy
    // warnings remain visible but non-blocking.
    files: [
      "app/page.tsx",
      "lib/use-live-feed.ts",
      "lib/use-destination-search.ts",
      "lib/initial-geolocation.ts",
      "components/smart-route/chat/use-progressive-text.ts",
    ],
    rules: {
      "react-hooks/set-state-in-effect": "error",
      "react-hooks/refs": "error",
      "react-hooks/exhaustive-deps": "error",
    },
  },
];

export default eslintConfig;
