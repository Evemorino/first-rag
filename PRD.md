# PRD：first-rag 个人学习记忆 RAG 系统

**版本**：v0.4
**日期**：2026-09-18
**状态**：Draft（grilling 已收敛，待阶段四 grill-me 质询）
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

- 采集：claude-code、codex、kimi-code（raw 会话蒸馏路径）+ trae（pre-summarized 轻转换路径）+ git 提交 + 手动快记
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
- **NG-010** zcode、qoder 采集插件（数据现状为空/不明，留待有真实数据后按插件模板接入）
- **NG-011** trae 加密数据库解析（使用其明文 `session_memory.jsonl`）

---

## 4. 功能需求（FR）

### 采集域

- **FR-001** 系统必须实现插件化采集架构：每个产品一个插件（`src/plugins/<product>/`，含 `discover()` 返回当日素材引用、`parse()` 解析为统一中间格式），registry 自动扫描注册；插件目录消失时 registry 静默跳过（无报错、无残留状态）。
- **FR-002** v0.1 必须交付 4 个插件：claude-code、codex、kimi-code（raw 路径：完整解析会话 JSONL，提取用户消息、助手文本、工具报错）；trae（pre-summarized 路径：读取 `session_memory.jsonl` 的 `{intent, actions, outcome, learned}` 记录做轻量转换直接并入，不做二次蒸馏，避免重复消耗 token 与再幻觉）。trae 类型映射规则：每条记录生成一条条目，text 由 `learned`/`outcome` 字段拼装，type 由 `config/schema.json` 的 `trae_type_map` 配置（键为记录字段名，默认全归 `reflection`），未匹配键落默认值。交付按纵切滚动：claude-code 先行验证插件架构，codex / kimi-code / trae 依次补齐；四插件全部通过验收方为 v0.1 完结（阶段四质询决议 Q2-A）。
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
- **FR-013** 手动快记与 trae 的 pre-summarized 素材走轻处理路径直接并入（快记沿用用户标注的类型），不进入 LLM 蒸馏。

### 入库域

- **FR-014** 入库必须幂等：point ID = `uuid5(NAMESPACE, f"{source}|{date}|{content_hash}")`，重复 sync 同一天不产生重复数据。
- **FR-015** 条目 payload 必须含：`text, date, type, tags, source, project, created_at, source_refs, distill_version, related`。
- **FR-016** 类型枚举必须由 `config/schema.json` 配置驱动（每个类型含 name/desc），蒸馏 prompt 运行时从配置拼装；新增类型零代码迁移。
- **FR-017** 入库时必须构建 related 关联边：对每条新条目做相似度搜索，取 top-5 中 score > 0.75 的建立双向边，单条条目 related 上限 5；双向回填时若对方条目 related 已达上限 5，则跳过该方向的回填（保持确定性与幂等）。

### 检索域

- **FR-018** `ask` 必须支持语义检索 + payload 过滤（`--type`、`--since`/日期区间、`--project`），top_k 可配。
- **FR-019** 回答必须带引用：每条结论可追溯到 `[date] type: 摘要` 及其 source。
- **FR-020** 关联边扩展必须可配置（`retrieval.expand.mode`：`off` / `all` / 数值阈值），默认 `all`；扩展邻居在 LLM 上下文中标记为"关联补充"（prompt 注明酌情使用）；CLI 提供 `--no-expand` 单次关闭。
- **FR-021** 检索配置（top_k、expand 各参数）统一放 `config/schema.json` 的 `retrieval` 节，CLI 与 API 共用。

### 运维域

- **FR-022** raw 快照保留策略必须可配置（`raw_retention_days`，默认 90，`null` = 永久），到期由 sync 顺手清理。
- **FR-023** 系统必须支持重蒸馏对照（US-5）：对指定日期重跑蒸馏，产出新旧条目集 diff，对齐规则：新旧条目同时满足（a）共享至少一个 source_refs 且（b）文本相似度 ≥0.85 则配对——配对且文本有差异记"改写"；仅出现在新集合记"新增"；仅出现在旧集合记"消失"。用户确认后整组替换该日条目或丢弃。
- **FR-024** FastAPI 薄壳必须提供：`GET /health`（Qdrant + 配置连通性）、`POST /log`、`POST /sync`（后台任务）+ `GET /sync/status`、`GET /ask`；路由只做参数校验与调用核心函数层，不含业务逻辑。
- **FR-025** sync 必须以纯 CLI 形式可运行（`make sync [D=日期]`），不依赖 API 进程存活。

---

## 5. 非功能需求（NFR）

- **NFR-001（隐私）**：密钥只从环境变量读取（`.env`，gitignore）；蒸馏前正则脱敏；采集器对源目录严格只读。
- **NFR-002（写入边界）**：所有运行时产物只允许写入 `./data/` 与 `./notes/` 两个根下；代码中不允许任何逃逸写入（临时文件用系统 tmp）。
- **NFR-003（幂等）**：见 FR-014/FR-017；任何重跑不产生重复数据或重复边。
- **NFR-004（故障隔离）**：单个插件解析失败只跳过该插件并记录日志，不中断其他来源的当日 sync；插件目标目录消失视为正常情况（静默 no-op）。
- **NFR-005（可扩展性）**：新增类型（改配置）、新增产品（加插件目录）均不改核心代码。
- **NFR-006（性能）**：典型日（raw 提取 100K–1M 字符）sync 全程 ≤ 5 分钟。
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
3. 源产品目录（`~/.claude`、`~/.codex`、`~/.kimi-code`、`~/.trae-cn` 等）只读；去重状态以 Qdrant 幂等 ID 为准，不向源目录写任何标记。

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

**待澄清项**：

- `[NEEDS CLARIFICATION]` 无——grilling 前沿已清空（2026-09-18）。若阶段四质询发现新问题，按 prd-workflow 回退规则处理。

---

## 10. 验收标准（AC）

- **AC-001** `make up` 后 Qdrant dashboard（:6333/dashboard）可访问；`make serve` 后 `curl :8300/health` 返回各组件连通状态。
- **AC-002** 用当日真实数据 `make sync`：`data/raw/<date>.json` 存在且含蒸馏元信息；Qdrant points > 0；payload 字段齐全（§FR-015 全部字段）。
- **AC-003** 同一日期重复 `make sync`：points 数不变、无重复边（幂等）。
- **AC-004** `make ask Q="..." --type error --since 7d`：仅返回 error 类型、日期过滤生效；回答每条结论带 `[date] type: 摘要` 引用。
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

- **用户手写**（AI 仅 review 与答疑，不代写）：核心逻辑层四模块——`ark_client`（嵌入调用）、相似度搜索与新颖度去重、幂等 ID 生成、蒸馏 prompt。
- **AI 生成**（用户抽查理解即可）：脚手架类——Dockerfile / docker-compose、Makefile、FastAPI 路由样板、`.gitignore`、配置文件骨架。

### 12.3 下游影响

- plan（阶段七）：任务拆分必须按 LG-002 纵切顺序组织。
- tasks（阶段九）：四个用户手写模块必须显式标注 user-handwritten 并关联本节；排期按用户手写速度估算，不按 AI 生成速度。
- 里程碑节点清单在 plan 阶段产出（LG-001）。

---

## 13. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v0.1 | 2026-09-18 | 初版：grilling 全部结论沉淀（Q1–Q7 + 部署与数据存放决策 + spec-kit 模板改造衔接） |
| v0.2 | 2026-09-18 | 新增需求"学习收益最大化"：新增 §12 学习目标与过程约束（LG-001~005、代码分工、下游影响）；无产品 FR 变更 |
| v0.3 | 2026-09-18 | 阶段四质询修复验收覆盖缺口：补 AC-012（脱敏）/ AC-013（未知类型）/ AC-014（条数熔断），兜底 FR-010/011/012；Q2-A 决议：FR-002 明确纵切滚动交付、四插件全过验收为 v0.1 完结 |
| v0.4 | 2026-09-18 | 阶段五 checklist 修订 4 项缺陷：FR-002 补 trae 类型映射规则（trae_type_map）；FR-023 补重蒸馏精确对齐规则（source_refs+相似度≥0.85 配对）；FR-017 补回填超上限处置（跳过）；§8 快记类型改为"轻处理不改类型"（消除与 FR-013 的矛盾） |
