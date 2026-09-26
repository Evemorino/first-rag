# Plugin Contract: 采集插件

来源：PRD FR-001/FR-002/FR-002a/FR-002b、NFR-001、宪法 III/V。本契约是插件与核心之间的唯一边界。

## 目录约定

```
src/plugins/<product>/
├─ __init__.py     # 必须：PLUGIN = Plugin(name="<product>", discover=…, parse=…)
└─ …               # 私有解析逻辑，核心不感知
```

registry（`src/plugins/__init__.py`）扫描 `src/plugins/` 下所有含 `PLUGIN` 的包并注册；
目录不存在 / 导入失败 / discover 抛异常 → **静默跳过 + 日志**（NFR-004）。

## 两个必须实现的函数

```python
def discover(date: date) -> list[SourceRef]:
    """返回该日存在的素材引用。目录为空/消失时返回 []（不抛异常）。"""

def parse(ref: SourceRef) -> RawMaterial:
    """把单个素材解析为统一中间格式。
    RawMaterial = {source, ref, ts, kind, text, meta}（data-model.md）。
    ts 必须是本地时区（Asia/Shanghai）感知的时间。"""
```

## 素材路径声明

- **raw 插件**（claude_code / codex / kimi_code / qoder / qoder_cn / workbuddy_ai / opencode / zcode / hermes）：完整解析会话，提取用户消息、助手文本、工具报错（kind=message|error）；行为证据（struggle 轮次）在 meta 供蒸馏使用（PRD FR-008）
- **pre-summarized 插件**（trae_work_cn / trae）：`{intent, actions, outcome, learned}` 轻转换直接并入（kind=trae_record），type 经 `config.schema.json` 的 `trae_type_map` 映射，未匹配默认 `reflection`（PRD FR-002、FR-002a）

两族按**读取方式**分（PRD FR-002a）：A 族读文本（JSONL/JSON），B 族读 SQLite。族内也不保证可复用——
`workbuddy_ai` 是 CodeBuddy 系 schema（连时间戳策略都与 qoder 系相反），`qoder`/`qoder_cn` 是一套 schema，
`trae`/`trae_work_cn` 是一套，三者互不通用。

**"同源"不等于"共用代码"**：`qoder`/`qoder_cn`、`trae`/`trae_work_cn` 两组各自 schema 相同，但**仍然各写各的
解析器**（规则 6 不容许例外，见下）。同源只体现在两点：一是两边的解析逻辑应当等值，二是这条等值关系
由**跨插件等价性测试**钉住（同一份 fixture 喂两个解析器，断言除 `source` 外逐字段相同）。选重复而非共享，
是因为共享在这套分层里唯一的合法落点是 registry，而那会让 registry 长出厂商解析职责（宪法 II 纯核薄壳）。

## 插件必须遵守（宪法层约束）

1. **只读**：对产品源目录零写入（宪法 V）。**SQLite 源 MUST 用 `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`**——普通 `connect()` 会在源目录落 `-wal`/`-shm`/`-journal`，就是写入。此条由 `scripts/write_boundary_check.py` 机械拦截（PRD NFR-001）。
   **但不要把 `mode=ro` 读成「绝不落 sidecar」**：它保证的是不改 `db` 本身（2026-09-26 实测：sidecar 不存在时只读连接会自己造出 `-wal`/`-shm`；源目录不可写时则打不开）。边界全文见 PRD NFR-001 的「已知边界」。
2. **不碰凭据**：B 族用**表允许列表**（只读明确列出的表），A 族用**路径允许列表**。已知须排除：`opencode.db` 的 `credential`/`account`/`control_account`；`~/.zcode/v2/credentials*.json`、`~/.hermes/{.env,auth.json}`、`~/.workbuddy/connectors/`、`~/.qoder-cn/` 下的 `state.json`（PRD NFR-001）
3. **注入物不是用户输入**：逐源过滤（zcode 的 `message.data.synthetic`；workbuddy_ai 只取 `<user_query>…</user_query>`）。每个源 MUST 带一条"注入物不得产生素材"的单测（PRD FR-002a）
4. **无核心依赖倒置**：只 import 核心暴露的类型（SourceRef/RawMaterial），不 import ark_client/ingest 等（宪法 III）
5. **不绕过 config**：类型映射、阈值一律读配置，不硬编码
6. **插件之间不互相 import**：`scripts/lint_layers.py` 强制，**不开例外**——连"schema 逐字段相同的同源插件对"（qoder/qoder_cn、trae/trae_work_cn）也不开，见上文。这一条就是**没有共享 SQLite helper 模块**的原因：三个 B 族插件各写各的三行连接。共用的代价用测试补，不用耦合补

## 新插件接入流程（US-6 验证）

1. 复制 `_template/` → `<product>/`
2. 实现两个函数（先跑 `python scripts/schema_check.py <真实文件>` 确认格式——只读照结构，不判对错）
3. 放入目录即被 registry 自动发现——核心零改动（AC-011 的插件面）

## 核心模块接口（供插件作者了解上下文，不强制依赖）

见 plan.md "Module Contracts" 节。
