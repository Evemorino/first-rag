# Quickstart: first-rag

## 前置

- Python 3.13、Docker daemon 运行中
- 方舟 API Key（已订阅，控制台获取）

## 步骤

```bash
# 1. 依赖（项目内 .venv，宪法 VI）
python3.13 -m venv .venv && source .venv/bin/activate
pip install -e .            # 或 uv sync

# 2. 密钥
cp .env.example .env        # 先只填 ARK_API_KEY

# 3. 基础设施
make up                      # Qdrant :6333，数据落 ./data/qdrant/

# 4. M0 验证（此时才填模型 ID）
make embed-test              # 真调 Ark 验证 .env 里已填的 EMBED/CHAT_MODEL 可用 → 按返回维度建集合（脚本只读 .env，模型 ID 由你手工填）

# 5. 日常
make sync                    # 当日采集→蒸馏→入库；补历史 make sync D=2026-09-17
make ask Q="我最近学了什么"    # 提问；可选参数走 ARGS=，如 ARGS="--type error --since 7d"
make log m="一句话快记"        # 手动快记 → notes/inbox.md

# 6. 测试
make test                    # unit + integration（需 make up 先行）
```

## 验一个采集源是否真的通（AC-015 的方法）

AC-015 要回答两件事，**方法分开** —— 一件不用写盘，一件必须写盘。

### ① 这个源在某天有素材吗 —— `make probe`（只读，零落盘）

```sh
DAY=2026-09-25
make probe D=$DAY            # 全源：逐源素材条数
make probe D=$DAY S=qoder    # 只看一个源：合计只算 qoder
```

`make probe` 跑的就是 `collect.gather(..., persist=False)`：采集照跑、逐源计数照给，
`data/raw/` **一个字节都不写** —— 那份快照是 `make redistill` 的重放基线，v0.7.4 弄丢过
它，其中几天不可恢复（所以问一句"有没有素材"不该动它）。两条读法：

- **素材 0 条是成功**，不是故障：那就是这个源今天真的没有东西可采。
- `S=<源>` 的合计只算那一个源。git 提交与 `notes/` 手动快记（`scope` 关不掉这两类）
  会列在分隔线下面并标明"未计入合计" —— 它们当天确实被采到了，只是不回答这个问题。

要验的源在**现存快照**里有素材的：`claude_code` / `codex` / `kimi_code` / `trae` /
`trae_work_cn` / `qoder` / `qoder_cn`；在任何现存快照里都没有素材、必须做一次新采集的：
`zcode` / `workbuddy_ai` / `hermes`（`hermes` 的库为空，AC-015 对它不适用，见 AC-017）。

### ② 入库路径通不通 —— 必须真跑一次 sync（这一步写盘，先备份）

`make sync` 要往**真库** upsert 点，这部分**不可还原**（验的就是入库）。会写盘的只有
`data/raw/<day>.json`，按下面做就能还原：

```sh
DAY=2026-09-25
ls -l config/scope.json 2>/dev/null || echo "0) 当前无 scope.json（= 全源采集），收尾要删回这个状态"
BK=$(mktemp -d); rsync -a data/raw/ "$BK/raw/"      # 1) 备份基线（唯一退路）
test -n "$(ls -A "$BK/raw")" || echo "备份为空，停手"
make scope                                          # 2) 人显式操作：n → t <源> → s
make sync D=$DAY                                    # 3) 跑（记下汇总里的 skipped_by_source）
ls -l data/raw/$DAY.json                            # 4) 看现场：素材数 / per_source / status
rsync -a --delete "$BK/raw/" data/raw/              # 5) 还原（含删除本次新增的快照）
diff -r "$BK/raw" data/raw && echo "raw 已还原"     # 6) 核对，必须无差异
rm -f config/scope.json                             # 7) 仅当第 0 步显示原本没有它
rm -rf "$BK"
```

**怎么判「入库成功」**（AC-015 ②）：`data/raw/<day>.json` 的 `distill_run.per_source[<源>]`
给蒸馏侧的 `materials` / `kept`，`make sync` 的汇总给入库侧被新颖度拦掉的
`skipped_by_source`。两者合起来才能判因：

| 观测 | 判读 |
|---|---|
| `materials > 0`、`kept = 0` | 蒸馏侧就没了：看日志的 WARNING（未知类型 / 空文本）或熔断 |
| `kept > 0`、`skipped_by_source[源] = kept` | 被 FR-009 的新颖度判为重复 —— 不是故障 |
| `kept > 0`、两边都没提 | 入库真的坏了（这两处计数就是为此存在） |

**三条不要**：不要用 `ALLOW_SHRINK=1` 做验证（那是「我承认这次削减」的语义，不是在验证）；
不要在**多源日**上做单源跑还不备份（v0.7.4 事故就是这么发生的）；不要把「库内 0 点」直接
读成「入库坏了」（先看上面那张表）。

## 故障速查

- Qdrant 未启动 → sync/ask 报连接错误，`make up` 后重试（幂等保证安全）
- 某工具目录消失 → 正常情况，该来源 no-op，看日志确认
- 重跑任何 sync 均安全：幂等 ID 保证无重复（PRD FR-014）
