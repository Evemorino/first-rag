# Implementation Plan: first-rag v0.1

**Branch**: N/A（单特性、未按特性建分支；项目已于 2026-09-20 `git init`，见 spec Assumptions） | **Date**: 2026-09-18 | **Spec**: [spec.md](spec.md)

**Input**: PRD.md **v0.7**（唯一需求事实来源；v0.1 范围已实现完毕，v0.7 扩采集面，见 PRD §13）；本文件所有需求引用均带 PRD 编号。

## Summary

个人学习记忆系统：插件化采集多个 AI 工具的当日会话（v0.1 为 4 个源，PRD v0.7 扩到 11 个，见"v0.7 采集面扩展"节）→ LLM 蒸馏为结构化条目 → Qdrant 幂等入库（含关联边）→ 过滤式语义检索 + 引用式回答；v0.8 再补一层**本机审阅页**（浏览 + 人工编辑 + 软删除），把"写错了只能手工连库改"这条堵上（PRD US-7，见"v0.8 审阅页与条目修正"节）。技术路线：纯函数核心层 + FastAPI 薄壳 + 本地 Docker Qdrant + 方舟 Ark API（OpenAI 兼容）。交付按纵切里程碑推进（PRD FR-002 Q2-A / LG-002）。

## Technical Context

- **Language/Version**: Python 3.13
- **Primary Dependencies**: qdrant-client、openai（SDK 指向 Ark base_url）、fastapi、uvicorn、python-dotenv、pytest
- **Storage**: Qdrant（本地 Docker，卷挂 `./data/qdrant/`）+ 文件（`data/raw/`、`notes/`）
- **Testing**: pytest（unit + integration，integration 需本地 Qdrant 运行）
- **Target Platform**: macOS 本机，单用户
- **Project Type**: CLI 工具 + 按需 API 服务（非守护进程，PRD ADR-4）
- **v0.8 新增**: 本机审阅页 —— 零构建静态单页，随 API 进程一起提供；**不引入新依赖、不新增常驻服务**（PRD NFR-008 / NFR-009）
- **v0.9 变更**: 上面那条**作废** —— 审阅页改由 `web/` 前端工程构建（Vite + React + TypeScript + Zustand + Tailwind + TanStack Query，见 ADR-21）；新增 Node 工具链（版本钉 `mise.toml`、lockfile 入库），**构建产物 `web/dist/` 不入库**，由接口进程读盘提供；**类型快照 `web/src/api/schema.d.ts` 相反 —— 入库**（契约，CI 重生成后要求零差异）；**仍不新增常驻服务**（NFR-008 成立），前端被门禁禁止直连向量库/读密钥（NFR-009 第 ④ 条 + NFR-012）
- **Performance Goals**: 当日 sync ≤5 分钟（NFR-006）。**计时按天**：2026-09-26 最忙日 09-25 全源端到端实测 **248.7s / 300s（83%）**，未突破（PRD v0.7.3 补测；4-源时代的 215.9s 口径已作废，见 PRD §9 风险表）；ask 单次 ≤10 秒（2026-09-25 实测 5.9s 达标；流式首字 0.7s）
- **Constraints**: 写入仅限 `data/`、`notes/`（宪法 V，NON-NEGOTIABLE）；密钥走 `.env`
- **Scale/Scope**: 单用户；日蒸馏输出 5–10K 字符；条目总量预期千级

## Constitution Check

*GATE: 逐条核对，全部通过方可进入实施。*

| 原则 | 本计划的符合方式 | 状态 |
|---|---|---|
| I 单一事实来源 | 全部需求引用 PRD 编号；无改写 | ✓ |
| II 纯核薄壳 | `src/` 核心层零 HTTP 依赖；`api/app.py` 仅参数校验 + 调用 | ✓ |
| III 配置驱动扩展 | 类型/rubric/检索/范围全在 `config/`；采集源全在 `src/plugins/` | ✓ |
| IV 幂等与确定性 | uuid5 条目 ID；关联边确定性构建；重跑无副作用 | ✓ |
| V 写入边界（NON-NEGOTIABLE） | 代码写入仅 `data/`、`notes/`；测试 fixture 全部用系统 tmp；源目录只读 | ✓ |
| VI 技术克制与可逆 | 单包 `src/` 平铺，无 workspace；常驻服务仅 Qdrant；依赖全在 `.venv` | ✓ |
| VII 学习优先 | 里程碑纵切；四个手写模块在结构图中显式标注 | ✓ |

**Complexity Tracking**：无违宪项，无需豁免记录。

## Project Structure

### Documentation (this feature)

```text
specs/001-learning-memory-rag/
├── plan.md              # 本文件
├── research.md          # 数据源探测结论（M0 前已完成的调研）
├── data-model.md        # Qdrant payload / raw 快照 / config schema
├── quickstart.md         # 安装与首次运行步骤
├── contracts/           # 插件契约与核心模块接口
│   └── plugin-contract.md
└── tasks.md             # 阶段九产出（$speckit-tasks）
```

### Source Code (repository root)

```text
first-rag/
├─ PRD.md
├─ docker-compose.yml        # 仅 qdrant 服务
├─ .env.example             # ARK_API_KEY / ARK_BASE_URL / EMBED_MODEL / CHAT_MODEL
├─ .gitignore               # 初版必含 data/ .env notes/（PRD §6 规则 2）
├─ Makefile                 # up / serve / sync / ask / log / embed-test / test / scope
├─ pyproject.toml           # 项目内 .venv 依赖
├─ config/
│  ├─ schema.json           # types / distill / retrieval / trae_type_map / raw_retention_days
│  ├─ scope.json            # make scope 的写入目标（工具×项目勾选）
│  └─ repos.txt             # git 采集仓库列表
├─ notes/inbox.md           # 手动快记
├─ data/                    # qdrant/ 与 raw/（gitignore，不进 git）
├─ src/
│  ├─ config.py             # 环境变量加载、路径、常量
│  ├─ secret_patterns.py    # 凭据/密钥形态表（脱敏与令牌扫描共用）
│  ├─ sanitize.py           # 脱敏：写库前抹掉密钥
│  ├─ log.py                # make log 入口：快记 → notes/inbox.md
│  ├─ scope.py              # make scope 交互：工具×项目勾选 → config/scope.json
│  ├─ ark_client.py         # ★：AI 实现已获用户 2026-09-20 明确授权；embed(texts) / chat(messages)
│  ├─ ids.py                # ★：AI 实现已获用户 2026-09-20 明确授权；uuid5 条目 ID（source|date|content_hash）
│  ├─ similarity.py         # ★手写：T017 新颖度去重已获 AI 授权；T027/T037 仍手写
│  ├─ distill_prompt.py     # ★：AI 实现已获用户 2026-09-20 明确授权；蒸馏 prompt 运行时拼装（rubric → prompt）
│  ├─ distill_candidates.py # 候选条目提取（LLM 输出 → 结构化候选）
│  ├─ distill_messages.py   # 蒸馏消息组装（素材 → messages）
│  ├─ distill_batches.py    # 分批并行（batch_max_chars × parallel_workers，NFR-006）
│  ├─ collect.py            # 汇聚各插件 + git + 快记 → DayRaw
│  ├─ distill.py            # 蒸馏编排（脱敏 → LLM → 类型校验 → 熔断）
│  ├─ ingest.py             # 嵌入 → upsert → 关联边（调 similarity）
│  ├─ ask.py                # 检索 + 过滤 + 引用式回答（含流式）
│  ├─ ask_expand.py         # 引用构建 + 一跳关联扩展（2026-09-25 size 门禁拆分）
│  ├─ redistill.py          # 重蒸馏对照 diff / --apply 整组替换（US-5）
│  ├─ sync.py               # 串联 collect→distill→ingest + retention 清理
│  ├─ plugins/
│  │  ├─ __init__.py        # registry：扫描注册，插件缺失静默跳过
│  │  ├─ _template/         # 新插件模板（discover + parse）
│  │  ├─ claude_code/       # raw 路径
│  │  ├─ codex/             # raw 路径
│  │  ├─ kimi_code/         # raw 路径
│  │  ├─ trae_work_cn/      # pre-summarized 路径 + trae_type_map（v0.7 由 trae/ 改名）
│  │  ├─ trae/              # v0.7 新增：~/.trae，与 trae_work_cn 同格式
│  │  ├─ qoder/             # v0.7 新增：Claude/JSONL 系
│  │  ├─ qoder_cn/          # v0.7 新增：独立解析器 + 与 qoder 的等价性测试（见 :144）
│  │  ├─ workbuddy_ai/      # v0.7 新增：CodeBuddy 系，独立解析器
│  │  ├─ opencode/          # v0.7 新增：SQLite（mode=ro）
│  │  ├─ zcode/             # v0.7 新增：SQLite（mode=ro）
│  │  └─ hermes/            # v0.7 新增：SQLite（mode=ro，零真实数据）
│  └─ api/app.py            # FastAPI 薄壳（health / log / sync+status / ask）
└─ tests/
   ├─ unit/                # ids / 脱敏 / trae_type_map / 对齐规则 / 插件解析（fixture）
   └─ integration/          # Qdrant 幂等 / 过滤检索 / 扩展 / 端到端
```

（★ = 默认用户手写模块，宪法 VII v2.0.0；T004/T012/T015/T017 已获用户 2026-09-20 逐任务明确授权由 AI 实现，其余可 AI 生成后由用户抽查）

**Structure Decision**: 单包平铺（宪法 VI）。插件为目录约定而非独立包；不引入 workspace，判据见宪法 Architecture 节。

## Milestones（纵切交付，LG-002；每片端到端可运行）

| # | 里程碑 | 内容 | 验收（PRD AC） |
|---|---|---|---|
| M0 | 脚手架 | compose/.env.example/.gitignore/Makefile/pyproject + .venv + **Ark 嵌入真调验证**（确认模型 ID 与维度）+ 建集合脚本（动态维度） | AC-001 |
| M1 | 采集直插 | ★ids + claude_code 插件 + collect + ingest 直插（无蒸馏）| AC-003（幂等先行验证）|
| M2 | 蒸馏上线 | ★distill_prompt + ★similarity（新颖度去重）+ distill 编排（脱敏/未知类型/熔断）| AC-002、AC-012/013/014 |
| M3 | 检索问答 | ask（过滤 + 引用）+ 扩展标记 | AC-004、AC-005 |
| M4 | 关联边 | ingest 内关联构建（含回填上限规则）+ 扩展消费 | AC-005 完整 |
| M5 | **Dogfood ①**（LG-001） | 用本项目自身开发会话跑 sync | — |
| M6 | 插件扩展 | codex → kimi_code → trae（该插件 v0.7 起名为 trae_work_cn）依次补齐 | AC-002（多源）|
| M7 | 运维能力 | retention 清理 / 重蒸馏对照 / 历史补跑 | AC-008/009/010 |
| M8 | 入口完备 | make scope + FastAPI 四端点 | AC-007、AC-001 完整 |
| M9 | 收尾 | AGENTS.md / README / 全 AC 回归 + **Dogfood ②** | AC-011 及全量 |
| M10 | **v0.7 采集面扩展** | 四步，顺序不可换（详见下节）：① `trae`→`trae_work_cn` 迁移 ② SQLite 只读机械门禁 ③ A 族 4 插件 ④ B 族 3 插件 + 逐源计时 | AC-015、AC-016、AC-017 |
| M11 | **v0.8 审阅页与条目修正** | 三片纵切（LG-002）：① 只读审阅页（列表 + 详情，纯读）② 条目编辑（覆写层）+ 软删除与恢复 + 留痕 ③ 页面打磨与人工冒烟 | AC-018~AC-021 |
| M12 | **v0.9 审阅页工程化** | 三片纵切：① 前端工程脚手架 + 只读列表/详情（Vite+React+TS+Tailwind+TanStack Query）② 写能力（编辑/软删/恢复 + 409 冲突 + 乐观更新）+ Playwright 覆盖 8 步验收 ③ 设计打磨与 a11y，删掉旧单文件并回灌文档 | AC-018~AC-021（行为不变，换实现） |

## v0.7 采集面扩展（M10）

PRD v0.7 把采集面从 4 个插件扩到 11 个。本节的四步有**严格顺序**，理由随附。

### ① 改名迁移（先行）

`trae`→`trae_work_cn` 改的是插件名，动的却是**条目 ID**：`point_id = uuid5(NS_URL, f"{source}|{date}|{content_hash}")`，source 一改，同样一条素材就换了 ID。存量已入库的 9 条会变成孤儿（挂在旧 source 下），而新插件再采一遍会按新 ID 重新蒸馏入库——同一素材两份。

所以顺序是：**先迁移脚本（旧 source → 新 source 重算 ID → upsert → 删旧），再启用新插件**。脚本要幂等、可 dry-run，并且要有**独立校验**：迁移前后条目总数不变、正文哈希集合逐一对应。校验不能用脚本自己的中间结果，要直接查 Qdrant 现况——自己验自己等于没验。

改名还牵动 `config/schema.json` 的 `trae_type_map`（键名不变，读取走 `config.load_schema().get("trae_type_map", {})`，见 `src/plugins/trae/__init__.py:140`——**不是硬编码字面量**，坏配置会落默认空表）与 `MEMORY_DIR`。新 `trae` 插件与 `trae_work_cn` 同格式，只差数据根。

### ② SQLite 只读机械门禁

B 族三个插件都要读 SQLite，而普通 `sqlite3.connect()` 会在源目录落 `-wal`/`-shm`/`-journal`——那是写入，违反宪法 V，且不可恢复。规矩写在 NFR-001 里不够，要有门禁：`scripts/write_boundary_check.py` 增加一条规则，对 `src/` 里任何非 `file:<path>?mode=ro` 的 `sqlite3.connect()` 判红。

门禁本身也要被门禁：`scripts/gate_selftest.py` 里加一个植入违规的用例，确认它真的会红——一个不会红的门禁比没有门禁更危险，因为它提供虚假的安全感。

**不放共享的 `src/plugins/_sqlite.py` helper**：`scripts/lint_layers.py:220` 禁止插件间互相 import，会把它判红，而 linter 自己的提示是把公共件放进 `src/plugins/__init__.py`——那等于让 registry 长出数据库职责，与"纯核薄壳"相冲。三个插件各写各的连接（都是三行），换来的是一条能被机械检查的边界。

### ③ A 族 · Claude/JSONL 4 插件

`qoder` / `qoder_cn` / `workbuddy_ai` / `trae`。**不要因为长得像就共用解析器**——三点实测反例：

- `qoder` 与 `qoder_cn` 逐字段同 schema，**语义同源**；但**不共用一个模块**——`lint_layers` 禁止插件互相 import，共享的代价是给那道门禁开口子，比重复更贵。改为各写各的，由 T053a 的跨插件等价性测试钉住。它们的时间戳**双编码混排**（消息记录 ISO 8601 UTC 字符串、bookkeeping 记录 epoch 毫秒），解析必须先判类型，不能假定单一编码。
- `workbuddy_ai` 是 CodeBuddy 系 schema，与上面两者都不同，**必须独立解析器**。它 89 条 user 记录里只有 32 条是真实用户输入，其余是对话摘要、任务通知、"Please continue with…"——正文只取 `<user_query>…</user_query>` 包裹的内容，取不到就不产素材。
- `trae` 与 `trae_work_cn` 同格式，直接复用。

每个源 M10 完成时带一条"注入物不得产生素材"的单测，断言用真实文件里数出来的计数（如 zcode 的 343/115/228），不是编的。

### ④ B 族 · SQLite 3 插件

`opencode` / `zcode` / `hermes`，一律 `mode=ro` 打开。逐源要点：

- `opencode`：`~/.local/share/opencode/opencode.db` 的 `session_message`（`data` 列是 JSON），epoch 毫秒。**表允许列表必须排除 `credential`/`account`/`control_account`**。
- `zcode`：`~/.zcode/cli/db/db.sqlite` 的 `message` + `part`，epoch 毫秒。必须排除 `message.data.synthetic` 为真的记录（343 条 user 消息里 115 条是 fork 提示与工具结果回填）。
- `hermes`：`~/.hermes/state.db`，epoch **秒**（不是毫秒——按毫秒解会得到 year 58684）。**该库零真实数据**：按 schema 实现，验收依据只有构造的 fixture，测试里必须显式标注"未经真实数据验证"。这不是走过场，是留一个诚实的缺口。

### 附带义务

- **按天计时**：真实 sync 把当天全源素材合并后统一分批，成本不按源可加——**逐源计时从原理上测不出总预算**（PRD v0.7.3 订正）。已实测 **248.7s / 300s**（2026-09-26，最忙日 09-25，1,928,545 字符 / 18 批 / 3 轮）；单源耗时只作诊断记录，不作验收尺子。当日计时逼近 300s 时按 PRD §9 的顺序处置（源级 scope → 分片/水位 → 重议预算），**不得无实测就放宽预算**。
- **不动历史工件**：`analysis.md`、`checklists/`、已发布的变更记录行记录的是当时的事实，不追改。

## v0.8 审阅页与条目修正（M11）

> **v0.9 起本节的"零构建单页"设计被 [ADR-21](../../docs/adr/0021-frontend-toolchain.md) 取代**：页面改由 `web/` 前端工程构建（产物不入库），接口契约与 AC-018~021 不变。下方内容保留为 v0.8 当期记录；v0.9 的工程结构、门禁清单与 CI 变更另起一节（本节末尾之后）。

**目标**：堵掉"库里写错了只能手工连 Qdrant"。本机审阅页浏览 + 条目人工编辑 + 软删除与恢复 + 留痕（PRD US-7 / FR-026~FR-030 / NFR-009~011 / AC-018~021）。逐题决策与理由见 `specs/001-learning-memory-rag/v08-decisions.md`。

### 设计要点

1. **人工编辑走覆写层，不动 ID**。条目标识仍是 `uuid5(source|date|content_hash)`；人工修改另存：`override`（正文/类型/标签/项目）、`edited_at`、`edited_prev_text`（最近一次变更前正文）、`original_text`（原始蒸馏正文，永久保留）。
   - 所有**面向人的读取路径**（列表、详情、检索上下文）取覆写值，缺省回落到原始字段；**ID / `related` / `source_refs` / `date` / `source` / `distill_version` 一律不变**。
   - 保存时**用覆写正文重算该条向量**——否则会出现"显示新文字、命中靠旧语义"。Ark 不可用则拒绝保存（不做"先存文本、后补向量"）。
2. **软删除靠 payload 过滤**：写 `deleted_at`（+ 可选 `deleted_reason`）。面向人的路径（列表、`ask`）加"不存在 `deleted_at`"条件；只读诊断（`rerun-overlap` / `batch-edges` / 重蒸馏 diff）**不过滤**并标注"已删"。
   - `related` 指向已软删条目时，检索期忽略该边；**不做级联删边**——那会把可恢复性变成不可恢复。
3. **同日重放不得冲掉人工值**（本版最容易踩的坑：原始快照还在，重放必然再蒸出同一条素材）：`ingest.upsert` 合并 payload 时 MUST 保留 `override`/`deleted_at`/`edited_at` 等人工字段；重放产生的新条目若与已软删条目**同 ID**，保持删除状态。
4. **页面零构建、只绑本机**：`GET /` 返回随仓库走的静态单页（HTML + CSS + 原生 JS，无 CDN、无 npm）；详情用同页右侧面板 + `#/entry/<id>` 锚点。不新增常驻服务，NFR-008 仍成立。
   - 写接口校验 `Origin`/`Host` 为本机并要求自定义头（`X-Requested-With`）——本机无鉴权服务唯一的实际风险面就是"浏览器里别的页面替你发写请求"。

### 界面与接口契约（新增）

| 入口 | 方法 | 作用 |
|---|---|---|
| `/` | GET | 审阅页（静态单页，浏览器打开即用） |
| `/types` | GET | 配置里的类型枚举（供筛选与编辑下拉，页面不硬编码类型表） |
| `/entries` | GET | 列表：`date_from` / `date_to` / `type` / `project` / `source` / `include_deleted` / `page` |
| `/entries/{id}` | GET | 详情：当前值 + 原始正文 + 留痕 + 关联边（标注"基于原始蒸馏正文"） |
| `/entries/{id}` | PATCH | 人工编辑（正文/类型/标签/项目 + `rev` 乐观并发） |
| `/entries/{id}/delete` | POST | 软删除（原因可选） |
| `/entries/{id}/restore` | POST | 恢复 |

- 路由层只做参数校验与转发（宪法 II）；核心逻辑放 `src/entries.py`，API 不写业务规则。
- 编辑/删除/恢复返回新 `rev`；`rev` 不符返回 409，提示刷新（两个标签页同时改同一条时不静默丢改动）。

### 目录与门禁影响

- 新增核心模块 `src/entries.py`；页面静态资源放 `src/api/ui/`。
- **新增 `src/` 目录必须登记进 `scripts/lint_layers.py` 的 `DIRECTORIES`**（宪法硬约束，兜底是拒绝）；静态资源为只读，预计不新增 `WRITE_SITES` 登记项，若新增则同步登记。
- 变异范围：`src/entries.py` 在 `src/` 下，进 mutmut 覆盖；HTML/CSS/JS 不在变异范围，由契约测试守。

### 测试策略（v0.8）

- **必须自动化**（真插库、真重放，内嵌 Qdrant fixture）：AC-019（编辑不换 ID + 重放不覆盖人工值）、AC-020（软删后重放不复活 + 恢复可用）、AC-021（留痕不被重放冲掉）。
- **契约自动化**：AC-018 的两条机械面 —— `GET /` 返回 200 且页面内**不含任何外部 URL**；服务监听地址为 `127.0.0.1`（读 app 配置断言，不起真进程）。
- **人工冒烟一次**：浏览器打开页面 → 点开条目 → 改一条 → 删一条 → 恢复一条（结果记进 quickstart 的验收段）。

### Constitution Check（v0.8 复核）

| 原则 | v0.8 的符合方式 | 状态 |
|---|---|---|
| I 单一事实来源 | 逐条引用 PRD FR-026~FR-030 / NFR-009~011 / AC-018~021 | ✓ |
| II 纯核薄壳 | 核心逻辑 `src/entries.py`；`api/app.py` 只校验与转发 | ✓ |
| III 配置驱动扩展 | 类型下拉读 `config/schema.json`，不硬编码类型表 | ✓ |
| IV 幂等与确定性 | 覆写不动 ID；重放不复活软删、不覆盖人工值（NFR-011） | ✓ |
| V 写入边界（NON-NEGOTIABLE） | 新字段只进 Qdrant payload，不新增写入根；新 `src/` 目录登记进 `DIRECTORIES` | ✓ |
| VI 技术克制与可逆 | 零构建单页、无新依赖、无新常驻服务 | ✓ |
| VII 学习优先 | 纵切三片（只读 → 编辑/软删 → 打磨）；LG-005 的决策经用户 2026-09-28 授权采用推荐值 | ✓ |

**Complexity Tracking（v0.8）**：无违宪项。唯一需要记录的取舍是"覆写层"引入的字段重叠（当前值 vs 原始值）——用 `original_text` 永久保留 + 详情页对照展示化解，不用新表。

## v0.9 审阅页工程化（M12）

**目标**：把审阅页从 v0.8 的"零构建单文件"换成**受限引入的前端工程**（ADR-21）。**接口契约、FR-026~FR-030、AC-018~AC-021 一行不改** —— 换的是"这层薄壳怎么造"。

### 工程结构与状态分工

```
web/
├── index.html            vite.config.ts        tailwind.config.ts
├── package.json          pnpm-lock.yaml        tsconfig.json     eslint.config.js
├── src/
│   ├── api/              # 走同一份 fetch 封装；schema.d.ts 由 openapi.json 生成（**入库**）
│   ├── queries/          # TanStack Query：列表/详情/编辑/软删/恢复
│   ├── store/            # Zustand：只放 UI 状态（选中项、筛选条件、主题）
│   ├── components/       # Table / DetailPanel / EditForm / Toast / ThemeToggle
│   └── main.tsx
├── tests/                # Vitest（组件与纯函数）
└── e2e/                  # Playwright（AC-018~021 的 8 步）
```

- **状态分工写死**：服务端状态归 **TanStack Query**（缓存、失效、乐观更新、409 冲突），**Zustand 只放 UI 状态** —— 两套不许争同一份数据（ADR-21 决策 1）。
- **产物 vs 类型，两条相反的规矩**：`web/dist/`（构建产物）**不入库**；`web/src/api/schema.d.ts`（由 `openapi.json` 生成的类型快照）**入库**，CI 重新生成并断言无 diff —— 前者是生成物、后者是契约快照，混为一谈就会出现"改字段忘一侧"的静默漂移。

### 入口与产物落点

| 入口 | 做什么 |
|---|---|
| `make ui` | `pnpm --dir web build` → `web/dist/`；**不自动跑在 serve 里**（保持 serve 快、职责单一） |
| `make ui-dev` | `pnpm --dir web dev`（Vite dev server，`/types`、`/entries*`、`/ask*` 代理到 `127.0.0.1:8300`） |
| `make serve` | 仍是唯一后端；`GET /` 返回 `web/dist/index.html`，`app.mount("/assets", StaticFiles(...))` 提供带 hash 的 JS/CSS；**不需要 SPA 兜底路由**（详情是 `#/entry/<id>` hash 路由） |
| `dist` 缺失时 | `GET /` 明确返回 **503 + "先跑 `make ui`"**，绝不返回空白页 —— 静默的白屏是最难查的一类失败 |

### 门禁清单（NFR-012 的落地）

**进提交钩子**（`.pre-commit-config.yaml`，全部 `files: ^web/`、`language: system`，只在 `web/` 有改动时触发，Python-only 提交不受影响）：

1. `eslint`（含**三条禁止规则**：禁 `qdrant-client` 一类客户端 import、禁读 `ARK_*`、禁 `node:fs` 写文件 —— 把"前端不是第二个后端"做成机械判据）
2. `tsc --noEmit`
3. `vitest run`

**只进 CI**：`playwright test`（覆盖 AC-018~021 的 8 步；需 `playwright install --with-deps chromium`）。

**`gate-selftest` 新增 4 类用例**（植入违规必须变红）：eslint（违规 import）、tsc（类型错误）、vitest（失败断言）、playwright（页面断言失败）。**实现约束**：临时仓库里要能**离线**装前端依赖（复用 pnpm 全局 store，同 Python 侧复用 uv 缓存的做法），否则"证明钩子会红"会变成每次联网；每个 JS 用例预计 +1~2 秒。

**配套两条**：`size_guard` 扩到 `web/src/**/*.{ts,tsx}`（沿用同一套阈值：文件 ≤300 行、函数 ≤80 行）；README 的用例数**分列 Python / 前端**。

**自动覆盖、无需改动的**：`secret_scan`（扫入库文件，天然覆盖 `web/`）。**前端没有变异测试**（Stryker 不列入本版）—— NFR-012 与 README 两处都要写明。

### CI 变更（`.github/workflows/ci.yml`）

现有 job 是"Python + Qdrant + 15 钩子 + gate-selftest"，v0.9 追加：
1. `setup-node`（版本读 `mise.toml`，与 uv 同样的单一事实来源）；
2. `pnpm install --frozen-lockfile`；
3. `pnpm --dir web build`（顺带证明产物可构建）；
4. `pnpm --dir web lint && tsc --noEmit && vitest run`；
5. `playwright install --with-deps chromium` + `playwright test`；
6. **类型快照断言**：重新生成 `schema.d.ts` 后 `git diff --exit-code`。

### 设计要点（片 3 的内容，不是"换栈自动获得"）

排版梯级与限宽、列表改真表格（粘性表头 + `tabular-nums`）、加载骨架/空态/错误态、Toast 取代 `prompt()`、`focus-visible` 焦点环、`aria-live`、`prefers-reduced-motion`、`prefers-color-scheme` 暗色 + 主题开关（**需要 Zustand 放主题状态**）、快捷键（j/k 上下、e 编辑、Esc 取消）。

### 测试策略（v0.9）

- **Vitest**：`effective` 视图映射、筛选参数拼装、409 冲突分支、乐观更新的回滚。
- **Playwright**：开页面 → 筛选 → 详情 → 编辑保存 → 删除 → 勾"显示已删除" → 恢复 → `make sync` 重放后人工值仍在（就是 T105 那张表的自动化版，跑真后端 + 内嵌/本地 Qdrant）。
- **后端侧不动**：`tests/unit/test_entries*.py`、`test_api_ui.py`、`test_api_writes.py`、`tests/integration/test_entries_replay.py` 原样保留 —— 它们是接口契约的守门人，前端换实现不该让它们变红。
- **"断网可用"要扫**产物**，不只扫入口**：`GET /` 返回的那段 HTML 只是入口，Vite 产物还有 `assets/*.js|.css` —— 外部 CDN 引用最可能藏在 bundle 里。所以断言 MUST 扫**整个 `web/dist/**`**（禁 `http(s)://` 外部地址、协议相对地址、CDN 域名），并配一条 ESLint 规则禁"导入远程 URL"；CI 与契约测试两处都要跑（NFR-009 ③）。（来源：v0.9 analyze 的 HIGH finding —— 原来的断言只覆盖入口，等于把"断网可用"押在一句愿望上。）

### Constitution Check（v0.9 复核）

| 原则 | v0.9 的符合方式 | 状态 |
|---|---|---|
| I 单一事实来源 | NFR-009 改写 + NFR-012 新增都在 PRD；plan/tasks 逐条引用 | ✓ |
| II 纯核薄壳 | 前端**不碰核心层**：只调 `src/api/app.py` 已登记端点；路由仍只做校验与转发 | ✓ |
| III 配置驱动扩展 | 类型下拉仍来自 `GET /types`（配置驱动），前端不硬编码类型表 | ✓ |
| IV 幂等与确定性 | 前端不参与 ID 生成与去重；人工覆写规则（NFR-011）不变 | ✓ |
| V 写入边界（NON-NEGOTIABLE） | 前端**不写盘、不写库**（ESLint 三条禁止规则机械守）；`web/` 在 `src/` 之外，不受 `WRITE_SITES`/`DIRECTORIES` 管辖 —— 但因此**多了两条门禁**来补这块空白 | ✓ |
| VI 技术克制与可逆 | 新增的唯一依赖是 Node 工具链（如实记代价）；仍**只有 Qdrant 一个常驻服务**；产物不入库、换框架不动后端 | ✓ |
| VII 学习优先 | 三片纵切，每片可运行；ADR-21 记录了栈选择与三条被否备选 | ✓ |

**Complexity Tracking（v0.9）**：新增一条"工具链复杂度"，如实登记为**已接受的代价**（门槛从 `.venv` 变成 `.venv + Node`、CI 变慢、前端无变异测试），并用"产物不入库 + 类型快照入库 + 两条新门禁"把它的风险面收窄。

## Module Contracts（摘要，全文见 contracts/plugin-contract.md）

- `ark_client.embed(texts: list[str]) -> list[list[float]]`；`chat(messages, json_mode=False, max_tokens=None, thinking=True) -> str`
- 插件：`discover(date) -> list[SourceRef]`；`parse(ref) -> RawMaterial`（统一中间格式，见 data-model.md）
  ——`SourceRef` / `RawMaterial` 类型定义归属 `src/plugins/__init__.py`（registry 模块），插件只从这里 import（F3 修复）
- `collect.gather(date, scope) -> DayRaw`；`distill.distill(day_raw) -> list[Entry]`
- `ingest.upsert(entries) -> Report`；`similarity.search(vec, k, filters) -> list[Hit]`
- `ask.query(question, *, type=None, project=None, since=None, until=None, expand=None) -> Answer`（含 citations）
- `sync.run(date)`：串联 + 到期 raw 清理；**入口处取 `data/.sync.lock` 文件锁，已有 sync 运行则立即报错退出**（防 cron 与 API 并发，F2 修复）

## Testing Strategy

- **unit**：uuid5 稳定性、脱敏正则（伪造密钥 fixture）、trae_type_map、重蒸馏对齐规则、各插件对 fixture JSONL 的解析
- **integration**（需 `make up`）：幂等重跑（AC-003）、过滤检索（AC-004）、扩展开关（AC-005）、熔断（AC-014）、插件目录消失（AC-006）
- **AC 追溯**：tasks.md 中每个任务标注实现的 AC 编号

## Risks

| 风险 | 缓解（均已在 PRD §9 备案） |
|---|---|
| Ark 嵌入模型 ID 未验证 | M0 一次性真调，维度动态读取建集合 |
| 产品会话格式漂移 | 解析隔离在插件层；各插件的 fixture 测试防回归；接入前用 `scripts/schema_check.py` 照真实文件确认格式 |
| LLM 输出不稳定 | 重试 + 丢弃策略已定义（FR-012 / §8） |
| 核心模块默认用户手写，进度不可控（宪法 VII v2.0.0） | 里程碑独立可运行，每片完成即有增量价值；仅在用户逐任务明确授权时允许 AI 代写 |
| **更换嵌入模型**（F1） | collection 维度绑定模型：新建 collection → 对存量条目全量重嵌入（raw 保留期内的直接重嵌入；超期的回产品源目录重采集，插件只读保证可重放）→ 验证后切换。蒸馏条目不重生成，仅向量重算 |
| **v0.7**：改名迁移写坏了存量条目（ID 不可逆） | 迁移脚本幂等 + dry-run；迁移前备份 `data/qdrant/`；校验直接查 Qdrant 现况（条目总数不变、正文哈希集合逐一对应），不用脚本自己的中间结果自证 |
| **v0.7**：SQLite 插件把 `-wal` 写进源目录 | 机械门禁（`write_boundary_check` 判非 `mode=ro` 的 `connect()`）+ `gate_selftest` 植入违规用例；另有一条"跑完源目录文件列表不变"的断言 |
| **v0.7**：11 源撑破 NFR-006 的 300s | **按天计时**（逐源计时测不出总预算，见 PRD v0.7.3）；逼近即按 PRD §9 处置（源级 scope → 分片/水位 → 重议预算），不放宽无实测的预算 |
| **v0.7**：hermes 插件"通过"实际是空转 | 测试显式标注"未经真实数据验证"；补验前不得以其声称任何 AC 通过 |
| **v0.8**：人工编辑被同日重放冲掉 / 软删条目复活 | 覆写层与自动字段分离 + 检索期过滤，由 AC-019/AC-020 的自动化用例（真跑重放）钉住；这是本版最可能踩的坑 |
| **v0.8**：无鉴权页面被浏览器里的其他页面驱动（跨站写请求） | 写接口校验 `Origin`/`Host` + 自定义头；页面只绑 `127.0.0.1`（NFR-010） |
| **v0.8**：覆写让"库里显示的"不再等于"蒸馏产出的"，将来追溯对不上 | `original_text` 永久保留、详情页对照展示；检索上下文用当前值并带"已编辑"标记 |
| **v0.9**：前端工具链抬高"跑起来"的门槛、拖慢 CI | NFR-009 已把"门槛从 `.venv` 变成 `.venv + Node`"登记为**已接受代价**；`make ui`（构建）与 `make ui-dev`（开发）分离，`make serve` **不自动 build**；`dist` 缺失时 `GET /` 返回 **503 + "先跑 `make ui`"**，不返回空白页 |
| **v0.9**：改了后端字段忘改前端（或反之），且没有任何机械判据 | 类型由 `openapi.json` 生成、**快照入库**，CI 重新生成后 `git diff --exit-code`；前端类型错误由 `tsc --noEmit` 在提交钩子挡住 |
| **v0.9**：`gate-selftest` 的前端用例因装不上依赖而变慢或**变瞎**（skip 掉） | 复用 pnpm 全局 store 离线安装（同 Python 侧复用 uv 缓存）；用例失败一律报红，**不允许 skip** —— "门禁自己瞎掉还全绿"是本仓库最怕的死法（gate-selftest 存在的理由） |
