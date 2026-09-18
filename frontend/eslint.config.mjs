import next from "eslint-config-next";

const nextConfigs = Array.isArray(next) ? next : [next];

const eslintConfig = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "public/**",
      "scripts/**",
      "tools/**",
      "next-env.d.ts",
      "**/*.check.mjs",
      "**/*.test.mjs",
    ],
  },
  ...nextConfigs,
  {
    files: ["**/*.{ts,tsx}"],
    rules: {
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/exhaustive-deps": "warn",
      "@typescript-eslint/no-explicit-any": "warn",
      "@next/next/no-img-element": "warn",
      "no-console": "warn",
      complexity: ["error", { max: 12 }],
      "max-depth": ["error", 4],
    },
  },
  {
    // SR-REVIEW-013: these lifecycle owners are verified independently and
    // must not regress to hook/ref/dependency warnings while unrelated legacy
    // warnings remain visible but non-blocking.
    files: [
      "app/page.tsx",
      "lib/hooks/use-live-feed.ts",
      "lib/hooks/use-destination-search.ts",
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
