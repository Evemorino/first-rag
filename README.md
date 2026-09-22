# first-rag

个人学习记忆系统：自动采集四个 AI 编码工具的当日会话 + git 提交 + 手动快记，
LLM 蒸馏为结构化学习条目，幂等存入本地 Qdrant，支持带过滤的语义检索与
引用式问答。

```
claude-code / codex / kimi-code / trae ─┐
git 提交（config/repos.txt）            ├─→ collect → data/raw/快照 → distill（脱敏/熔断）→ ingest（幂等）→ Qdrant
手动快记（notes/inbox.md，轻路径）      ─┘                                                    ↓
                                                                      make ask（过滤检索 + 关联扩展 + 引用回答）
```

## 安装

要求：Python 3.13、Docker（只跑 Qdrant）、方舟 Ark API Key。

```sh
uv sync                                # 项目内 .venv 装依赖
cp .env.example .env                   # 填 ARK_API_KEY / EMBED_MODEL / CHAT_MODEL
make up                                # 启动 Qdrant（localhost:6333）
make embed-test                        # 真调验证嵌入模型与维度并建集合（首次必跑）
```

## 日常使用

所有 make 目标底层都是 `uv run python -m …`；Windows 上没装 make 时直接用
等价命令（见 [AGENTS.md](AGENTS.md)）。

```sh
make sync                              # 晚上跑一次：当日素材 → 蒸馏 → 入库（幂等可重跑）
make log m="踩了个坑：..." t=error     # 随手快记（未标类型默认 reflection）
make ask Q="我在 qdrant 上踩过什么坑" --type error --since 7d
make scope                             # 勾选采集哪些工具/项目（写 config/scope.json）
make redistill D=2026-09-18            # 改完蒸馏标准后对照 diff；加 APPLY=1 整组替换
make serve                             # 按需 API：/health /log /sync /ask
```

## 质量指标

**装一次钩子，之后每次 `git commit` 自动跑**（详见 `.pre-commit-config.yaml`）：

```sh
make hooks        # = uv run pre-commit install
```

13 个钩子，按"从便宜到贵"排：大文件/冲突/JSON/YAML/AST → 行尾空白 →
**密钥扫描** → 分层依赖 → 规模 → pytest → CRAP → 孤儿模块。

- **pytest 挂了会直接停下**（`fail_fast`）—— 否则 CRAP 会拿一份残缺的
  coverage.xml 判门禁，凭空报出一堆不存在的 crappy 函数。CRAP 与孤儿检查
  都吃这份数据，所以必须排在 pytest 之后。
- **分层与规模只管 `src/`**；`example/` 是示例代码，豁免质量钩子。

改 README 这类非 Python 文件不会触发测试；想临时跳过用 `git commit --no-verify`。

### 分层与规模

```sh
make layers        # 分层依赖：src/ 的 import 方向对不对
make layers-list   # 打印各层允许 import 什么
make size          # 规模：src/ 单文件 ≤300 SLOC、单函数 ≤80 行
make size-top      # 摸底：看最长的文件与函数
```

**分层**（`scripts/lint_layers.py`，依赖只能向下）：插件不许互相依赖、
也不许反向依赖编排层；`api/` 只碰编排层；核心层（`similarity` / `ids` /
`ark_client` / `distill_prompt`）不许依赖编排层；`config` 不依赖任何 src。
写下时零违规 —— 加它是防半年后有人图省事破坏依赖方向。例外只有一条且写明
理由：`distill_prompt → src.collect`（只为取 `DayRaw`）。

**规模**（`scripts/size_guard.py`）用 SLOC 而非物理行数，免得罚注释写得好的
文件；只管 `src/`，因为测试函数天然长。注意它只是底线：
`codex.parse` 66 行复杂度 21（CRAP 21.1，全项目最高）它拦不住 ——
真正的风险判断交给 CRAP。

覆盖率只能说明"这行跑过没有"，说明不了"改坏了会不会被发现"。所以再加两层：

```sh
make cov           # 行覆盖率 → coverage.xml（下面三项的输入）
make crap          # CRAP = 复杂度² × (1-覆盖)³ + 复杂度，≥30 视为 crappy
make orphans       # 孤儿模块：一行都没被测试跑过的（CRAP 的盲区）
make mutation-selfcheck  # 先跑这个：确认"改坏源码 → 测试会红"这条链路真的通
make mutation      # 变异测试：改坏源码，看测试能不能抓到（内部会先跑自检）
```

- **CRAP** 把复杂度和覆盖率乘在一起：复杂度 23、覆盖 74% 的函数 CRAP 是 32.8，
  一眼看出该拆还是该补测试。当前基线：函数内语句覆盖 88.5%（口径只算函数体内
  语句，与 pytest 报的全量行覆盖 90% 不是一回事），142 个函数，均值 4.6，
  **0 个 crappy**（`config.py:_validate` 按 section 拆成 6 个小函数；
  `collect._cap` / `redistill._fetch_day_entries` / `claude_code` 错误提取
  补测试到 100% 覆盖）。
- **孤儿模块**（`scripts/orphan_check.py`）补的是 CRAP 的盲区：CRAP 问的是
  "复杂的代码测够了吗"，问不了"这个模块有人碰过吗"。一个只有简单函数
  （复杂度 1）的模块，哪怕零测试，CRAP 也只有 1×(1-0)³+1 = **2** —— 离 30
  的阈值远得很。所以"新加了模块却一个测试都没写"能悄无声息地溜过门禁，
  只有这里会喊。豁免 `plugins/_template/`：那是给新插件照抄的骨架，两个函数
  都直接 `raise NotImplementedError`，没有测试才是对的。
- **变异测试**默认只打核心链路（ids / similarity / ark_client / distill_prompt /
  ingest / sync，外加宪法 V 的脱敏边界 sanitize，见 `pyproject.toml` 的
  `only_mutate`）。当前基线：408 个变异体
  被杀死、70 个存活、5 个无测试覆盖，**变异分数 85.4%**。

  往 `only_mutate` 里加模块时要注意：mutmut 只跑已有 `.meta` 里待检查的变异体，
  **新加的文件不会自动 collect**（它连 `collect` 子命令都没有），加完必须
  `mv mutants /tmp/…` 完整重建一遍才会真正生效 —— 否则就是"配置写了但没跑"，
  又是一个只有数字、没有实质的信号。

  存活的 71 个里，约 34 个在 `sync.__try_lock` —— 那是 Windows 的 `msvcrt`
  分支，在 macOS 上根本执行不到；约 31 个是 prompt 里的字段名与示例文案
  （`"source"` 改成 `"SOURCE"` 这种）。这两类都不该靠测试去杀：前者得造假
  平台环境，后者得把整段 prompt 抄进断言，维护成本远高于收益。
  所以 `pyproject.toml` 里用 `do_not_mutate_patterns` 排除了纯文案行与日志行
  ——变异测试该验证逻辑，不是验证文案。核心的 `similarity.filter_novel` 与
  `ingest.upsert` 只剩 2 个存活，都是等价变异（改了行为不变）。

  这个数字写完就会开始腐烂，所以它不是"记一次就完事"：`make mutation` 跑完
  时会自动拿 `mutants/` 里的真实结果和上面这句话比对，不一致就报错
  （`scripts/baseline_check.py`）。只想查不想重跑时用 `make baseline`，它还会
  列出比上次跑批还新的 `src/`、`tests/` 文件 —— 那才是基线真正开始说谎的时刻
  （踩过一次：把 `distill.py` 从 417 行拆到 241 行，分数没变，但没人能事先知道）。

CRAP 与孤儿检查都依赖 coverage 数据，所以顺序是 `cov → crap / orphans / mutation`。

### 为什么还需要 `mutation-selfcheck`

变异分数是**代理指标**：它测的是"测试看起来有多严"，不是"测试真的严"。
而它失效的时候**不报错、不崩溃，只安静地给个数字**。本项目踩过一次：
`tests/mutmut_compat.py` 的转发补丁缺失导致变异体从来没被真正套用，mutmut
照样报了 28.8% —— 修好之后真实分数是 75.8%。那 47 个百分点是假的，
但工具从头到尾一声没吭。

所以 `make mutation-selfcheck` 做一件事：**往源码里塞一个已知必死的改动，
看测试抓不抓得住**。抓不住就退出码 1，告诉你"现在这个分数不可信"。
内置 3 个 canary（`scripts/mutation_selfcheck.py` 顶部可加）：

- `ids.py` 的 `[:16]` → `[:15]`（16 位截断是 data-model 硬契约）
- `ids.py` 的 `NAMESPACE_URL` → `NAMESPACE_DNS`（换掉整个 ID 空间）
- `sync.py` Windows 分支里的 `os.lseek(fd, ...)` 改错参数，**期望抓不住**
  （macOS 走 fcntl 分支，这段代码根本执行不到）。若它"被抓住"就是环境在假杀

前两个选用**独立重算期望值**的契约型断言，不是把实现抄进测试的那种；
第三个是反向对照 —— 只会报 PASS 的检查，自己就是下一个假信号。

**一条运维经验**：mutmut 默认并行（`max_children = os.cpu_count()`）。如果在容器
或沙箱里因为临时目录问题需要加 `--basetemp`，**必须同时加 `--max-children 1`**
—— 并行时多个 pytest 会同时 rmtree 同一个 basetemp、互相破坏，测试莫名变红，
分数会从 84.4% 虚高到假的 96.9%（露出马脚的地方：macOS 上根本执行不到的
Windows 分支会被"全部杀死"）。看到不可达分支被全杀，先怀疑环境，别高兴。

**自检自己也会制造假信号**：它要往源码里写东西，被 kill 的时候 `finally`
不执行，源码就留在改坏的状态（本项目踩过：超时杀掉自检后 `src/ids.py` 留成
`NAMESPACE_DNS`，紧接着的全量 pytest 因此红了 1 个 —— 那个红跟测试质量毫无关系）。
所以它装了 signal handler 兜底还原，并在开跑前预检"每个 canary 的原文还在不在"，
不在就直接退出码 2。检查器也需要被检查，包括检查它自己有没有污染现场。

**在受限容器里跑不动测试时**：pytest 的 assertion rewriting 会把重写后的源码
写进 `__pycache__`，某些环境的安全扫描会拦这一步（表现为收集阶段就
`PermissionError`，卡两分钟后超时）。加 `--assert=plain` 即可 —— 测试照跑照判，
只是断言失败时少一点上下文。别为了迁就环境去改项目配置。

同理，别的指标也别当真理看：覆盖率 100% 的测试可以一条有意义的断言都没有
（本项目 `test_ids.py` 早期就是 `pytest.raises(match=...)` 只比对子串）。

## 隐私与数据流向

- **源目录只读**：采集器对 `~/.claude`、`~/.codex`、`~/.kimi-code`、`~/.trae-cn`
  零写入、零标记；去重状态只依赖库内幂等 ID。
- **写入边界**：运行时产物只写 `data/`（向量库与 raw 快照）和 `notes/`（快记），
  其余位置零写入；测试 fixture 全走系统临时目录。
- **密钥**：只从 `.env`（已 gitignore）读取，不出现在代码与提交物中；素材在送往
  蒸馏服务前先做正则脱敏（密钥/令牌/密码模式替换为 `[REDACTED]`）。
- **蒸馏数据流向**：会话素材会发送到火山方舟（Ark）做蒸馏与嵌入。你本就通过
  方舟代理使用这些编码工具，数据流向与现有使用方式一致，无新增暴露面（PRD §9）。
- **备份/迁移**：备份 = 复制 `data/`；换机器 = 复制 `data/` + `config/` + `notes/`。

## 更多

- 需求与验收：[PRD.md](PRD.md)（唯一事实来源）
- 实施原则：[.specify/memory/constitution.md](.specify/memory/constitution.md)
- 任务与进度：[specs/001-learning-memory-rag/tasks.md](specs/001-learning-memory-rag/tasks.md)
- AI 协作速查：[AGENTS.md](AGENTS.md)
