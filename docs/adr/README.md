# ADR 目录

ADR-1…11 是**实施前**的决定，以三列摘要表的形式记在 [`PRD.md`](../../PRD.md) §7。
实施期又攒下够格的新决定，共 6 条候选（素材见
[`specs/001-learning-memory-rag/adr-candidates.md`](../../specs/001-learning-memory-rag/adr-candidates.md)），
按信息密度分两路处理（2026-09-26 决定）：

- **三条写成完整 ADR**（备选与被否理由、证据塞不进三列表）——本目录：
  - [ADR-12 按**输出**切批](0012-batch-by-output.md)（`distill.batch_max_chars`）
  - [ADR-13 思维链默认关，但只对 ask 关](0013-thinking-off-for-ask.md)（`retrieval.disable_thinking`）
  - [ADR-14 快照写保护](0014-snapshot-write-protection.md)（`ALLOW_SHRINK` 明路）
- **另外三条**（`parallel_workers`、SQLite `mode=ro`、迁移判据用 ref 路径）信息量本来就是
  一行，**结论定了之后回 PRD §7 加一行摘要**，不单独建文件。

## 这三份现在是 Proposed

**`## 决策（待你写）` 那一节是空的，等用户本人写**（宪法 §12.1 LG-005：实施中新增的
技术决策，AI 只提供素材与选项，结论由作者写）。每份文件里已经填好：背景、备选与被否的
理由、当时的证据、可逆性与已知代价，以及**三个具体的待答问题** —— 把答案写成那一段，
状态改成 Accepted 即可。

写法（省事版）：把 `## 决策（待你写）` 整节替换成
`## 决策` + 你的结论段落（两三句也够），再把顶部的状态行改成 `Accepted（YYYY-MM-DD）`。
改完顺手在 PRD §13 加一行说明，并把 `tasks.md` 的 T067 勾上。

**为什么不是 AI 代写**：这 6 条里有 3 条的核心是"当时没做的那个评估"（切批的 120000 依据、
蒸馏不关思维链的质量对照、拒写 vs 备份），写不出来 = 还没想清楚 —— 那正是这条会进 ADR 的原因。
