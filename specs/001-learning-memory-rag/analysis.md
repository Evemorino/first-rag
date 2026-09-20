# Analysis: first-rag v0.1（阶段十一产出，2026-09-18）

**Input**: PRD.md v0.4 + spec.md + plan.md + tasks.md + data-model.md + plugin-contract.md + constitution v1.0.0
**范围**: 实施前的一致性与就绪度分析。分析结论处停，等用户确认后进入 implement。

## 1. 追溯链完整性

```
PRD(25 FR / 8 NFR / 11 NG / 14 AC) → spec(23 spec-FR ↔ 16 SC) → plan(M0–M9 ↔ AC) → tasks(T001–T045 ↔ FR/AC)
```

- FR 25/25 → tasks 落位（阶段十核查）；AC 14/14 → tasks 验证项
- SC 16/16 挂 AC（SC-016 基础设施连通 ← AC-001，阶段六补）
- 唯一未闭环：FR-008 行为证据（struggle rounds）在 T010 采集、T015 prompt 注入，但**无独立验证项**——已判定可接受：蒸馏质量属 AC-002 端到端验收面，不单测 LLM 判断力

## 2. 本 workflow 全程缺陷台账（学习价值，LG-004）

| 阶段 | 缺陷 | 修复 |
|---|---|---|
| 五 checklist | 4 处需求缺陷（trae 类型映射缺失、FR-023 模糊、快记矛盾、backfill 上限未定义） | PRD v0.4 |
| 六 grill-with-docs | AC-001 无 SC 映射、旧 plan 漂移 | SC-016 + 降级注记 |
| 八 计划审查 | F1 嵌入模型迁移缺失、F2 sync 并发锁、F3 类型归属、F4 quickstart 时序矛盾 | plan/quickstart 修订 |
| 十 任务质询 | G1 Dogfood① 越序、G2 ask 时延无验证、G3 git 采集无验证 | tasks.md 修订 |

10+3 处，全部在动手写代码前拦下。**结论：门禁有效，预测-验证机制成立（LG-003）**。

## 3. 实施前风险排序（代码期最可能触发的三件事）

1. **claude_code JSONL 解析与真实数据偏差**（research.md 4/6 确认，本项目为其中之一，剩余风险低但非零）→ T010 先跑只读探测再写解析；插件隔离，回滚面小
2. **Ark 嵌入模型 ID / 维度不确定** → M0 T005 一次性真调 + 动态建集合，是全链路第一个硬闸门
3. **★ 手写任务进度不可控** → 纵切结构保证每片独立可运行，等待不阻塞相邻任务（T011 可先于 T015 完成并测试）

## 4. 就绪度结论

- 六个工件 + 宪法交叉一致，无未解决的矛盾
- 写入边界、幂等、脱敏三条红线均有任务 + 测试双落位（宪法 IV/V）
- **结论：READY——可进入阶段十二 implement**。首个动作为 T001（脚手架），T004（★ ark_client）由用户手写，AI standby review

## 5. 停点

按约定停在 analyze 结论。用户确认后：implement 阶段启动（AI 生成非 ★ 任务脚手架、用户手写四个核心模块）。
