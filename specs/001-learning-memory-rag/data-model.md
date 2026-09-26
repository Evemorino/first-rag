# Data Model: first-rag

来源：PRD FR-014/015/016/017、§6。ID 均指 PRD 条目。

## Qdrant Collection `learning_memory`

- 距离：cosine；维度：首次嵌入真调响应动态读取（M0 建集合脚本）
- **Point ID**：`uuid5(NAMESPACE_URL, f"{source}|{date}|{content_hash}")`，content_hash = text 的 sha256 十六进制摘要的**前 16 个字符**（即 8 字节；`src/ids.py:26` 的 `hexdigest()[:16]`）（PRD FR-014）

### Payload schema（每字段一行依据）

| 字段 | 类型 | 依据 |
|---|---|---|
| `text` | str，蒸馏条目正文，单条 ≤1000 字 | FR-015 |
| `date` | "YYYY-MM-DD"（Asia/Shanghai 折算） | FR-015、§6 |
| `type` | str，枚举来自 config types[].name | FR-016 |
| `tags` | list[str]，蒸馏打，2–5 个 | FR-015 |
| `source` | 插件名之一（v0.7 起共 11 个）："claude_code" / "codex" / "kimi_code" / "trae_work_cn" / "trae" / "qoder" / "qoder_cn" / "workbuddy_ai" / "opencode" / "zcode" / "hermes"，外加非插件的 "git" / "manual" | FR-015、FR-002a |
| `project` | str 或 null（仓库/项目路径名） | FR-015 |
| `created_at` | ISO 8601 时间戳（入库时刻） | FR-015 |
| `source_refs` | list[str]（会话文件标识 / 提交 hash / 快记文件名） | FR-015 |
| `distill_version` | str（`{model}+rubric@{config_hash 前 8 位}`） | FR-006 |
| `related` | list[uuid str]，上限 5，双向 | FR-017 |

## Raw 快照 `data/raw/YYYY-MM-DD.json`

```json
{
  "date": "2026-09-18",
  "collected_at": "2026-09-18T22:37:00+08:00",
  "distill_run": {
    "model": "…", "rubric_hash": "…", "status": "ok|failed|noop"
  },
  "materials": [
    {"source": "claude_code", "ref": "…", "ts": "…",
     "kind": "message|error|commit|note|trae_record",
     "text": "…", "meta": {"cwd": "…", "struggle_rounds": 2}}
  ]
}
```

## config/schema.json

```json
{
  "types": [{"name": "progress", "desc": "…"}, {"name": "error", "…"},
            {"name": "idea"}, {"name": "reflection"}],
  "distill": {
    "include_signals": ["…"], "exclude_signals": ["…"],
    "examples": {"keep": ["…"], "drop": ["…"]},
    "novelty_threshold": 0.82, "max_entries_per_day": 30,
    "struggle_rounds": 3, "max_raw_chars": 2000000,
    "batch_max_chars": 120000, "parallel_workers": 8
  },
  "retrieval": {
    "top_k": 8, "answer_max_tokens": 600, "disable_thinking": true,
    "expand": {"mode": "all", "neighbor_limit_per_hit": 2,
               "context_cap": 12, "neighbor_min_score": null}
  },
  "trae_type_map": {"learned": "reflection", "outcome": "progress"},
  "raw_retention_days": 90
}
```

（values 为初版默认，全部可改；结构即契约。其中 `distill.batch_max_chars` / `parallel_workers`
与 `retrieval.answer_max_tokens` / `disable_thinking` 是**实施期新增**——分别对应蒸馏分批并行
（NFR-006，sync 983s → 215.9s）与思维链默认关闭；其 ADR 结论待补，见 `tasks.md` T067）

## 统一中间格式（插件 parse 输出）

`RawMaterial = {source, ref, ts, kind, text, meta}` —— 与快照 materials 元素同构（PRD FR-001"统一中间格式"）。
