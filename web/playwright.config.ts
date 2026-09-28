import { defineConfig } from "@playwright/test";

// 跑真后端：uvicorn 提供构建产物（web/dist）与 JSON 端点，Qdrant 由 `make up` 起着。
// 端口避开 8300，免得和你自己开着的 serve 打架。
const PORT = 8321;

// 嵌入维度（AC-001 实测：doubao-embedding-vision → 2048）。有两处要用到它，必须是
// 同一个数：探针建集合时的向量形状，与假 Ark 返回的向量长度 —— 长度对不上，第 ④ 步
// 编辑会因为"维度不匹配"而失败。单一定义放在这里。
export const EMBED_DIM = 2048;

// 假 Ark 的端口：只服务 embeddings，让冒烟不花钱、不联网，CI 也不需要 ARK_API_KEY。
const FAKE_ARK_PORT = 8322;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  use: { baseURL: `http://127.0.0.1:${PORT}` },
  webServer: [
    {
      // 第 ④ 步"编辑"要调 Ark 算向量，所以整条冒烟都得配一个假 Ark（scripts/fake_ark_server.py）。
      // 真调用验的是嵌入模型（AC-001，`make embed-test`），不是这 8 步 UI 行为。
      command: `.venv/bin/python scripts/fake_ark_server.py --port ${FAKE_ARK_PORT} --dim ${EMBED_DIM}`,
      cwd: "..",
      url: `http://127.0.0.1:${FAKE_ARK_PORT}/`,
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      // **先构建再起服务**：`GET /` 读的是 web/dist，而 dist 是构建产物 ——
      // 忘了 build 就会"测试跑在旧 bundle 上"（真踩过：e2e 找不到刚加的编辑表单，
      // 而类型检查、单测全绿）。把 build 放进 webServer 让这个坑不可能再出现。
      command: `pnpm --dir web build && .venv/bin/python -m uvicorn src.api.app:app --port ${PORT}`,
      cwd: "..",
      // 后端读 env 在每次调用时（config.env），所以这里注入就能盖住 .env；干净环境
      // （CI）里根本没有 .env，四个变量一个都不能少：ARK_BASE_URL 指到假 Ark，
      // 而 ARK_API_KEY / EMBED_MODEL 为空会在发出请求之前就抛 ConfigError。
      //
      // ARK_API_KEY 取 "fake" 这么短是**故意**的：值只要求非空（config.env），
      // 假 Ark 根本不看它；而 scripts/secret_scan.py 的赋值式规则拦 ≥6 字符的值，
      // 那道门禁该保持严格 —— 与其教它认"哪些值算占位符"，不如让这个值压根不像密钥
      // （真密钥有 40+ 字符，短值不可能是）。
      env: {
        ARK_API_KEY: "fake",
        ARK_BASE_URL: `http://127.0.0.1:${FAKE_ARK_PORT}/v1`,
        EMBED_MODEL: "fake-embed",
        CHAT_MODEL: "fake-chat",
      },
      url: `http://127.0.0.1:${PORT}/`,
      reuseExistingServer: true,
      timeout: 30_000,
    },
  ],
});
