import { defineConfig } from "@playwright/test";

// 跑真后端：uvicorn 提供构建产物（web/dist）与 JSON 端点，Qdrant 由 `make up` 起着。
// 端口避开 8300，免得和你自己开着的 serve 打架。
const PORT = 8321;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: { baseURL: `http://127.0.0.1:${PORT}` },
  webServer: {
    // **先构建再起服务**：`GET /` 读的是 web/dist，而 dist 是构建产物 ——
    // 忘了 build 就会"测试跑在旧 bundle 上"（真踩过：e2e 找不到刚加的编辑表单，
    // 而类型检查、单测全绿）。把 build 放进 webServer 让这个坑不可能再出现。
    command: `pnpm --dir web build && .venv/bin/python -m uvicorn src.api.app:app --port ${PORT}`,
    cwd: "..",
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
