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
  // NFR-009 ③（无外部 CDN、断网可用）的一半：静态引用在源码这一层就挡掉。
  // 另一半在产物那一侧 —— scripts/dist_external_url_check.py 扫整个 web/dist，
  // 因为 bundle 里也可能出现源码里看不出来的地址（依赖注入、CSS 里的 url()）。
  {
    selector:
      "ImportDeclaration[source.value=/^(?:https?:)?\\/\\//], ImportExpression[source.value=/^(?:https?:)?\\/\\//]",
    message: "不得从远程 URL 导入（NFR-009 ③：断网也要能用）",
  },
  {
    selector: "Literal[value=/^(?:https?:)?\\/\\//]",
    message: "不得引用远程地址（NFR-009 ③：断网也要能用）",
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
      // 与 Python 侧同一条规矩、同一个数字（`scripts/size_guard.py` 的
      // DEFAULT_MAX_FUNC_LINES = 80）。**口径也要对齐**：那边算的是
      // `endline - lineno + 1`，即含空行与注释；所以这里不加 skipBlankLines /
      // skipComments —— 否则同一段代码在两边的读数会不一样，而"哪个数字算数"
      // 正是这类门禁最容易烂掉的地方。
      "max-lines-per-function": ["error", { max: 80 }],
    },
  },
);
