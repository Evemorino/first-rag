# ADR 目录

ADR-1…11 是**实施前**的决定，以三列摘要表的形式记在 [`PRD.md`](../../PRD.md) §7。
实施期又攒下够格的新决定，共 6 条候选（素材见
[`specs/001-learning-memory-rag/adr-candidates.md`](../../specs/001-learning-memory-rag/adr-candidates.md)），
按信息密度分两路处理（2026-09-26 决定）：

- **三条写成完整 ADR**（备选与被否理由、证据塞不进三列表）——本目录：
  - [ADR-12 按**输出**切批](0012-batch-by-output.md)（`distill.batch_max_chars`）
  - [ADR-13 思维链默认关，但只对 ask 关](0013-thinking-off-for-ask.md)（`retrieval.disable_thinking`）
  - [ADR-14 快照写保护](0014-snapshot-write-protection.md)（`ALLOW_SHRINK` 明路）
  - [ADR-18 人工修正走**覆写层**](0018-entry-override-layer.md)（改正文不换 ID、不重算关联边）
  - [ADR-19 删除用**软删除 + 检索期过滤**](0019-soft-delete-by-filter.md)（不做硬删除、不级联删边）
  - [ADR-20 审阅页**零构建**且只绑本机](0020-zero-build-local-ui.md)（无 npm/CDN + 写接口同源校验）
  - [ADR-21 审阅页改用**受限引入的前端工程**](0021-frontend-toolchain.md)（Vite + React + Zustand + Tailwind + TanStack Query；**取代 ADR-20 第 1 条**）
- **另外三条**（`parallel_workers`、SQLite `mode=ro`、迁移判据用 ref 路径）信息量本来就是
  一行，**结论定了之后回 PRD §7 加一行摘要**，不单独建文件。

## 这三份现在是 Accepted（2026-09-27 用户裁定）

三条由用户 2026-09-27 逐题裁定（选项清单与理由见 `notes/decision-sheet.md`），`## 决策` 段已成文、
状态转 **Accepted**、摘要并入 `PRD.md` §7 表。裁定要点：

- **ADR-12**：认 `120000`，但如实记为**经验值**（判据是实测断点 ~6.9k 输出，不是推导值）；
  跨批关联断裂记为已知副作用，并留一条观测项（下次 `ingest` 后抽查"同事件分两批"的样点）。
- **ADR-13**：蒸馏的质量对照**不豁免**（列入待办，用 `redistill` 抽一天对照类型分布 + 人工抽查）；
  默认值不改 —— 正确性由「只关 ask」这个**边界**保证，默认值是延迟驱动定下的，质量对照欠着。
  **2026-09-28 更新**：对照已执行（T086，只读配对，未走会写库的 `redistill`）并**定性结案** ——
  「已评估：无证据显示 OFF 有质量退化」，判据（事件覆盖 + 类型分布 + JSON 有效性）与结论边界
  （单日单次采样）见 ADR-13 的「质量定性（2026-09-28 用户裁定）」一节。
- **ADR-14**：取「拒写 + `ALLOW_SHRINK` 明路」（更怕静默弄丢基线）；变异分数 89.6% → 88.6%
  记为已接受代价、并写明这是**度量口径问题**；写明「守卫的完备性由变异测试守、不由覆盖率守」。

## 三条一行式候选已结（T085 → PRD §7 的 ADR-15/16/17）

`parallel_workers` / SQLite `mode=ro` / 迁移判据用 ref 路径 —— 信息量本来是一行，不单独建文件：
2026-09-27 由用户裁定（全 A）后**并入 `PRD.md` §7 表**（ADR-15 / ADR-16 / ADR-17），
逐题材料留在 `specs/001-learning-memory-rag/adr-candidates.md` 各条的「待你判断」段。

## 以后新增 ADR 怎么写

沿用本目录格式：`NNNN-title.md`（编号续下一号），头部写状态 / 日期 / 素材 / 实施位置，
正文四段 —— 背景、备选与被否的理由、证据、后果与可逆性，最后是 `## 决策`。
