import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
// 从 vitest/config 拿 defineConfig：它认 `test` 段（vite 自己的类型不认）。
import { defineConfig } from "vitest/config";

// 开发时把后端的 JSON 端点代理到本机 8300；生产由 FastAPI 读 dist/ 提供
// （见 plan「v0.9 审阅页工程化」的"入口与产物落点"）。
const BACKEND = "http://127.0.0.1:8300";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: Object.fromEntries(
      ["/types", "/entries", "/ask", "/health", "/log", "/sync"].map((p) => [
        p,
        BACKEND,
      ]),
    ),
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./tests/setup.ts"],
    // 只收 tests/：e2e/ 归 Playwright（默认 include 是 **/*.spec.*，会把 e2e 也吸进来，
    // 于是 Vitest 去跑 @playwright/test 的 spec 而报错 —— 两个 runner 必须各管一摊）。
    include: ["tests/**/*.test.{ts,tsx}"],
  },
});
