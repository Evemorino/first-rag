<!--
================================================================================
SYNC IMPACT REPORT (temporary scratch material for human review; remove before commit)
================================================================================
Version change: 0.0.0 (unratified template) → 1.0.0 (initial ratification)
Modified principles: N/A (initial version)
Added:
  - Core Principles (7 principles, V tagged NON-NEGOTIABLE)
  - Architecture & Technology Constraints
  - Development Workflow (process invariants only; procedure details live in prd-workflow skill)
  - Governance
Removed sections: N/A
Templates requiring updates: none — plan-template.md Constitution Check compatible by design.
Follow-up TODOs: none. All placeholders replaced.
================================================================================
-->

# first-rag Constitution

## Core Principles

### I. 单一事实来源（Single Source of Truth）

PRD 是需求的唯一事实来源。下游工件（spec / plan / tasks）MUST 以编号引用
PRD 的 FR / NFR / AC / NG 条目，MUST NOT 自行改写其措辞。范围、验收标准
或业务规则的任何变更 MUST 先落 PRD，再改下游工件；发现下游与 PRD 冲突时，
以 PRD 为准并回退下游。

理由：多个工件各自表述同一需求必然漂移；引用编号使一致性可机械校验。

### II. 纯核薄壳（Pure Core, Thin Shell）

业务逻辑 MUST 只存在于不依赖任何入口协议的纯函数核心层
（采集 / 蒸馏 / 入库 / 检索）。CLI、HTTP API、cron 及未来的 MCP 接入
MUST 保持薄入口：只做参数校验与调用核心层，MUST NOT 含业务逻辑。
新增入口 MUST NOT 要求修改核心层。

理由：入口形态会持续增加（网页、MCP、其他工具），核心只有一个。

### III. 配置驱动扩展（Configuration-Driven Extension）

新增类型、蒸馏标准、检索参数 MUST 通过配置完成（config/，
零代码迁移）；新增采集源 MUST 通过插件目录完成
（src/plugins/，registry 自动发现）。两者 MUST NOT 要求修改核心代码。
插件 MUST 只依赖核心暴露的契约（discover / parse / 统一中间格式）。

理由：可扩展性是本项目的原始诉求；扩展点不依赖核心代码变更，
才能保证水平扩展不影响已稳定部分。

### IV. 幂等与确定性（Idempotency and Determinism）

任何同步或入库操作重跑 MUST NOT 产生重复数据、重复关联边或重复副作用。
条目标识 MUST 由输入内容确定性派生。同样的输入 MUST 产生同样的输出，
除显式标记为非确定的外部调用（LLM 蒸馏）外。

理由：故障后重跑是常态而非例外；不可重入的操作无法安全恢复。

### V. 写入边界与数据隐私（NON-NEGOTIABLE）

运行时代码 MUST 只写入 data/ 与 notes/ 两个目录根之下；任何其他位置的
写入均属违宪。临时产物 MUST 使用系统 tmp 目录。所有产品源目录
（~/.claude、~/.codex、~/.kimi-code、~/.trae-cn 等）MUST 保持只读，
去重状态以库内幂等 ID 为唯一依据，MUST NOT 向源目录回写任何标记。
密钥 MUST NOT 出现在代码、配置模板或任何提交物中，只从环境读取。
data/、notes/、.env MUST 永久处于 gitignore。

验证手段：git status 出现在上述三处之外的新文件即违宪；
对源目录，采集前后其 mtime 与内容指纹 MUST 不变。

理由：隐私与数据边界不存在"本特性例外"；未来任何功能
（web 界面、导出、MCP）都必须在这堵墙内进行。

### VI. 技术克制与可逆（Restraint and Reversibility）

MUST NOT 为尚未到来的需求引入技术或组件。任何引入的组件 MUST 满足
"移除 = 删除目录 / 卸载依赖"级别的可逆。依赖 MUST 安装在项目内
（.venv），MUST NOT 做全局安装或修改全局状态（brew 除外）。
选型 MUST 倾向开源、免费、可逆。

理由：本项目为单用户本机系统；预支的复杂度没有消费者，
只有维护成本。

### VII. 学习优先（Learning First）

本项目同时是学习项目。核心逻辑模块（嵌入客户端、相似度搜索与去重、
幂等标识、蒸馏提示词）MUST 由用户手写，AI MUST NOT 代写，
只做 review 与答疑。交付 MUST 按端到端可运行的纵切薄片推进，
MUST NOT 按模块横切批量产出。脚手架类文件（构建、路由样板、
配置骨架）可由 AI 生成。

理由：学习收益最大化是显性需求（PRD §12）；分工边界写在
宪法里，防止任何未来的流程参与者（包括 AI）越界代写。

## Architecture & Technology Constraints

- 语言与运行时：Python 3.13；单包平铺布局（src/），插件为目录约定
  而非独立分发的包。
- 多包架构（uv workspace 等）MUST NOT 引入，直到出现
  "独立演进、独立版本化的多个消费者"这一判据成立之时。
- 常驻外部服务仅限本地 Docker 中的向量数据库（Qdrant），
  数据卷挂在宿主机 data/ 下；应用本身跑宿主机 .venv，不做守护进程。
- 时区统一 Asia/Shanghai；内容以中文为主，
  代码标识符与注释用英文。
- 嵌入与蒸馏使用火山方舟（Ark）OpenAI 兼容接口，
  确切模型 ID 以真实调用验证为准（PRD 假设）。

## Development Workflow

- prd-workflow 的阶段门 MUST NOT 跳过；门未通过 MUST 回退到
  最早受影响的阶段，MUST NOT 并行另起主流程。
- 质询类门（grill-me / grill-with-docs）MUST 由用户手动触发，
  模型 MUST NOT 自审自过。程序细节（各阶段输入/产物/退出条件）
  以 prd-workflow skill 为准，本宪法不复制。
- 实施前 MUST 先给方案并经用户确认；只改当前任务涉及的代码。
- Git：不主动 commit / push，由用户决定时机；
  禁止 force push、改写已推送历史、reset --hard 丢弃未提交改动。

## Governance

- 宪法管原则，PRD 管范围：两者冲突时，原则问题以本宪法为准，
  范围与验收问题以 PRD 为准；无法归类的冲突交用户裁决。
- 修订 MUST 经用户批准并按语义化版本递增：
  MAJOR = 原则删除或重定义；MINOR = 新增原则或实质扩展；
  PATCH = 措辞澄清。修订 MUST 附 Sync Impact Report。
- 所有审查环节（plan 阶段 Constitution Check、最终一致性审查）
  MUST 对照本文件逐条检查，而非凭记忆引用。

**Version**: 1.0.0 | **Ratified**: 2026-09-18 | **Last Amended**: 2026-09-18
