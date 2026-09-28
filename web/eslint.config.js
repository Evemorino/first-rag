import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";

// 三条禁止规则 = ADR-21 第 3 条"前端不得成为第二个后端"的机械判据（NFR-009 ④）。
const forbiddenBackend = [
  {
    selector: "ImportDeclaration[source.value=/^(qdrant|qdrant-client|openai|@qdrant)/]",
    message: "前端不得直连向量库或模型服务（NFR-009 ④）",
  },
  {
    selector:
      "MemberExpression[object.property.name='env'][property.name=/^ARK_/]",
    message: "前端不得读取模型服务密钥（NFR-009 ④）",
  },
  {
    selector: "ImportDeclaration[source.value=/^(node:fs|fs|node:fs\\/promises)$/]",
    message: "前端不得写文件（NFR-009 ④）",
  },
];

export default tseslint.config(
  { ignores: ["dist/**", "node_modules/**", "src/api/schema.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}", "tests/**/*.{ts,tsx}"],
    languageOptions: {
      globals: { ...globals.browser, ...globals.node },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      "no-restricted-syntax": ["error", ...forbiddenBackend],
    },
  },
);
