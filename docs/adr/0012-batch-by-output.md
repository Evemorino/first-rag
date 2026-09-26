# ADR-12 按**输出**切批：`distill.batch_max_chars`

- **状态**：Proposed —— **决策段待用户撰写**（LG-005：结论由用户写，AI 只给素材与选项）
- **日期**：2026-09-26
- **素材**：`specs/001-learning-memory-rag/adr-candidates.md` 的 ADR-c2（AI 整理，2026-09-26）
- **实施位置**：`src/distill_batches.py:split_for_batches`、`src/distill.py` 按批调用、`config/schema.json` 的 `distill.batch_max_chars`（默认 120000）

## 背景

一次 `distill` 把当天全部素材塞进**一次** chat 往返时，方舟会以
`finish_reason='length'` 截断输出，`json.loads` 报 `Unterminated string`，
`_parse_or_retry` 的重试是**原样再发同一个 prompt**，于是注定再断一次 ——
一整天 0 条入库，还白烧 218 秒与一堆 token（README 有专节记这条）。

## 备选与被否的理由

| 方案 | 当时为何没选 |
|---|---|
| 加大 `max_tokens` | 无关：断的是**服务端 completion 上限**，不是请求参数。实测 `max_tokens=30` 也一样 |
| 靠 `_parse_or_retry` 重试 | 重试**原样再发**，同一处再断一次，整天 0 条入库 |
| 按素材**条数**切批 | 尺寸极不均（一条会话可达 200000 字符），字符数才是决定量 |
| 流式收输出再拼 JSON | 未走；解析半截 JSON 的成本没评估 |

## 证据

- 09-18 断在 **6894 字符 / 6865 completion tokens**、`finish_reason='length'`；
  而输入大 5.7 倍的 09-23（1.33M 字符）**一次就过** —— 决定成败的是**输出**长度，
  不是输入长度。这正是本条反直觉、值得留痕的地方。
- 修完同一天实测：188.06s、2 次 chat 往返、27 条入库、库内 39 点。

## 后果与可逆性

- **可逆性**：配置项，改 1 个数字。
- **副作用（要显式记进决策）**：切批后 `source_refs` 只能引用**本批**的素材
  （`src/distill.py`），跨批的关联在蒸馏期就断了 —— 同一事件分在相邻两批时，
  关联边要等库内相似度去补，蒸馏期看不出来。

## 决策（待你写）

请回答这三个问题，答案就是这一节的正文：

1. **120000 这个数认不认？** 仓库里没有它的推导过程（`src/`、README、PRD、specs 都搜过了）。
   相对实测断点（~6.9k 输出）它是"宽松到不会断"，还是"刚好"？若你要换数，换多少、依据什么？
2. **跨批关联断裂**是可接受的代价，还是要在 ADR 里写成已知副作用并补一条观测/补偿？
3. **状态**：Accepted（并写明生效日期）还是仍留 Proposed 待评估？
