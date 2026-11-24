// Configuración ESLint compartida (flat config). TypeScript estricto: sin `any` explícito.
import js from "@eslint/js";
import next from "@next/eslint-plugin-next";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

/** @param {{ next?: boolean }} [opts] */
export function klickConfig(opts = {}) {
  return tseslint.config(
    {
      ignores: [
        "**/.next/**",
        "**/dist/**",
        "**/node_modules/**",
        "**/src/schema.d.ts",
        "**/public/sw.js",
        "next-env.d.ts",
      ],
    },
    js.configs.recommended,
    ...tseslint.configs.recommended,
    {
      languageOptions: { globals: { ...globals.browser, ...globals.node } },
      plugins: { "react-hooks": reactHooks, ...(opts.next ? { "@next/next": next } : {}) },
      rules: {
        ...reactHooks.configs.recommended.rules,
        ...(opts.next ? next.configs.recommended.rules : {}),
        "@typescript-eslint/no-explicit-any": "error",
        "@typescript-eslint/consistent-type-imports": [
          "error",
          { fixStyle: "inline-type-imports" },
        ],
        "@typescript-eslint/no-unused-vars": [
          "error",
          { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
        ],
        eqeqeq: ["error", "always"],
        "no-console": ["error", { allow: ["warn", "error"] }],
      },
    },
  );
}

export default klickConfig();
