# Spec Review Checklist: first-rag 个人学习记忆系统

**Purpose**: 阶段五需求质量门——以"需求的单元测试"方式检验 PRD v0.4 与 spec 的完整性、一致性、可测试性
**Created**: 2026-09-18
**Feature**: [spec.md](../spec.md) · [PRD.md](../../../PRD.md) · [constitution.md](../../../.specify/memory/constitution.md)

**Note**: 本 checklist 由 `$speckit-checklist` 流程生成。标记 `[x]` 表示需求质量维度已审满足，不代表实现完成。

## Requirement Completeness（需求完整性）

- [x] CHK001 六个用户故事均有可独立测试的验收场景与失败路径 [Spec §User Scenarios]
- [x] CHK002 外部依赖（嵌入/蒸馏服务、向量库、产品会话文件）的失败模式均有明确处理 [PRD §8]
- [x] CHK003 数据生命周期完整：raw 保留期、备份、迁移、彻底删除均有定义 [PRD §6]
- [x] CHK004 trae pre-summarized 素材到类型枚举的映射规则已定义（`trae_type_map` 配置，未匹配默认 reflection）[PRD FR-002，v0.4 修复]

## Requirement Clarity（需求清晰度）

- [x] CHK005 阈值类需求全部量化（相似度 0.75/0.85、保留期 90 天、条数 30、时长 5 分钟）[PRD 全文]
- [x] CHK006 重蒸馏"新增/消失/改写"判定规则精确无歧义（共享溯源 + 相似度 ≥0.85 配对）[PRD FR-023，v0.4 修复]
- [x] CHK007 "关联补充"标记的行为明确（进 LLM 上下文、prompt 注明酌情使用、`--no-expand` 单次关闭）[PRD FR-020]

## Requirement Consistency（需求一致性）

- [x] CHK008 快记类型处理无矛盾：FR-013（不走 LLM 蒸馏）与边界表（轻处理不改类型）一致 [PRD §8，v0.4 修复]
- [x] CHK009 PRD ↔ spec ↔ constitution 三层无术语与规则冲突（写入边界、纯核薄壳、非目标措辞逐条核对）[三文件]
- [x] CHK010 交付顺序声明与纵切交付原则一致（FR-002 Q2-A ↔ LG-002）[PRD §12]

## Acceptance Criteria Quality（验收质量）

- [x] CHK011 每条 FR 至少被一条 AC/SC 兜底（25 FR ↔ 14 AC ↔ 16 SC 映射核对；SC-016 系阶段六补齐 AC-001 映射）[PRD §10, spec §SC]
- [x] CHK012 全部 AC 可机械验证，观察点明确（含 AC-012 脱敏、AC-014 熔断）[PRD §10]

## Scenario Coverage（场景覆盖）

- [x] CHK013 正常流、空态（无素材日）、失败流在六个故事中均有覆盖 [Spec §User Scenarios, Edge Cases]

## Edge Case Coverage（边界覆盖）

- [x] CHK014 关联边双向回填遇对方已达上限 5 时的行为已定义（跳过该方向，保持确定性）[PRD FR-017，v0.4 修复]
- [x] CHK015 时区折算、跨日、超量素材截断、非法 LLM 输出（JSON/未知类型）均有边界定义 [PRD §8]

## Non-Functional Requirements（非功能需求）

- [x] CHK016 隐私四件套（脱敏、密钥环境变量、源目录只读、三处永久 gitignore）均有验收兜底 [NFR-001/002 + AC-012]
- [x] CHK017 性能与可恢复性可测（sync ≤5 分钟；备份 = 拷目录）[NFR-006/007 + SC-011/012]

## Dependencies & Assumptions（依赖与假设）

- [x] CHK018 假设显式且带验证方式（模型 ID setup 时真调验证；格式漂移隔离在插件层）[PRD §9]
- [x] CHK019 学习过程约束已传导至下游要求（PRD §12.3 → plan/tasks 待执行项）[PRD §12]

## Ambiguities & Conflicts（歧义与冲突）

- [x] CHK020 无残留 [NEEDS CLARIFICATION] 或未解释占位符（PRD/spec/constitution 三处扫描）[三文件]

## Notes（审计轨迹）

- **初检（2026-09-18）**：4 项未过——CHK004（trae 类型映射缺失）、CHK006（重蒸馏对齐规则含糊）、CHK008（快记类型 FR-013 与边界表矛盾）、CHK014（关联边回填超上限未定义）。
- **处置**：按"不通过 → 回 PRD 修订"规则升 PRD 至 v0.4（FR-002/FR-023/FR-017/§8），spec 同步四处。
- **复审**：修订后 20/20 全过。本清单由模型执行复审；阶段六 grill-with-docs（用户手动触发）是对它的独立复核。
- 4 项修复的默认值取舍（映射默认 reflection、配对阈值 0.85、回填跳过、轻处理不改类型）均取合理默认，用户可否决——改 PRD 即可，见 v0.4 变更记录。

## v0.8 增量复核（2026-09-28）：审阅页与条目修正

以"需求的单元测试"方式复核 PRD v0.8.0 / spec 的 US-7 增量（CHK021~CHK028）。

- [x] CHK021 新场景的目标与真实问题对齐：US-7 的 Why now 指明"库是唯一学习资产、却没有人工校正入口"，不是为做页面而做页面 [PRD §2 US-7]
- [x] CHK022 范围与非目标同步更新：NG-002 撤销**同时**收窄边界（不含公网/多用户/通用笔记编辑器）；两条待澄清项（硬删除、合并条目）已裁决为"本版不做" [PRD §3.2、§9]
- [x] CHK023 幂等基石未被破坏：人工编辑走覆写层，ID 生成规则零改动；`date`/`source` 在界面上不可编辑（要改只能走迁移脚本）[PRD FR-028、spec FR-026]
- [x] CHK024 重放路径有明确规则：同日重放不覆盖人工字段、不复活软删条目（AC-009 的重放是常规操作，不是边角）[PRD NFR-011、spec 边界条件]
- [x] CHK025 删除语义与安全边界写清：软删可恢复、不级联删边；并**如实声明**它不构成"敏感内容已清除" [PRD FR-029、ADR-19]
- [x] CHK026 访问面有明确约束：无鉴权（NG-005 不撤销）→ 只绑 `127.0.0.1` + 写接口同源校验；撤销 NG-005 前不许监听 `0.0.0.0` [PRD NFR-010、ADR-20]
- [x] CHK027 验收可观察可机械验证：AC-018~021 四条各自对应可执行动作，其中三条（编辑不换 ID / 软删不复活 / 留痕不丢）定为**必须自动化**并与夹具复用方式一并写明 [PRD §10、plan 测试策略]
- [x] CHK028 技术取舍有记录：三条 ADR（覆写层 / 软删除 / 零构建 + 本机）记了备选与被否理由；LG-005 的例外（用户授权按推荐值采用）显式标注 [docs/adr/0018~0020]

**结论：8/8 通过。** 与阶段四质询结论（frontier 已清空，`v08-decisions.md` Round 1–3）一致，无阻塞项。
