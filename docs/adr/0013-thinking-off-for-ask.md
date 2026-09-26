# ADR-13 思维链默认关，但**只对 ask 关**：`retrieval.disable_thinking`

- **状态**：Proposed —— **决策段待用户撰写**（LG-005：结论由用户写，AI 只给素材与选项）
- **日期**：2026-09-26
- **素材**：`specs/001-learning-memory-rag/adr-candidates.md` 的 ADR-c3（AI 整理，2026-09-26）
- **实施位置**：`src/ask.py` 读配置传 `thinking=not retrieval["disable_thinking"]`；
  `src/ark_client.py` 在 `thinking=False` 时加 `extra_body={"thinking":{"type":"disabled"}}`；
  `config/schema.json` 的 `retrieval.disable_thinking`（默认 true）

## 背景

doubao 系模型**默认跑思维链**，问答因此目测"很慢"。先前的归因是"模型 TTFT 天生
20–40s"——**那是误判**，真因是思维链（响应里带 `reasoning_content`）。
`ark_client.chat` 的默认 `thinking=True` 语义是"**不传这个键、让服务端保持原样**"，
不是"显式打开"。

## 备选与被否的理由

| 方案 | 当时为何没选 |
|---|---|
| 调低 `max_tokens` | 实测无效：`max_tokens=30` 仍 38s，200 要 43.5s |
| 靠流式掩盖延迟 | 首字仍卡在思考上（33.4s），只是感知稍好 |
| 换模型 | 未走 |
| **全局关（含蒸馏）** | 蒸馏关掉后同批条目 **9 → 28 条**，质量/数量的差异未经评估 → 只对 ask 关 |

## 证据

- TTFT 33.4s → **1.6–4.1s**；整轮 71.6s → **5.91s**；流式首字 **0.7s**。
- 归因教训：把 71.6s 记成"模型天生慢"是误判，真因是思维链 —— 这条比数字本身更值得留。

## 后果与可逆性

- **可逆性**：配置项，改 1 个布尔值。
- **未评估的代价**：关思维链是拿**回答质量**换延迟，而这个交换当时是性能问题驱动的，
  没有做质量对照。

## 决策（待你写）

请回答这三个问题，答案就是这一节的正文：

1. **"蒸馏不关 thinking"（因为 9→28 条、质量未评估）这个评估做不做？** 不做的话等于把
   质量永久悬置 —— 而 9→28 是 3 倍量级，很难说与质量无关。做的话用什么判据（rubric 重蒸馏对照？人工抽查 N 条？）
2. **哪个是安全默认**：`default true（关）` 还是 `default false（开）`？你现在知道这个交换是
   质量换延迟了吗（当时不知道）—— 知道之后要不要改默认？
3. **状态**：Accepted / 仍 Proposed 待评估？
