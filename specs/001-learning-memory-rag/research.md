# Research: 数据源探测结论

**Date**: 2026-09-17/18 | 探测方式：只读检查用户机器真实文件

## 各产品素材格式（已实测确认 4/6 —— 但 kimi 当初只实测了路径，事件形态是 2026-09-24 补的）

| 产品 | 路径 | 格式要点 | 路径类型 |
|---|---|---|---|
| claude-code | `~/.claude/projects/<project>/*.jsonl` | 每行一条事件（user/assistant 消息、tool_result 含 is_error） | raw |
| codex | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `session_meta`（cwd/git branch）+ `response_item`（role 消息、时间戳） | raw |
| kimi-code | `~/.kimi-code/sessions/wd_<ws>/session_<id>/` | `state.json`（cwd/title/createdAt）+ `agents/main/wire.jsonl` 事件流。**事件形态 2026-09-24 才实测补齐**（原来只确认了路径，插件照别家产品的常见 shape 猜，结果对 39206 个真实事件认出 0 条）：`turn.prompt`（`input[].text`，`origin.kind` user/task/skill_activation）、`agent.message.appended`（`message.message.{role,content[]}`，content 分 `text` 与 `think`）、`context.append_loop_event`（`event.type` tool.call/tool.result…，报错看 `result.isError`）。时间戳一律 **int 毫秒** | raw |
| trae(TraeWork CN) | `~/.trae-cn/memory/projects/<path>/YYYYMMDD/session_memory_*.jsonl` | 每行 `{intent, actions, outcome, learned, message_summary_time}` —— 近终态，走 pre-summarized 轻转换 | pre-summarized |
| qoder | `~/.qoder/projects/<path>/memory` | 实测为空，暂无数据 | — |
| zcode | `~/.zcode/` | 结构不明，暂缓 | — |

（qoder/zcode 为 PRD NG-010 非目标）

## 数据量实测（保留决策依据）

- 原始会话总量：codex ~20M / kimi ~68M / claude ~17M
- 单日 raw 提取：100K–1M 字符（上限截断 ~2M）
- 单日蒸馏输出预期：5–10K 字符、≤30 条（熔断）
- 结论：磁盘非约束；raw 保留 90 天默认值的依据

## Ark API（方舟，OpenAI 兼容）

- `https://ark.cn-beijing.volces.com/api/v3`，Bearer key（`.env`，已订阅）
- 嵌入模型 doubao-embedding；**确切模型 ID 与维度未经真调验证**（M0 任务，PRD §9 假设）
- volcengine 文档 JS 渲染抓取不到正文，以真调为准

## 环境备注

- ark 代理会话中 Bash/Agent 安全分类器间歇超时、WebSearch 403——影响开发体验不影响产品设计；实施时若复现用重试/替代工具
- Docker 29.4.0 daemon 就绪；Python 3.13.15 就绪
