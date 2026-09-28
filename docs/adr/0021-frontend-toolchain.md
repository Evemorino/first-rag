# ADR-21 审阅页改用**受限引入的前端工程**：Vite + React + Zustand + Tailwind + TanStack Query

- **状态**：Accepted（2026-09-28，用户选定技术栈；本 ADR **取代 ADR-20 的第 1 条**，ADR-20 第 2、3 条继续有效）
- **日期**：2026-09-28
- **素材**：PRD v0.9.0 的 NFR-009（改写）/ NFR-012（新增）；ADR-20（被部分取代）
- **实施位置**：`web/`（前端工程，产物不入库）→ 构建产物 = `web/dist/`：`index.html` 由 `GET /` 返回，`assets/*` 由 FastAPI 挂静态资源（见下"产物怎么被服务"）；新增 `make ui` / `make ui-dev`

## 产物怎么被服务（以及 Docker 为什么不参与）

Vite 的产物**不是单个文件**，是 `dist/index.html` + `dist/assets/<hash>.js|.css`。所以"由 `GET /` 返回"只解决一半，另一半要明确：

- `GET /` 返回 `dist/index.html`（保持**入口仍是 `/`** —— 这是 v0.8 决策记录 Q5 的结论，不推翻）；
- `app.mount("/assets", StaticFiles(directory=web/dist/assets))` 提供带 hash 的 JS/CSS；Vite 默认 `base='/'`，asset URL 正好是 `/assets/...`，**不需要改 `base`**；
- **不需要 SPA 兜底路由**：详情页用的是 hash 路由（`#/entry/<id>`），刷新时请求的还是 `/`，不是深层路径 —— 这是当初选 hash 路由换来的好处之一；
- **Docker 不参与**：`docker-compose.yml` 里只有 Qdrant（文件头写着"App stays on the host"，因为采集要读 `$HOME` 下的产品目录）。前端产物是**静态文件**，由宿主机上的 FastAPI 进程（`.venv`）直接读盘返回 —— **不构建镜像、不加容器**，NFR-008「唯一常驻服务是 Qdrant」与 VI「技术克制」都保持成立。若哪天要把前端也进容器，那是改 NFR-008/VI 的独立需求。

## 背景

v0.8 的审阅页是"零构建单文件"（ADR-20 第 1 条），当时给出的代价栏写着"交互复杂度上限较低（无组件生态、无响应式框架）"。用户（前端工程师）看过实际页面后反馈"纯 HTML 有点 low"，并明确希望换成熟悉的前端栈 —— **这属于需求变更**（NFR-009 原文写死"不引第二套工具链/无 npm/无构建"），因此先改 PRD，再动代码。

核心事实：**后端接口契约不需要动**。页面只是 7 个 JSON 端点（`/types`、`/entries`、`/entries/{id}`、`PATCH`、`delete`、`restore` + `/ask` 一族）之上的薄壳，`GET /` 的入口职责也不变 —— 换的只是"产物从哪来"。

## 备选与被否的理由

| 方案 | 为何没选 |
|---|---|
| **Vite + React + Zustand + Tailwind + TanStack Query**（采用） | 工具链轻、无服务端概念要理解、用户已有熟练度；TanStack Query 正好覆盖"缓存 + 乐观更新 + 409 冲突"这三件写能力必然遇到的事 |
| **Next.js 静态导出**（`output: 'export'`） | 能用，但 Next 的差异点（SSR/RSC/ISR/edge）在这个项目**一条都用不上**（单用户、本机、离线、无鉴权、无 SEO），却要多背一套心智负担与更重的构建；更大风险是"将来有人顺手加个 Route Handler"，于是悄悄多出一条**写入边界门禁看不见**的写路径 |
| **Next.js 当 BFF**（Route Handlers / Server Actions 代理） | **不选**：多一个常驻进程与运行时，校验规则要写两遍；`scripts/write_boundary_check.py` 只扫 `src/`（Python），Node 侧的写库/写盘是它结构上看不见的路径 —— 等于把宪法 V 的机械保证让掉一半 |
| **全栈重写**（丢掉 FastAPI，Next + 别的存储） | **不选**：11 个采集插件、幂等 uuid5 ID、脱敏、关联边算法、mutmut 变异基线、15 个门禁钩子全要重来；这不是本仓库的一次增量，而是另一个项目 |

## 证据

- 写入边界门禁的扫描面是 `src/`（`scripts/write_boundary_check.py`），这是"前端不得成为第二个后端"必须写成硬约束、而不是靠自觉的**机械理由**。
- 本 ADR 之前，ADR-20 已经把代价写清："若页面长成多视图应用，本 ADR 需要重新评估" —— 本次正是那次重新评估，结论是**换工具链、不换接口**。
- v0.8 的页面规模（单文件 282 行、6 个端点、两个视图）说明这里换栈的收益主要在 **DX 与可维护性**（组件、类型、HMR、测试），不在渲染能力 —— 所以验收标准（AC-018~021）一行不改。

## 后果与可逆性

- **可逆性**：高。接口契约是稳定的那一半；前端是薄壳，换框架不动后端一行。
- **代价（如实记）**：① 跑起来的门槛从"一个 `.venv`"变成 "`.venv` + Node 工具链"，备份恢复多一步 `pnpm install`；② CI 变慢（Node 安装 + 构建）；③ 维护面变宽（同一份数据字段可能前后端两处出现）—— 缓解办法是**由 `openapi.json` 生成 TS 类型**并进 CI 断言，把"改字段忘一侧"变成红灯；④ 前端**没有**变异测试（Python 侧的 `mutmut` 不覆盖 TS）。
- **边界**：前端只准消费 `src/api/app.py` 已登记的端点；不直连 Qdrant、不持 Ark 密钥、不自己写库（NFR-009 第 ④ 条）。

## 决策

1. 技术栈：**Vite + React + TypeScript + Zustand + Tailwind CSS + TanStack Query**。状态分工写死：**服务端状态归 TanStack Query**，Zustand 只放 UI/客户端状态（选中项、筛选条件、主题）—— 两套状态系统不许争同一份数据。
2. **产物不入库**（与 `coverage.xml`、`mutants/` 同规矩）；**Node 版本与 uv 一样钉在 `mise.toml`**，lockfile 入库；`make ui` 构建、`make ui-dev` 开发（dev server 把 `/entries` 等代理到 8300）。
3. **前端不得成为第二个后端**：只消费既有 JSON 端点。
4. **门禁对等**（NFR-012）：lint / 类型检查 / 单测 / 真浏览器冒烟四类，每类进 `gate-selftest`；CI 增 Node 步骤；**前端没有变异测试，此点必须写在文档里**。
5. **取代关系**：本 ADR 取代 ADR-20 **第 1 条**（零构建）；ADR-20 **第 2 条**（只绑 `127.0.0.1`）与**第 3 条**（写接口同源校验）**继续有效**，与用什么框架无关。
6. **包管理器 = pnpm**：`web/pnpm-lock.yaml` 入库；CI 与 `gate-selftest` 一律 `--frozen-lockfile` + 复用 pnpm 全局 store 离线安装。
7. **Playwright 只进 CI**，不进提交钩子 —— 提交钩子保持秒级，浏览器冒烟放在 CI 的独立 step（含 `playwright install --with-deps chromium`）。
8. **类型不手抄**：由 FastAPI 的 `openapi.json` 生成 TS 类型，**CI 断言生成结果与仓库一致**；这样"改了后端字段忘了前端"会在 CI 变红，而不是在浏览器里静默少一个字段。
9. **"前端不是第二个后端"用门禁守，不靠自觉**：ESLint 规则禁止 `qdrant-client` 一类客户端 import、禁止读取 `ARK_*` 密钥（`no-restricted-imports` + `no-restricted-syntax`）——把第 3 条从 review 变成机械判据。
10. **配套两条**：README 的用例数**分列 Python / 前端**（否则一个总数会被误读）；`size_guard` 扩到 `web/src/**/*.{ts,tsx}`，沿用同一套阈值。
