# PRD：first-rag 个人学习记忆 RAG 系统

**版本**：v0.7.4
**日期**：2026-09-26
**状态**：Active（v0.1 范围实现完毕；v0.7 扩采集面 4 → 11 插件；v0.7.1 修掉 v0.7 里「共用解析器」与契约「插件不互相 import」的互相矛盾，裁决为各自实现 + 等价性测试；v0.7.2 订正 NFR-001 对 `mode=ro` 的过度承诺；v0.7.3 订正 NFR-006 计时口径并补实测；v0.7.4 记录「验证方法覆盖共享产物」风险并给快照加写保护；v0.7.5 回灌三处 PRD 文档漂移（AC-016 补「默认拒跑、退出码 2」、NFR-006 正文同步按天口径、§3.1 In Scope 补 hermes）。见 §13 变更记录）
**需求来源**：用户访谈（2026-09-17 ~ 09-18 grilling 会话）；先前批准的实施计划已降级为下游参考材料（`~/.claude/plans/soft-wishing-karp.md`）

---

## 1. 背景与目标

### 1.1 问题

用户（单人开发者）日常在多个 AI 编码工具（Claude Code、codex、kimi-code、trae 等）中工作，每天产生大量会话记录：学习进展、踩过的坑、突发的想法、对方法的反思。这些内容：

- 分散在各工具自己的存储里，格式互不相同，无法统一检索；
- 原始会话冗长，有价值密度低，直接存等于垃圾场；
- 隔周就忘，"我之前在哪里踩过这个坑？"无法回答。

### 1.2 目标

构建一个**个人学习记忆系统**：自动采集当日多工具会话素材 → LLM 蒸馏为结构化知识条目 → 嵌入并存入 Qdrant → 支持带日期/类型/项目过滤的语义检索，回答必须带引用。

核心价值：**让"过去的自己"可被查询**。

### 1.3 非价值（明确不追求）

不做团队知识库、不做通用笔记软件、不追求实时性（天级延迟可接受）。

---

## 2. 用户角色与场景

**角色**：单用户 nava（开发者，中文为主，同时使用多个 AI 编码工具，工具集合随时间增减）。

### 场景 US-1：每日自动沉淀（P1）

每天晚间跑一次 sync，系统自动采集当天各工具会话、git 提交、手动快记，蒸馏成结构化条目入库。用户不写任何额外东西，第二天能问"我昨天学了什么"。

### 场景 US-2：带过滤的语义检索（P1）

用户问"我在 ark 代理上踩过什么坑？上周的"、"最近的报错有哪些"。系统按语义检索 + 类型/日期过滤，回答每条结论附引用（`[date] type: 摘要`）。

### 场景 US-3：手动快记（P2）

一句话想法随手记（CLI `make log` 或 API `POST /log`），落到 `notes/inbox.md`，随当日素材一起蒸馏入库。

### 场景 US-4：采集范围手动选择（P2）

`make scope` 展示 工具×项目 矩阵（含最近会话日期、条数等元信息），用户勾选哪些工具、哪些项目纳入采集，结果写入配置文件。

### 场景 US-5：蒸馏质量校准（P3）

用户修改蒸馏 rubric 或更换蒸馏模型后，对某一天重蒸馏，系统给出新旧条目集 diff（新增/消失/改写），用户过目后决定应用（该日条目整组替换）或丢弃。

### 场景 US-6：新工具接入（P3，扩展性验证）

新的 AI 编码工具出现后，按 `_template/` 写一个插件（实现 `discover()` + `parse()`），放入 `src/plugins/<product>/`，无需改核心代码即被 registry 自动发现。

---

## 3. 范围（In Scope / Out of Scope）

### 3.1 In Scope（v0.1）

- 采集（11 源）：claude-code、codex、kimi-code、opencode、zcode、qoder、qoder_cn、workbuddy_ai、hermes（raw 会话蒸馏路径）+ trae_work_cn、trae（pre-summarized 轻转换路径）+ git 提交 + 手动快记
- 插件化采集架构（registry + `_template`）
- 采集范围交互选择（`make scope`）
- 蒸馏：可配置价值门槛 + 行为证据 + 新颖度去重 + 每日条数熔断
- 入库：嵌入 + Qdrant 幂等 upsert + related 关联边
- 检索：向量 + payload 过滤 + 引用式回答 + 关联边扩展（可配置）
- 运维：raw 保留策略、重蒸馏对照、FastAPI 薄壳（按需启动）

### 3.2 Out of Scope（非目标，v0.1 明确不做）

- **NG-001** cron 自动安装（提供示例配置，装不装用户定）
- **NG-002** Web 前端（FastAPI 只提供 JSON API；页面属未来版本）
- **NG-003** MCP 接入（仅在架构上预留 Streamable HTTP 挂载位，不实现）
- **NG-004** BM25 / 混合检索 / rerank（纯向量 + 过滤）
- **NG-005** 鉴权与多用户（单用户本机系统）
- **NG-006** 图数据库（related 边存 Qdrant payload，不引入 Neo4j 等）
- **NG-007** 对源产品目录的任何写操作（采集只读）
- **NG-008** 自动/定时重蒸馏与自动校准（US-5 为手动触发）
- **NG-009** 跨设备同步、云端部署（本机系统；备份=拷目录）
- **NG-010** ~~zcode、qoder 采集插件~~ **已撤销（v0.7）**：两源现有真实数据（zcode 15 会话 / 1863 消息；qoder 3 会话 / 38629 行；qoder_cn 12 会话 / 34513 行），按 FR-002 接入
- **NG-011** trae 系产品的加密数据库解析（只使用其明文 `session_memory.jsonl`）
- **NG-012** `~/.workbuddy` 采集（该目录无会话数据，只有守护进程日志与连接器凭据；WorkBuddy 的会话在 `~/.workbuddy-ai`，已由 FR-002 覆盖）

---

## 4. 功能需求（FR）

### 采集域

- **FR-001** 系统必须实现插件化采集架构：每个产品一个插件（`src/plugins/<product>/`，含 `discover()` 返回当日素材引用、`parse()` 解析为统一中间格式），registry 自动扫描注册；插件目录消失时 registry 静默跳过（无报错、无残留状态）。
- **FR-002** v0.1 必须交付 4 个插件：claude-code、codex、kimi-code（raw 路径：完整解析会话 JSONL，提取用户消息、助手文本、工具报错）；trae（pre-summarized 路径：读取 `session_memory.jsonl` 的 `{intent, actions, outcome, learned}` 记录做轻量转换直接并入，不做二次蒸馏，避免重复消耗 token 与再幻觉）——**v0.7 起本插件更名为 `trae_work_cn`**（它读的一直是 `~/.trae-cn`），`trae` 之名转给 `~/.trae`，见 FR-002a。trae 类型映射规则：每条记录生成一条条目，text 由 `learned`/`outcome` 字段拼装，type 由 `config/schema.json` 的 `trae_type_map` 配置（键为记录字段名，默认全归 `reflection`），未匹配键落默认值。交付按纵切滚动：claude-code 先行验证插件架构，codex / kimi-code / trae 依次补齐；四插件全部通过验收方为 v0.1 完结（阶段四质询决议 Q2-A）。
- **FR-002a**（v0.7 新增）采集面扩展到 **11 个插件**，新增 7 个，全部遵循 FR-001 的插件契约与 NFR-001 的只读/凭据边界。**改名**：原 `trae` 插件重命名为 `trae_work_cn`（它读的一直是 `~/.trae-cn`，名实不符）；`trae` 这个名字转给 `~/.trae`。改名会改变条目 ID（ID 含 source 字段），存量迁移见 §8。
  - **A 族 · Claude/JSONL 系**
    - `qoder` / `qoder_cn`：`projects/<slug>/<uuid>.jsonl`。两者 schema 逐字段相同，**语义上同源**。但**各自实现解析器**——插件之间不得互相 import（契约第 6 条 / `scripts/lint_layers.py` 强制），共用逻辑唯一合法去处是 registry，而让 registry 长出某个厂商的解析职责与「纯核薄壳」相冲。代价是两份实现会漂移，因此 MUST 配一条**跨插件等价性测试**：同一份 fixture 喂给两个解析器，断言除 `source` 外输出逐字段相同——漂移由机械测试抓，不靠注释提醒。**时间戳双编码**：消息类记录为 ISO 8601 UTC 字符串，bookkeeping 类为 epoch 毫秒整数，同一文件内混排——解析 MUST 先判类型，不得假定单一编码。
    - `workbuddy_ai`：`~/.workbuddy-ai/projects/<slug>/<uuid>.jsonl`。**CodeBuddy 系 schema，不与上面两者共用解析器**（连时间戳策略都相反）。时间戳统一 epoch 毫秒。**正文 MUST 只取 `<user_query>…</user_query>` 包裹的内容**：其余是对话摘要、任务通知、"Please continue with…"、`<system-reminder data-role="user-context">` 等注入物；取不到该标签的记录 MUST 不产生素材。实测（2026-09-25）116 条 user 记录中仅 **47** 条含该标签——且该标签总在整条记录的**末尾**，首条样本 12392 字里 12354 字是注入物（含用户的 `SOUL.md` 全文），只有 38 字是本人输入；不做提取就等于把注入模板当素材。
    - `trae`：`~/.trae/memory/projects/<path>/YYYYMMDD/session_memory_*.jsonl`。与 `trae_work_cn` 同格式、同映射规则（含 `trae_type_map`），仅 `MEMORY_DIR` 不同。同样**各自实现**（理由同上），并由同一条跨插件等价性测试覆盖。
  - **B 族 · SQLite 系**（一律以 `mode=ro` URI 只读打开，见 NFR-001）
    - `opencode`：`~/.local/share/opencode/opencode.db` 的 `session_message` 表（`data` 列为 JSON）。时间戳 epoch 毫秒。
    - `zcode`：`~/.zcode/cli/db/db.sqlite` 的 `message` + `part` 表。时间戳 epoch 毫秒。**MUST 排除 `message.data.synthetic` 为真的记录**：实测 343 条 user 消息中 115 条是 fork 提示与工具结果回填等注入物，真用户输入仅 228 条。
    - `hermes`：`~/.hermes/state.db`。**该源当前零真实数据**（库建于 2026-09-24，`messages` / `sessions` 均 0 行，`freelist=0` 确认为真空库），按 schema 实现。其验收依据**仅为构造的 fixture，未经真实数据验证**；待该源产生数据后补验，在此之前不得据其声称任何 AC 通过。
- **FR-002b**（v0.7 新增）`~/.workbuddy` 与 `~/.workbuddy-ai` 是**同一个产品的两个数据根**（同一 connector UUID、同一 device-id、逐字节相同的连接器 README）：前者是守护进程/连接器 home，**无任何会话数据**，只有启动日志与 OAuth 凭据；后者是 AI 客户端 home，会话全在这里。因此采集只读 `~/.workbuddy-ai`（NG-012 排除前者）。
- **FR-003** 系统必须采集 `config/repos.txt` 所列仓库的当日 git 提交（`--stat`）。
- **FR-004** 系统必须支持手动快记：CLI `make log m="..."` 与 API `POST /log` 共用同一实现，追加带时间戳的行到 `notes/inbox.md`。
- **FR-005** 系统必须提供 `make scope` 交互式选择器：展示 工具×项目 矩阵及元信息（最近会话日期、估算条数），用户勾选后写入配置文件；**纯文本配置文件是唯一事实来源**，选择器只是它的编辑器。

### 蒸馏域

- **FR-006** 每日采集结果必须先落盘为统一中间快照 `data/raw/YYYY-MM-DD.json`，内含本次蒸馏运行元信息（模型、rubric 配置哈希 / distill_version）。
- **FR-007** 蒸馏必须按可配置的价值门槛执行：`config/schema.json` 的 `distill` 节定义 include_signals / exclude_signals / keep-drop 示例 / novelty_threshold / max_entries_per_day / struggle_rounds，prompt 运行时拼装。
- **FR-008** 蒸馏必须提取行为证据：从会话中机械提取"挣扎轮次"（同一问题上 ≥ struggle_rounds 轮未解决再解决），作为高价值信号。
- **FR-009** 蒸馏必须做新颖度去重：候选条目先嵌入、检索库中已有条目，相似度超阈值则跳过或并入已有条目，不重复入库。
- **FR-010** 每日入库条数必须受 `max_entries_per_day` 熔断保护（默认 30），防止蒸馏失控或成本异常。
- **FR-011** 蒸馏前必须做正则脱敏（密钥、token、密码模式）。
- **FR-012** LLM 返回的类型若不在配置的类型枚举中：重试一次并在 prompt 中纠正，再失败则丢弃该条（不落库、记录日志）。
- **FR-013** 手动快记与 trae 系的 pre-summarized 素材（`trae_work_cn`、`trae`，FR-002a）走轻处理路径直接并入（快记沿用用户标注的类型），不进入 LLM 蒸馏。

### 入库域

- **FR-014** 入库必须幂等：point ID = `uuid5(NAMESPACE, f"{source}|{date}|{content_hash}")`，重复 sync 同一天不产生重复数据。
- **FR-015** 条目 payload 必须含：`text, date, type, tags, source, project, created_at, source_refs, distill_version, related`。
- **FR-016** 类型枚举必须由 `config/schema.json` 配置驱动（每个类型含 name/desc），蒸馏 prompt 运行时从配置拼装；新增类型零代码迁移。
- **FR-017** 入库时必须构建 related 关联边：对每条新条目做相似度搜索，取 top-5 中 score > 0.75 的建立双向边，单条条目 related 上限 5；双向回填时若对方条目 related 已达上限 5，则跳过该方向的回填（保持确定性与幂等）。

### 检索域

- **FR-018** `ask` 必须支持语义检索 + payload 过滤（`--type`、`--since`/日期区间、`--project`），top_k 可配。
- **FR-019** 回答必须带引用：每条结论可追溯到 `[date] type: 摘要` 及其 source。
- **FR-020** 关联边扩展必须可配置（`retrieval.expand.mode`：`off` / `all` / 数值阈值），默认 `all`；扩展邻居在 LLM 上下文中标记为"关联补充"（prompt 注明酌情使用）；CLI 提供 `--no-expand` 单次关闭。**扩展按 ID 直接取数，不受 `--type` / `--since` / `--project` 过滤约束**——"关联补充"的语义正是越过当前过滤窗口找回上下文；它只进 LLM 上下文，不改写主检索结果（边界见 AC-004）。
- **FR-021** 检索配置（top_k、expand 各参数）统一放 `config/schema.json` 的 `retrieval` 节，CLI 与 API 共用。

### 运维域

- **FR-022** raw 快照保留策略必须可配置（`raw_retention_days`，默认 90，`null` = 永久），到期由 sync 顺手清理。
- **FR-023** 系统必须支持重蒸馏对照（US-5）：对指定日期重跑蒸馏，产出新旧条目集 diff，对齐规则：新旧条目同时满足（a）共享至少一个 source_refs 且（b）文本相似度 ≥0.85 则配对——配对且文本有差异记"改写"；仅出现在新集合记"新增"；仅出现在旧集合记"消失"。用户确认后整组替换该日条目或丢弃。
- **FR-024** FastAPI 薄壳必须提供：`GET /health`（Qdrant + 配置连通性）、`POST /log`、`POST /sync`（后台任务）+ `GET /sync/status`、`GET /ask`、`GET /ask/stream`（SSE 流式问答：`citations` → 逐块 `chunk` → `done`，生成中途失败补 `error` 事件；2026-09-25 用户批准新增，CLI 对应 `--stream`）；路由只做参数校验与调用核心函数层，不含业务逻辑。
- **FR-025** sync 必须以纯 CLI 形式可运行（`make sync [D=日期]`），不依赖 API 进程存活。

---

## 5. 非功能需求（NFR）

- **NFR-001（隐私）**：密钥只从环境变量读取（`.env`，gitignore）；蒸馏前正则脱敏；采集器对源目录严格只读。
  - **v0.7 · SQLite 源 MUST 用只读 URI 打开**：对产品源目录的任何 `sqlite3.connect()` 必须写成 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`。普通 `connect()` 会在源目录落 `-wal` / `-shm` / `-journal` —— 那就是写入，违反宪法 V，且违规不可恢复。此条**由 `scripts/write_boundary_check.py` 机械拦截**，不靠自觉（与 NFR-002 同级待遇）。
    - **已知边界（2026-09-26 实测订正）**：`mode=ro` **不是**「不落 sidecar」的保证，只保证**不改 `db` 文件本身、不写产品数据**。对照实验（干净 WAL 库，目录里只有 `t.db`）：`connect("file:…?mode=ro", uri=True)` 打开后目录变成 `['t.db', 't.db-shm', 't.db-wal']`——只读连接在 sidecar 不存在时会**自己造出**它们。推论两条：① 源目录**不可写**时 `mode=ro` 打不开（`attempt to write a readonly database`），插件据此降级为空数据并告警（NFR-004）；② `immutable=1` 虽零落盘，但**会忽略 `-wal`**（实测 opencode 的 `-wal` 达 5.6 MB，最新会话会静默丢失），**不得采用**。
  - **v0.7 · 凭据必须被双向排除**。源目录里混着凭据文件与凭据表，采集面从 4 个源扩到 11 个源之后，"顺手读进来"的概率不再是零。每个 B 族插件 MUST 用**表允许列表**（只读列出的表，而非排除黑名单），每个 A 族插件 MUST 用**路径允许列表**。已知需要排除的（接入时逐条核实并写进插件测试）：
    - 表：`opencode.db` 的 `credential` / `account` / `control_account`
    - 路径：`~/.zcode/v2/credentials*.json`、`~/.hermes/` 下的 `.env` 与 `auth.json`、`~/.workbuddy/connectors/`（OAuth 凭据与 device-id）、`~/.qoder-cn/` 下的 `state.json`
- **NFR-002（写入边界）**：所有运行时产物只允许写入 `./data/` 与 `./notes/` 两个根下；代码中不允许任何逃逸写入（临时文件用系统 tmp）。
- **NFR-003（幂等）**：见 FR-014/FR-017；任何重跑不产生重复数据或重复边。
- **NFR-004（故障隔离）**：单个插件解析失败只跳过该插件并记录日志，不中断其他来源的当日 sync；插件目标目录消失视为正常情况（静默 no-op）。
- **NFR-005（可扩展性）**：新增类型（改配置）、新增产品（加插件目录）均不改核心代码。
- **NFR-006（性能）**：典型日（raw 提取 100K–1M 字符）sync 全程 ≤ 5 分钟。**计时口径按「天」不按「源」**（v0.7.3 订正：真实 sync 把当天全源素材合并后统一分批，成本不按源可加，逐源计时从原理上测不出总预算）——单源耗时只作**诊断记录**，不作验收尺子。**已实测**：2026-09-26 最忙日 09-25（1,928,545 字符 / 18 批 / 3 轮）全源端到端 **248.7s / 300s（83%）**，未突破；采样面仅 1 天、无 run-to-run 方差样本，余量在方舟延迟波动下不保证次次够。当日计时逼近 300s 即触发处置（见 §9 风险表），**不得在无实测的情况下放宽预算**。
- **NFR-007（可备份/可迁移）**：备份 = 复制 `data/` 目录；换机器 = 拷贝 `data/` + `config/` + `notes/`。
- **NFR-008（可逆性）**：Qdrant 是唯一常驻外部服务（Docker，数据在宿主机 `./data/qdrant/`）；删容器+删目录即完全移除；应用层依赖全部装项目 `.venv`。

---

## 6. 数据存放与生命周期（强制）

| 位置 | 内容 | 生命周期 | 进 git |
|---|---|---|---|
| `./data/qdrant/` | 向量库全量（条目 + payload + related 边 + 溯源） | 项目终身 | ❌ |
| `./data/raw/YYYY-MM-DD.json` | 当日原始素材 + 蒸馏运行元信息 | `raw_retention_days`（默认 90，`null` 永久），sync 清理 | ❌ |
| `./notes/` | 手动快记（源素材） | 项目终身，源头不删 | ❌（含个人笔记） |
| `./config/` | schema.json、范围选择结果、repos.txt | 项目终身 | ✅（无密钥） |
| `.env` | 密钥 | — | ❌ 永远 |

配套规则：

1. 运行时写入只限 `./data/`、`./notes/` 两根（NFR-002）。
2. `data/`、`.env`、`notes/` 必须出现在 `.gitignore` 初版，属实施第一步验收项。
3. 源产品目录（`~/.claude`、`~/.codex`、`~/.kimi-code`、`~/.trae-cn`、`~/.trae`、`~/.qoder`、`~/.qoder-cn`、`~/.workbuddy-ai`、`~/.local/share/opencode`、`~/.zcode`、`~/.hermes`）只读；去重状态以 Qdrant 幂等 ID 为准，不向源目录写任何标记。SQLite 源另有 NFR-001 的 `mode=ro` 硬性要求。

时区统一 `Asia/Shanghai`；"当天"的判定以条目自身时间戳折算到该时区为准。

---

## 7. 技术决策记录（ADR 摘要）

| # | 决策 | 理由 |
|---|---|---|
| ADR-1 | Qdrant 本地 Docker（唯一容器化组件） | 唯一有状态服务，容器化收益最大；应用需读 home 下多产品目录，塞进容器丧失隔离意义 |
| ADR-2 | 应用跑宿主机 `.venv`；做页面时再容器化服务层 | 同上；核心为纯函数层，届时只加 Dockerfile 不改代码 |
| ADR-3 | 方舟 Ark API（OpenAI 兼容）做嵌入与蒸馏/问答 | 用户已订阅；确切可用模型 ID 在 setup 时真实调用验证 |
| ADR-4 | FastAPI 薄壳按需启动（`make serve`），不做守护进程 | v1 日常仅每日 sync + 偶尔 CLI 提问；cron 只跑 CLI，与 API 零耦合 |
| ADR-5 | 采集插件化：`discover()`+`parse()` 契约 + registry 扫描 + `_template` | 产品随时间增减是常态；插件消失=静默 no-op（NFR-004） |
| ADR-6 | 素材分 raw / pre-summarized 两类，trae 走后者轻转换 | 省 token、避免对已蒸馏内容的再幻觉 |
| ADR-7 | related 边存 Qdrant payload，不引入图数据库 | 单用户数据量小，一跳扩展够用（NG-006） |
| ADR-8 | 蒸馏价值门槛 = 可配置 rubric + 行为证据 + 新颖度去重，而非固定条数上限 | "为什么是 15 条"之问的结论：上限只作熔断（FR-010），不作质量标准 |
| ADR-9 | raw 默认保留 90 天且可配置 | raw 是格式稳定的审计材料，支持重蒸馏对照（US-5）；磁盘实测每天 ≤2M，非约束 |
| ADR-10 | 关联扩展三模式可配置（off/all/阈值），默认 all | 远关联价值高；噪声用上下文标记交给 LLM 判断而非硬阈值拦截 |
| ADR-11 | PRD 为唯一需求事实来源；spec-kit 的 spec 模板已加 In Scope / Out of Scope 强制节，必须引用 PRD 编号 | spec-kit 原模板无非目标强制节（2026-09-18 查证 github/spec-kit templates/）；防止 PRD↔spec 漂移 |

---

## 8. 边界条件与异常处理

| 情形 | 处理 |
|---|---|
| 某产品目录消失/为空 | 插件静默 no-op，日志记录（NFR-004） |
| 单插件解析抛异常 | 跳过该插件，当日其他来源继续（FR 同 NFR-004） |
| LLM 返回非法 JSON | 重试一次；仍失败则当日蒸馏记为失败，raw 已落盘可重跑 |
| LLM 返回未知 type | 重试一次（prompt 纠正）；仍失败丢弃该条（FR-012） |
| 嵌入/API 网络失败 | sync 失败退出非零码；已入库部分靠幂等可安全重跑 |
| Qdrant 未启动 | sync/ask 启动时探活，给出明确错误提示（`make up` 引导） |
| 当日无任何素材 | 正常 no-op，写空 raw 文件（保持重蒸馏基线完整） |
| 单日素材异常大 | 蒸馏前截断至配置上限 + FR-010 条数熔断 |
| 手动快记未标类型 | 默认 `reflection`；轻处理路径不改类型（需改类型请在快记时标注） |
| 跨时区会话时间戳 | 统一折算 Asia/Shanghai 后判定归属日（§6） |
| 重蒸馏应用后旧条目 | 整组替换（先删该日全部再插新组），事务性由幂等 ID + 删除顺序保证 |
| **v0.7** 插件改名导致条目 ID 变化（`trae` → `trae_work_cn`） | 条目 ID = `uuid5(source\|date\|content_hash)`，改 source 等于换 ID：旧 9 条会变成孤儿（`source="trae"`），新插件再采一遍又会按新 ID 重新蒸馏入库。处理：**先跑一次性迁移脚本**（按 `source="trae"` 捞出旧点 → 用新 source 重算 ID → upsert 新点 → 删旧点），再启用新 `trae` 插件。迁移脚本 MUST 幂等、可 dry-run，且**独立校验**（迁移前后条目总数与正文哈希集合必须逐一对应）。**顺序不能反**——先上新插件会让同一条素材以两个 ID 并存 |

---

## 9. 依赖、假设与风险

**依赖**：Docker（Qdrant）、Python 3.13、方舟 Ark API 可用性、各产品会话文件格式保持可解析。

**假设**：

- 各产品路径/格式可能随版本漂移——插件 parse 层隔离，格式变更只改插件（ADR-5）。
- 方舟 embedding 具体模型 ID 与维度以控制台真实调用为准（setup 时验证，建集合动态读维度）。

**风险**：

| 风险 | 缓解 |
|---|---|
| 蒸馏质量不达预期 | FR-007/008/009 三层门槛 + US-5 重蒸馏对照回路持续校准 |
| 产品格式漂移导致采集失效 | NFR-004 故障隔离；失败显式入日志而非静默丢数据 |
| 隐私：素材发往方舟蒸馏 | 用户本就经方舟代理使用这些工具，数据流向一致，无新增暴露；README 注明 |
| 成本失控 | FR-010 熔断 + pre-summarized 路径省 token（ADR-6） |
| **v0.7**：采集面 4 → 11 源，NFR-006 的 5 分钟预算被撑破 | **计时按「天」不按「源」**（v0.7.3 订正：真实 sync 把当天全源素材合并后统一分批，成本不按源可加，逐源计时测不出总预算）。**已实测（2026-09-26，最忙日 09-25，全源 1,928,545 字符 / 18 批 / 3 轮）：端到端 248.7s，占 300s 的 83%，未突破**。采样面仅 1 天、无 run-to-run 方差样本，51s 余量在方舟延迟波动下不保证次次够——该缺口如实留在此处。真逼近 300s 时的处置顺序：① 源级 scope 勾选（FR-005 已有，用户可手动关掉大源）② 大源按日切片/增量水位 ③ `parallel_workers`/批次大小 ④ 重新议 NFR-006 的预算。**不得在无实测的情况下放宽预算** |
| **v0.7**：注入物被当成用户输入蒸馏 | 已在 FR-002a 逐源写明过滤规则（zcode `synthetic`、workbuddy `<user_query>`）；每个源 MUST 带一条"注入物不得产生素材"的插件单测，用真实文件里数出来的计数做断言 |
| **v0.7**：凭据被读进素材 | NFR-001 双向允许列表 + `scripts/write_boundary_check.py` 机械拦截 `mode=ro`；插件测试断言凭据路径/表 access 会抛异常而非静默返回 |
| **v0.7**：hermes 零数据，插件"通过"实际是空转 | FR-002a 已明示其验收依据仅构造 fixture、未经真实数据验证；在补验前不得以其声称任何 AC 通过 |
| **v0.7.2**：只读连接在源目录落 `-wal`/`-shm` sidecar（2026-09-26 实测，非推测） | 这是 SQLite 在「非 immutable 且库为 WAL 模式」下的固有行为，**没有任何连接参数能同时做到「读 WAL 里最新内容」与「不落 sidecar」**（`immutable=1` 能零落盘但会忽略 `-wal`，静默读到过期数据，故不可选）。已把边界写进 NFR-001「已知边界」并降级 AC-017① 的表述，不做代码规避。残留风险：源目录不可写时插件打不开库、只能降级为空数据（`_query` 的 `except sqlite3.Error`），表现为「该源当天无素材」而非报错——由 NFR-004 的日志显式记 warning 兜住 |
| **v0.7.4**：**验证方法本身会覆盖共享产物** —— `data/raw/<day>.json` 被单源验证跑覆盖（2026-09-26 实测已发生） | 起因不是代码缺陷而是方法缺陷：AC-015 要求"每个新源挑一个有真实数据的日期**单独** `make sync D=<date>`"，而 `save_snapshot` 是裸覆写，于是每一天的单源跑都把当天**多来源**快照替换成 1 条素材的单来源快照。Qdrant 不受影响（条目是 upsert），受害的是 `data/raw/` 作为 `make redistill` **重放基线**的性质：之后对这些日期做 diff，比的是"1 条素材的日"与"库里 27 点"，**不报错，只给一个错的 diff**。处置：① `save_snapshot` 增写保护——同日快照素材数会变少即拒写（`SnapshotShrinkError`），`ALLOW_SHRINK=1` 是明路（窄 scope 重跑是合法操作，所以留开关而不是一刀切）；② 已恢复 09-18 / 09-23 两份（凭 §8 迁移备份还原）；③ 09-21/22/24/25/26 无备份，**不可恢复**，已记录。**残留风险**：写保护只看「素材数」，所以"同数量但换了来源"的覆盖拦不住；且只要还用"单源跑一天"这种方法做验证，就需要人记得 `ALLOW_SHRINK=1` 的语义。改用「先复制 `data/raw/` 再验证」或改走 `gather` 的纯函数路径可根除，见 tasks T060 |

**待澄清项**：

- `[NEEDS CLARIFICATION]` 无——grilling 前沿已清空（2026-09-18）。若阶段四质询发现新问题，按 prd-workflow 回退规则处理。

---

## 10. 验收标准（AC）

- **AC-001** `make up` 后 Qdrant dashboard（:6333/dashboard）可访问；`make serve` 后 `curl :8300/health` 返回各组件连通状态。
- **AC-002** 用当日真实数据 `make sync`：`data/raw/<date>.json` 存在且含蒸馏元信息；Qdrant points > 0；payload 字段齐全（§FR-015 全部字段）。
- **AC-003** 同一日期重复 `make sync`：points 数不变、无重复边（幂等）。
- **AC-004** `make ask Q="..." --type error --since 7d`：**主检索结果**仅含 error 类型、日期过滤生效；**关联补充条目不受该过滤约束**（FR-020 默认开启，单独标记"关联补充"，可 `--no-expand` 关闭）；回答每条结论带 `[date] type: 摘要` 引用。
- **AC-005** 默认检索含关联扩展且扩展条目标记"关联补充"；`--no-expand` 后扩展条目消失。
- **AC-006** 将某产品目录临时改名后 `make sync`：sync 成功（该来源 no-op），日志有记录。
- **AC-007** `make scope` 勾选后配置文件更新，sync 按新范围采集。
- **AC-008** 将 `raw_retention_days` 设为 0 后 sync：过期 raw 文件被清理，Qdrant 条目不受影响。
- **AC-009** `make sync D=<过去日期>`：历史日期可补跑；补跑与当日流程行为一致。
- **AC-010** 修改 rubric 后对某日重蒸馏：diff 输出新增/消失/改写清单；确认应用后该日条目整组替换。
- **AC-011** `config/schema.json` 新增一个类型后 sync：无需改代码，新类型可被蒸馏 prompt 识别。
- **AC-012（脱敏，FR-011/NFR-001）** 快记中植入伪造密钥模式（如 `sk-test-` 前缀）后 sync：库中条目、原始快照、日志均不出现该字符串。
- **AC-013（未知类型，FR-012）** 诱导蒸馏返回配置外类型：重试一次后丢弃该条并留日志，其余条目正常入库。
- **AC-014（条数熔断，FR-010）** 将 `max_entries_per_day` 临时设为 3 跑超量素材日：恰好入库 3 条，日志含熔断记录，sync 正常结束（非失败退出）。
- **AC-015（多源采集，FR-002a）** registry 注册的插件数 = 11。对每个新增源，挑一个有真实数据的日期单独 `make sync D=<date>`：该源当日素材非空且入库成功；且注入物按 FR-002a 的规则被排除（zcode：`message.data.synthetic` 为真的记录不入库；workbuddy_ai：条目正文只含 `<user_query>` 内的内容，不含对话摘要/任务通知）。**hermes 不适用本 AC**——其库为空，见 AC-017。
- **AC-016（源改名迁移，§8）** 迁移脚本 `--dry-run` 列出将被改写的 9 条（`source` 由 `trae` 改为 `trae_work_cn`）；`--apply` 之后：Qdrant 中 `source="trae"` 的点数为 0，`source="trae_work_cn"` 的点数与之相等，且**正文哈希集合逐一对应**，条目总数不变；再次 `--apply` 无任何副作用（幂等）——由**默认拒跑 + `--force` 只迁旧路径**保证，故重跑会以**退出码 2** 停下而非静默重写。迁移判据是 **ref 路径**（旧数据在 `~/.trae-cn/`）而非 source 名——v0.7.1 之后 `trae` 这个名字被新插件合法复用。
- **AC-017（SQLite 只读与凭据边界，NFR-001）** ① 任一 B 族插件跑完后，产品源目录的文件列表与运行前逐一致（无 `-wal` / `-shm` / `-journal` 新增）——**该条在产品运行期间成立**（sidecar 由产品自己持有）；若产品已干净退出并删掉 sidecar，`mode=ro` 会把它造回来，这是 NFR-001 已知边界内的行为，不计为违规，但须在 T058 的实证中单独记录实测到的前置状态；② `scripts/write_boundary_check.py` 对 `src/` 中任何非 `mode=ro` 的 `sqlite3.connect()` 判红（含故意植入违规的 `make gate-selftest` 用例）；③ hermes 插件的验收**只能依据构造 fixture**，须在测试中显式标注"未经真实数据验证"。

---

## 11. 下游工件衔接

- spec（spec-kit /specify）从本 PRD 派生；其 In Scope / Out of Scope 节必须逐条引用本文档的 FR/NFR/NG 编号，不得改写措辞（ADR-11）。
- plan/tasks 依 prd-workflow 阶段七/九执行，并受 §12.3 过程约束（纵切顺序、user-handwritten 标注）；spec-kit 已于 2026-09-18 初始化（`.specify/` 就位，spec 模板已改造）。

---

## 12. 学习目标与过程约束（v0.2 新增）

本项目同时是用户的**学习项目**：学习收益是显性需求，与产品功能同级。本节不新增任何产品 FR，只约束构建过程。

### 12.1 学习机制（LG）

- **LG-001 Dogfood 自举**：在 plan 阶段定义里程碑节点（如：首个 AC 跑通、蒸馏首次上线、首个 ask 引用式回答），到点即 `make sync`，让系统采集自身的开发会话——既是真实数据测试，也是学习巩固。系统的第一批数据就是它自己的诞生史。
- **LG-002 纵切交付**：实施不按模块横切，按"端到端可运行的薄片"推进（嵌入 smoke → 采集直插无蒸馏 → +蒸馏 → +过滤检索 → +关联边 → …）。每片可运行、可观察，加层前后对比效果。
- **LG-003 预测-验证**：每个模块首次运行前，用户先写下预期行为（相似度量级、rubric 过滤结果等），运行后对照；偏差即学习信号。预测记录随手记入 `notes/`（随素材入库，本身成为学习反思条目）。
- **LG-004 Rubric 实验台**：FR-007 三层门槛 + US-5 重蒸馏 diff 作为 prompt engineering 的受控实验流程使用：改一版 rubric → 重蒸馏对照日 → 看 diff。
- **LG-005 ADR 用户写结论**：实施中新增的技术决策，AI 只提供素材与选项，结论由用户撰写；写不出来 = 未想清楚。

### 12.2 代码分工（约束）

- **用户优先手写**（AI 仅 review 与答疑，不代写）：核心逻辑层四模块——`ark_client`（嵌入调用）、相似度搜索与新颖度去重、幂等 ID 生成、蒸馏 prompt。例外：2026-09-20 用户逐任务明确授权 AI 实现 `ark_client`、`ids`、`distill_prompt` 与 `similarity` 的 T017 新颖度去重部分，授权仅限这些任务，须附带测试与验证证据。
- **AI 生成**（用户抽查理解即可）：脚手架类——Dockerfile / docker-compose、Makefile、FastAPI 路由样板、`.gitignore`、配置文件骨架。

### 12.3 下游影响

- plan（阶段七）：任务拆分必须按 LG-002 纵切顺序组织。
- tasks（阶段九）：核心模块默认显式标注 user-handwritten 并关联本节；T004/T012/T015/T017 已获 2026-09-20 逐任务 AI 实现授权；其余排期按用户手写速度估算，不按 AI 生成速度。
- 里程碑节点清单在 plan 阶段产出（LG-001）。

---

## 13. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-18 | 初版：grilling 全部结论沉淀（Q1–Q7 + 部署与数据存放决策 + spec-kit 模板改造衔接） |
| v0.2 | 2026-09-18 | 新增需求"学习收益最大化"：新增 §12 学习目标与过程约束（LG-001~005、代码分工、下游影响）；无产品 FR 变更 |
| v0.3 | 2026-09-18 | 阶段四质询修复验收覆盖缺口：补 AC-012（脱敏）/ AC-013（未知类型）/ AC-014（条数熔断），兜底 FR-010/011/012；Q2-A 决议：FR-002 明确纵切滚动交付、四插件全过验收为 v0.1 完结 |
| v0.4 | 2026-09-18 | 阶段五 checklist 修订 4 项缺陷：FR-002 补 trae 类型映射规则（trae_type_map）；FR-023 补重蒸馏精确对齐规则（source_refs+相似度≥0.85 配对）；FR-017 补回填超上限处置（跳过）；§8 快记类型改为"轻处理不改类型"（消除与 FR-013 的矛盾） |
| v0.5 | 2026-09-25 | 交付收尾：FR-024 扩一项，新增 `GET /ask/stream`（SSE 流式问答，2026-09-25 用户批准，CLI 对应 `make ask --stream`）——原 FR-024 只列四端点，流式是实施期新增范围，本次补记入变更记录；无其他 FR/NFR/AC 变更 |
| v0.6 | 2026-09-25 | 澄清过滤边界：FR-020 与 AC-004 原先字面冲突——AC-004 写"仅返回 error 类型、日期过滤生效"，而 FR-020 默认扩展开启会把过滤窗外的一跳邻居送进 LLM 上下文。本次把边界写死：过滤只约束**主检索结果**，关联补充**有意不受约束**（其语义就是越过窗口找回上下文），且它只进上下文、不改写主检索结果。同步修订 spec SC-003。**无代码变更**——该行为自 T030 起即如此，且有 `tests/integration/test_ask_filters.py` 钉住，本次只是把既有事实写进需求 |
| **v0.7.1** | 2026-09-25 | **同源插件对不共用解析器**（v0.7 的内在不一致修复）。v0.7 的 FR-002a 写"`qoder`/`qoder_cn` 共用解析器"，同批批准的契约第 6 条却写"插件之间不互相 import（`lint_layers` 强制），共用逻辑提到 `src/plugins/__init__.py`"——两条互相矛盾，而契约自己给的唯一合法共享点会让 registry 长出厂商解析职责（违宪法 II）。用户裁决：**各自实现**。FR-002a 两条同源描述改写，代价用一条**跨插件等价性测试**补偿（同 fixture 喂两个解析器，断言除 `source` 外逐字段相同）；契约规则 6 补明"同源插件对也不例外"。另：workbuddy_ai 的注入物实测计数更新为当日真值（116 条 user 记录 / 47 条含 `<user_query>`，替换 v0.7 的 89/32——该数字随使用增长，故同时写明**判据是标签而非比例**）。**无代码变更，无 AC 变更** |
| **v0.7.3** | 2026-09-26 | **NFR-006 计时口径订正 + 补实测**（T057 执行中发现）。① 订正：§9 原写"每接入一个源单独计时"，但真实 sync 的 `split_for_batches` 跑在**当天全源合并后的 materials** 上，成本不按源可加——逐源计时从原理上就测不出总预算，改为**按天计时**。② 补实测：最忙日 09-25（1,928,545 字符 / 18 批 / 8 workers = 3 轮）全源端到端 **248.7s / 300s（83%）**，未突破，附 3 条逐源数据点与 30 天离线扫描。③ 结论：**NFR-006 正文不动、预算不放宽**（PRD 自定纪律要求实测，此为实测）。④ 记录未测部分：仅 1 天样本、无方差样本，51s 余量不保证每次够 |
| **v0.7.2** | 2026-09-26 | **订正 NFR-001 对 `mode=ro` 的过度承诺**（用户批准的措辞订正，非范围变更）。NFR-001 原文由「普通 `connect()` 会落 `-wal`/`-shm`」隐含推出「`mode=ro` 不会」，**实测该隐含是错的**：干净 WAL 库经 `connect("file:…?mode=ro", uri=True)` 打开后目录会多出 `-wal`/`-shm`（只读连接自己造的）。订正内容：① NFR-001 增「已知边界」——`mode=ro` 保证的是不改 `db` 本身，并写明两条推论（源目录不可写时打不开、`immutable=1` 因忽略 `-wal` 不得采用）；② AC-017① 补明「该条在产品运行期间成立」，sidecar 被产品删除后的重建属已知边界、不计违规但须记录实测前置状态；③ 同步修订 spec 的 NFR 条目与 SC-019、契约规则 1。**无代码变更，无 FR 变更**——B 族三插件（T054–T056）自始即按 `mode=ro` 实现，本次是把真实边界写进需求，不是改行为 |
| **v0.7.4** | 2026-09-26 | **验证方法覆盖共享产物 → 快照加写保护**（T059 收尾时自查发现，用户裁定"恢复能恢复的 + 加写保护"）。事实：AC-015 的验证方法要求"每个新源挑一个有真实数据的日期**单独** `make sync D=<date>`"，而 `collect.save_snapshot` 是裸覆写——2026-09-26 16:13 那批单源跑把 09-18（8 条 → 1 条）、09-23（26 条 → 1 条）等多来源快照替换成单来源快照。**Qdrant 未受影响**（条目是 upsert；历史条目 27/21 点俱在），受害的是 `data/raw/` 作为 `make redistill` 重放基线的性质——此后对这些日期做 diff 会静默给出错误对照。变更：① §9 新增 v0.7.4 风险行；② `collect.save_snapshot` 增写保护（同日快照素材数变少即拒写，抛 `SnapshotShrinkError`），`sync.run(allow_shrink=…)` / `make sync ALLOW_SHRINK=1` 为明路（窄 scope 重跑是合法操作）；③ 09-18 / 09-23 两份凭 §8 的迁移备份已恢复，09-21/22/24/25/26 无备份不可恢复。**本版为风险+防护变更，无 FR/NFR/AC 变更**——写保护防的是数据资产，不是需求行为；需求侧的教训记在 §9 与 tasks T060 |
| **v0.7.5** | 2026-09-26 | **回灌三处 PRD 文档漂移**（最终 converge 时发现：v0.7–v0.7.4 四次变更每次都改了 PRD，但 spec / plan / README 均未同步，留下自相矛盾的副本）。本版只动 PRD 侧三处：① **AC-016 补明**「幂等由默认拒跑 + `--force` 只迁旧路径保证，重跑以**退出码 2** 停下」——该可观测行为自 v0.7.4 起就已如此，PRD 却记着「无 AC 变更」，属验收标准漏记；② **NFR-006 正文改写为按天计时**——v0.7.3 当时选择"正文不动、只记 §9"，代价是需求正文与风险表两处口径打架（即本轮 converge 的 F4/T065），本版补正；③ **§3.1 In Scope 补 `hermes`**——v0.7 变更记录写着「范围列出 11 源」，而那一行实际只列了 10 个，漏掉的正是已实现并交付的 `hermes`。**无代码变更。** 同步回灌：spec（FR-002a 的 qoder 解析器裁决与 workbuddy 计数 116/47、SC-011 按天口径、同步头）、plan（代码树漏 8 个模块、`chat`/`query` 签名、`trae_type_map` 读取断言、v0.7.3 实测）、README/AGENTS/Makefile/quickstart（`make ask` 的 `ARGS=` 写法共四处、端点清单、gate-selftest 与用例数、钩子枚举）、data-model（`content_hash` 量纲、schema 缺 4 键） |
| v0.7 | 2026-09-25 | **扩采集面 4 → 11 插件**。起因：交付收尾后对本机真实源做了一轮只读勘察，发现原 NG-010（"不做 zcode/qoder"）的判断已过时——两源现有真实数据（zcode 15 会话/1863 消息；qoder 3 会话/38629 行；qoder_cn 12 会话/34513 行）。变更：① §3.1 范围列出 11 源；② **撤销 NG-010**，新增 NG-011（trae 加密库不解析，只用明文 `session_memory.jsonl`）与 NG-012（`~/.workbuddy` 无会话数据，排除——它是守护进程 home，会话在 `~/.workbuddy-ai`）；③ 新增 FR-002a（7 个新源分两族：A 族 Claude/JSONL 系 `qoder`/`qoder_cn`/`workbuddy_ai`/`trae`，B 族 SQLite 系 `opencode`/`zcode`/`hermes`，逐源写明时间戳编码与注入物过滤规则）与 FR-002b（workbuddy 双数据根）；④ **`trae` 插件改名 `trae_work_cn`**，`trae` 之名转给 `~/.trae`——改名会改变条目 ID（ID 含 source），存量 9 条需迁移，见 §8；⑤ NFR-001 增两条机械约束（SQLite 必须 `mode=ro` URI；凭据按路径与表双向允许列表排除）；⑥ NFR-006 标注风险而不编造新数字；⑦ §6 规则 3 源目录清单扩到 11 个；⑧ §9 增 4 条风险；⑨ §8 增"插件改名导致条目 ID 变化"的处置与顺序约束；⑩ §10 增 AC-015（多源采集）/ AC-016（改名迁移）/ AC-017（SQLite 只读与凭据边界）。**本版为范围变更**（新增 7 个采集源、改名 1 个、新增 3 条 AC），须重跑受影响门禁；原 NG-010 的撤销依据是本机真实数据勘察，非推测 |
