# Implementation Plan: first-rag v0.1

**Branch**: N/A（项目未 git init，见 spec Assumptions） | **Date**: 2026-09-18 | **Spec**: [spec.md](spec.md)

**Input**: PRD.md v0.4（唯一需求事实来源）；本文件所有需求引用均带 PRD 编号。

## Summary

个人学习记忆系统：插件化采集四个 AI 工具的当日会话 → LLM 蒸馏为结构化条目 → Qdrant 幂等入库（含关联边）→ 过滤式语义检索 + 引用式回答。技术路线：纯函数核心层 + FastAPI 薄壳 + 本地 Docker Qdrant + 方舟 Ark API（OpenAI 兼容）。交付按纵切里程碑推进（PRD FR-002 Q2-A / LG-002）。

## Technical Context

- **Language/Version**: Python 3.13
- **Primary Dependencies**: qdrant-client、openai（SDK 指向 Ark base_url）、fastapi、uvicorn、python-dotenv、pytest
- **Storage**: Qdrant（本地 Docker，卷挂 `./data/qdrant/`）+ 文件（`data/raw/`、`notes/`）
- **Testing**: pytest（unit + integration，integration 需本地 Qdrant 运行）
- **Target Platform**: macOS 本机，单用户
- **Project Type**: CLI 工具 + 按需 API 服务（非守护进程，PRD ADR-4）
- **Performance Goals**: 当日 sync ≤5 分钟（NFR-006）；ask 单次 ≤10 秒
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
│  ├─ ark_client.py         # ★手写：embed(texts) / chat(messages)
│  ├─ ids.py                # ★手写：uuid5 条目 ID（source|date|content_hash）
│  ├─ similarity.py         # ★手写：相似度搜索 / 新颖度去重 / 重蒸馏对齐
│  ├─ distill_prompt.py     # ★手写：蒸馏 prompt 运行时拼装（rubric → prompt）
│  ├─ collect.py            # 汇聚各插件 + git + 快记 → DayRaw
│  ├─ distill.py            # 蒸馏编排（脱敏 → LLM → 类型校验 → 熔断）
│  ├─ ingest.py             # 嵌入 → upsert → 关联边（调 similarity）
│  ├─ ask.py                # 检索 + 过滤 + 扩展 + 引用式回答
│  ├─ sync.py               # 串联 collect→distill→ingest + retention 清理
│  ├─ plugins/
│  │  ├─ __init__.py        # registry：扫描注册，插件缺失静默跳过
│  │  ├─ _template/         # 新插件模板（discover + parse）
│  │  ├─ claude_code/       # raw 路径
│  │  ├─ codex/             # raw 路径
│  │  ├─ kimi_code/         # raw 路径
│  │  └─ trae/              # pre-summarized 路径 + trae_type_map
│  └─ api/app.py            # FastAPI 薄壳（health / log / sync+status / ask）
└─ tests/
   ├─ unit/                # ids / 脱敏 / trae_type_map / 对齐规则 / 插件解析（fixture）
   └─ integration/          # Qdrant 幂等 / 过滤检索 / 扩展 / 端到端
```

（★ = 用户手写模块，宪法 VII；其余可 AI 生成后由用户抽查）

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
| M6 | 插件扩展 | codex → kimi_code → trae 依次补齐 | AC-002（多源）|
| M7 | 运维能力 | retention 清理 / 重蒸馏对照 / 历史补跑 | AC-008/009/010 |
| M8 | 入口完备 | make scope + FastAPI 四端点 | AC-007、AC-001 完整 |
| M9 | 收尾 | AGENTS.md / README / 全 AC 回归 + **Dogfood ②** | AC-011 及全量 |

## Module Contracts（摘要，全文见 contracts/plugin-contract.md）

- `ark_client.embed(texts: list[str]) -> list[list[float]]`；`chat(messages, json_mode=True) -> str`
- 插件：`discover(date) -> list[SourceRef]`；`parse(ref) -> RawMaterial`（统一中间格式，见 data-model.md）
  ——`SourceRef` / `RawMaterial` 类型定义归属 `src/plugins/__init__.py`（registry 模块），插件只从这里 import（F3 修复）
- `collect.gather(date, scope) -> DayRaw`；`distill.distill(day_raw) -> list[Entry]`
- `ingest.upsert(entries) -> Report`；`similarity.search(vec, k, filters) -> list[Hit]`
- `ask.query(q, filters, expand_mode) -> Answer`（含 citations）
- `sync.run(date)`：串联 + 到期 raw 清理；**入口处取 `data/.sync.lock` 文件锁，已有 sync 运行则立即报错退出**（防 cron 与 API 并发，F2 修复）

## Testing Strategy

- **unit**：uuid5 稳定性、脱敏正则（伪造密钥 fixture）、trae_type_map、重蒸馏对齐规则、各插件对 fixture JSONL 的解析
- **integration**（需 `make up`）：幂等重跑（AC-003）、过滤检索（AC-004）、扩展开关（AC-005）、熔断（AC-014）、插件目录消失（AC-006）
- **AC 追溯**：tasks.md 中每个任务标注实现的 AC 编号

## Risks

| 风险 | 缓解（均已在 PRD §9 备案） |
|---|---|
| Ark 嵌入模型 ID 未验证 | M0 一次性真调，维度动态读取建集合 |
| 产品会话格式漂移 | 解析隔离在插件层；schema_check fixture 防回归 |
| LLM 输出不稳定 | 重试 + 丢弃策略已定义（FR-012 / §8） |
| 用户手写进度不可控（宪法 VII） | 里程碑独立可运行，每片完成即有增量价值 |
| **更换嵌入模型**（F1） | collection 维度绑定模型：新建 collection → 对存量条目全量重嵌入（raw 保留期内的直接重嵌入；超期的回产品源目录重采集，插件只读保证可重放）→ 验证后切换。蒸馏条目不重生成，仅向量重算 |
