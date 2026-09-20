# Plugin Contract: 采集插件

来源：PRD FR-001/FR-002、宪法 III。本契约是插件与核心之间的唯一边界。

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

- **raw 插件**（claude_code / codex / kimi_code）：完整解析会话，提取用户消息、助手文本、工具报错（kind=message|error）；行为证据（struggle 轮次）在 meta 供蒸馏使用（PRD FR-008）
- **pre-summarized 插件**（trae）：`{intent, actions, outcome, learned}` 轻转换直接并入（kind=trae_record），type 经 `config.schema.json` 的 `trae_type_map` 映射，未匹配默认 `reflection`（PRD FR-002）

## 插件必须遵守（宪法层约束）

1. **只读**：对产品源目录零写入（宪法 V）
2. **无核心依赖倒置**：只 import 核心暴露的类型（SourceRef/RawMaterial），不 import ark_client/ingest 等（宪法 III）
3. **不绕过 config**：类型映射、阈值一律读配置，不硬编码

## 新插件接入流程（US-6 验证）

1. 复制 `_template/` → `<product>/`
2. 实现两个函数（先跑 `schema_check` 用真实文件确认格式）
3. 放入目录即被 registry 自动发现——核心零改动（AC-011 的插件面）

## 核心模块接口（供插件作者了解上下文，不强制依赖）

见 plan.md "Module Contracts" 节。
