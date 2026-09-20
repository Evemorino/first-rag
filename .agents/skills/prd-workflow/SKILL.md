---
name: prd-workflow
description: PRD 驱动的需求到交付工作流编排。以 PRD 为需求唯一事实来源，协调 spec-kit、grill-me、grill-with-docs、superpowers 的调用顺序、门禁与回退。适用于任何新需求或增量需求从 PRD 到实现交付的全流程管理。
---

# PRD 驱动的需求到交付工作流编排

## 1. 定位与外部编排

本 skill 是需求交付的**工作流编排层**，以 PRD 为需求事实来源，协调以下能力的调用顺序、门禁与回退。本 skill 负责流程，不替代它们的正式结果，也不伪造未执行的审查、测试或验证结论。

| Skill | 使用时机 | 通过标准 | 不通过回退 |
|---|---|---|---|
| `spec-kit` | `specify`、`clarify`、`checklist`、`plan`、`tasks`、`analyze`、`converge` | 当前阶段工件满足阶段门禁 | 回当前阶段修订，或回更早冲突来源 |
| `grill-me` | PRD/clarify 完成后；tasks 完成后；高风险变更实施前 | 关键问题可回答；无阻塞性假设、边界遗漏或验收歧义 | 回 clarify、tasks；必要时回 PRD 或 specify |
| `grill-with-docs` | spec/checklist 后；plan 后；converge 前；重大变更后 | 无阻塞冲突、遗漏、文档漂移或不可验证项 | 按问题归属回 PRD、spec、plan、tasks 或 implement |
| `superpowers`（非流程类能力） | implement 与验证期间按需 | 按该能力的协议完成调试、测试或验证 | 留在 implement，修复后重新验证 |

职责区分：`grill-me` 质询需求理解、假设、边界、失败路径与验收；`grill-with-docs` 核对 PRD、spec、plan、tasks、文档与现有实现之间的一致性；`superpowers` 只服务实现阶段，不得改变本流程的阶段顺序、门禁、权威工件或回退规则。

## 2. 核心原则

1. **PRD 是需求事实来源**：范围、目标、用户场景、功能与非功能需求、验收标准、边界与非目标，以 PRD 为准。
2. **可追溯性优先**：PRD → spec → plan → tasks → 实现 → 验证，必须能够相互追溯。
3. **先形成材料，再进行审查**：`grill-me` 和 `grill-with-docs` 不得在没有相应材料时作为形式化门禁。
4. **不通过必须回退**：回到问题所属的最早工件修订；修订影响审查范围时，必须重新审查。
5. **不跳过门禁**：未满足阶段退出条件，不得进入下一阶段。
6. **单一主流程**：不得并行启动会另建需求、规划或执行主流程的能力；此类能力的输出只能视为输入材料，回写到本流程的 PRD、spec、plan 或 tasks 并重新通过门禁。

## 3. 能力不可用时

- 缺少 spec 引擎：不得生成或伪造正式的 spec、plan、tasks 等工件；停止在相应阶段前。
- 缺少 `grill-me` 或 `grill-with-docs`：不得声称已通过正式审查，也不得放行相应门禁；标记为"待正式审查"。
- 缺少测试、执行或调试能力：不得伪造验证结果；明确说明未验证的范围与风险。
- 任何能力不可用时：说明缺失能力、受阻阶段、已完成内容及恢复所需条件。

## 4. 工件层级与冲突处理

| 层级 | 工件 | 作用 |
|---|---|---|
| 需求层 | PRD | 业务目标、范围、用户价值、验收与边界（最高需求事实来源） |
| 规格层 | spec | PRD 的结构化实现说明 |
| 检查层 | checklist | spec 质量门禁 |
| 设计层 | plan | spec 的技术实现方案 |
| 执行层 | tasks | plan 的实施分解 |
| 审查层 | grill 结果 | 阶段门禁证据 |
| 实现层 | 代码、测试、文档 | 对计划和需求的实现证据 |

冲突处理顺序：PRD 自相矛盾或与用户最新确认冲突 → 先澄清并更新 PRD；spec 与 PRD 不一致 → 修订 spec；plan 与 spec 不一致 → 修订 plan；tasks 与 plan/spec 不一致 → 修订 tasks；实现与已通过工件不一致 → 优先判断是实现偏差还是需求/设计变化。任何影响需求范围、验收或边界的变更，必须回写 PRD 并重新执行受影响门禁。

PRD 的必备内容、新需求/增量需求处理与变更控制规则见 [reference/prd-rules.md](reference/prd-rules.md)——**创建或更新 PRD 时读取**。

## 5. 阶段流程与门禁

```text
需求输入
  → PRD 创建 / 更新
  → specify → clarify → grill-me → checklist → grill-with-docs
  → plan → grill-with-docs
  → tasks → grill-me → analyze
  → implement → converge → grill-with-docs（最终）
  → 收尾
```

| 当前阶段 | 进入下一阶段的条件 | 不通过时 |
|---|---|---|
| PRD → specify | 目标、范围、场景、初步验收方向已明确；未知项已标记 | 修订/澄清 PRD |
| specify → clarify | spec 覆盖 PRD 核心要求且具备关联关系 | 修订 spec 或 PRD |
| clarify → grill-me | 无影响后续决策的待澄清项 | 回 clarify 或 PRD |
| grill-me → checklist | 假设、边界、异常流、验收无阻塞问题 | 回 PRD/spec/clarify |
| checklist → grill-with-docs | 必需检查项通过 | 回 spec 或 PRD |
| grill-with-docs → plan | PRD、spec、文档、约束与现状无阻塞冲突 | 按归属回 PRD/spec/clarify/checklist |
| plan → 审查 | Constitution Check 通过，技术风险有处置策略 | 修订 plan 或回 PRD/spec |
| 计划审查 → tasks | 无阻塞设计冲突、兼容性问题或文档漂移 | 回 plan 或上游工件 |
| tasks → grill-me | 任务覆盖计划，具备追溯、验证与依赖信息 | 修订 tasks/plan |
| 任务质询 → analyze | 任务边界、完成定义、异常与验收无阻塞问题 | 回 tasks/plan/PRD/spec |
| analyze → implement | 无 CRITICAL；HIGH 已修复或记录处置 | 按归属修订并重做受影响检查 |
| implement → converge | 所有任务满足完成定义；验证留证完整 | 留在 implement |
| converge → 最终审查 | 工件、实现、测试和文档已同步 | 回对应工件或 implement |
| 最终审查 → 收尾 | 零未处理缺口，标记 `Converged` | 按归属回退并重审 |

各阶段的输入、动作、产物与退出条件详见 [reference/stages.md](reference/stages.md)——**进入某个阶段时读取对应小节**。

## 6. 审查结论与回退规则

结论等级：

- **PASS**：无阻塞问题，可进入下一阶段；
- **PASS WITH NOTES**：无阻塞问题，但备注必须有责任归属和处理时机；
- **FAIL**：存在阻塞问题，不得进入下一阶段；
- **BLOCKED**：因必要输入或能力缺失无法形成正式结论。

只有 `PASS` 和 `PASS WITH NOTES` 可通过门禁。

回退按问题归属：业务范围、规则、场景、验收或非目标 → 回 PRD；可实现规格、行为定义或需求映射 → 回 spec/clarify；技术设计、接口、数据、迁移或测试策略 → 回 plan；拆分、排序、完成定义或覆盖度 → 回 tasks；编码、测试、缺陷或验证证据 → 回 implement。回退修订后，必须重做所有受影响的下游检查、审查和分析。

问题记录格式见 [reference/stages.md](reference/stages.md) 的"问题记录格式"一节——**记录审查问题时读取**。

## 7. 实施与收尾

1. 实施不得超出已通过的 PRD/spec/plan 范围；新增范围必须先走变更控制。
2. 每项任务完成时，更新其状态、关联验证和实现证据。不得以"已实现"代替"已验证"；无验证证据的内容不得标记完成。
3. 测试应覆盖对应的 FR/NFR/AC，尤其是边界和失败路径。`superpowers` 的辅助结果必须落入项目实际代码、测试或验证记录，不得只保留口头结论。
4. 若实施中发现 PRD 不完整、spec 不可实现、设计错误或验收不充分，立即停止将其作为单纯编码问题处理，按第 6 节回退。

收尾时输出交付摘要，至少包括：PRD 版本与本次变更范围；已实现的 FR/NFR/AC；明确未实现、延期或不在范围的内容；关键技术决策、迁移或兼容性说明；测试与验证证据；`grill-me` 与 `grill-with-docs` 的最终状态；已知风险、限制和后续事项；最终结论（`Converged` / `Converged with Notes` / `Not Converged`）。

仅当最终 `grill-with-docs` 通过，且所有阻塞项关闭或经批准处置后，才可标记为 `Converged`。
