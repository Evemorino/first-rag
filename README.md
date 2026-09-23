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
- **提交之后还有一个提醒，不算门禁**（`scripts/mutation_reminder.py`）：
  改动若落在变异测试覆盖的文件里，会提示"该重跑 `make mutation` 了"。
  变异跑一遍半小时，进不了提交门禁，只能靠它 —— 本项目为此漏过两次。
  它必须设 `verbose: true`：pre-commit 对**成功**的钩子默认不打印输出，
  不开的话提醒是看不见的（装完第一次提交就发现了：只显示一行 Passed）。
- **分层与规模只管 `src/`**；`example/` 是示例代码，豁免质量钩子。
- **钩子自己也会坏，而且坏得很安静**。改了 `.pre-commit-config.yaml` 或
  `scripts/` 下任何一个检查脚本之后，跑 `make gate-selftest`（约 10 秒）：
  它给每个钩子植入一个已知违规，断言"必须红"，再拿一个干净仓库断言"必须绿"。

改 README 这类非 Python 文件不会触发测试；想临时跳过用 `git commit --no-verify`。

### 门禁自检

```sh
make gate-selftest                  # 26 个用例：13 个"该红" + 13 个"该绿"
uv run python scripts/gate_selftest.py --why    # 打印每个用例为什么这样设计
```

只测"该红时不红"是不够的 —— 一个永远报错的钩子也能通过。所以每个钩子都配了
一个干净仓库的对照组。自检本身也可以被验证：把 `scripts/lint_layers.py` 的
`main()` 开头塞一行 `return 0`，自检必须报"期望红 实际绿"并退出 1。

自检过程顺带挖出了两条从来没写在任何地方的事实：

- `check-merge-conflict` **只在 merge/rebase 中干活**（靠 `.git/MERGE_MSG` 判断），
  平时提交它永远绿 —— 这不是 bug，但"它平时不保护你"值得知道。
- pytest 钩子会往工作区写 `coverage.xml`，而 pre-commit 一旦发现"钩子改了被
  跟踪的文件"就判失败。真实仓库一直是绿的，全靠 `coverage.xml` 在 `.gitignore`
  里；把这行删掉，每次提交都会红。

### Makefile 那一跳也要有测试

`tests/unit/test_makefile_forwarding.py` 核对"README 里写着可用的参数，
Makefile 目标真的转发给了 CLI"。加它是因为真踩过：`make log m="…" t=error`
的配方只写了 `m="$(m)"`，`t` 被 make 静默吞掉 —— 快记照样写进 inbox，只是
类型退回 reflection（collect 读不到 marker 时的默认值）。README 一直写着
`t=error` 能用，`test_log.py` 也一直绿，因为它测的是
`log.main(["log", "m=…", "t=…"])`，**直接跳过了 make 这一层**。

同一个坑（"两层之间那一跳没人管"）本仓库踩过不止一次：变异提醒漏过两次、
`also_copy` 漏文件两次。判据是：**只要有一层是"转发/搬运"性质的，就得有一个
断言盯着它把东西搬到位** —— 这类层自己不会算错，只会漏，而漏的时候下游
拿到的是"少了一个参数"的合法输入，一路绿到底。

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
- **变异测试**默认只打"改坏了会**静默**出问题"的链路：宪法 VII 点名的手写核心
  （ids / similarity / ark_client / distill_prompt）+ 幂等入库与编排
  （ingest / sync）+ 宪法 V 的脱敏边界（sanitize）+ 三条主流程
  （ask / collect / distill），见 `pyproject.toml` 的 `only_mutate`。
  当前基线：1553 个变异体
  被杀死、28 个存活、14 个无测试覆盖，**变异分数 98.2%**。

  往 `only_mutate` 里加模块时要注意：mutmut 只跑已有 `.meta` 里待检查的变异体，
  **新加的文件不会自动 collect**（它连 `collect` 子命令都没有），加完必须
  `mv mutants /tmp/…` 完整重建一遍才会真正生效 —— 否则就是"配置写了但没跑"，
  又是一个只有数字、没有实质的信号。

  存活的 28 个逐条看过，分两类，**没有一类是"还没来得及查"**：

  - **文案与输出格式**（21 个）：prompt 话术（5）、usage / 报错提示的措辞
    （8）、`logger.exception` 的日志串（4）、引用行的 `[:80]` 截断（2）、
    `json.dumps(indent=)`（1）、`print("\n引用：")`（1）。要杀掉它们只能把
    整段文案抄进断言 —— 维护成本远高于收益。
  - **已证等价**（7 个，改了行为不变，**任何**测试都杀不死）：两处
    `"utf-8"` → `"UTF-8"`（codec 名大小写不敏感）、`filter_novel` 的
    `+ 1` → `+ 2`（第 N+1 个槽位永远是批外点，见 `similarity.py` 的注释）、
    `ingest.upsert` 传给 `filter_novel` 的 `collection_name`（写成 `None`
    和整个删掉是两个变体；`filter_novel` 里是
    `collection_name or config.COLLECTION`）、`sync.main` 的 `ensure_ascii`
    （summary 目前全是 ASCII）、`distill._direct_type` 里 `note_type` 的
    默认值（大写 `REFLECTION` 不在 schema 的 type 名里，一样走兜底，返回
    值不变）。**它们不进 `do_not_mutate_patterns`**：前提一旦变了（比如
    summary 里出现中文），变异体会自己变回真信号 —— 写死排除规则反而会
    永久性地盖住它。

  `pyproject.toml` 里的 `do_not_mutate_patterns` 只留了 4 条，排的是
  **纯文案行**：给 LLM 看的素材示例、整行就是一个字符串字面量、
  `logger.info/warning/error/debug(` 与 `logging.basicConfig(`。
  这里踩过一个坑值得记下来：**排除的单位是"行"，不是"变异"** ——
  命中后该行的**所有**变异体都不再生成，所以每条 pattern 都得先回答
  "这行除了我想排除的，还有没有别的逻辑"。曾经用 `"utf-8"` 这种"包含"式
  pattern 排除编码名，结果连坐了同行的 `open(inbox, "a", …)`（`"a"` →
  `"w"` 会让快记被覆盖）和 `.splitlines()`；同类还有 `[\["]` 这种宽松写法，
  连坐了 `"citations": [asdict(c) …]` 这类真实 payload 行。这些行从此再也
  不被变异检验，而**报告上看不出任何异常**。判据：宁可留一个等价变异体
  在报告里当"已知存活"，也不要关掉一整行。

  **别为了分数好看补断言**：把 `search_limit` 等于几、`collection_name`
  等于什么写进断言，杀掉的是变异体，不是风险 —— 那种断言测的是实现，
  换个等价写法就全废。反过来，补断言要挑"改坏了会静默出错"的：比如
  追问时漏掉模型上一次的原始输出，追问照样发出去、断言照样绿，只是
  改不对的条目被悄悄丢掉 —— 这种才值得补（`tests/unit/test_distill.py`
  的 `test_unknown_type_retries_once_with_correction`）。

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

**批量结果本身也带噪声**：本轮跑批把 `distill._direct_type` 里一个
`"reflection"` → `"REFLECTION"` 的**等价**变异体报成了 killed，单跑
（`mutmut run <key>`，几秒钟）才发现它其实 survived。同一份代码连跑，
结论能差十几个。所以那个百分数只能当趋势看；要下结论（尤其是
"这条到底有没有测试盯着"）必须单跑那一个 key。分数在一两个百分点内
抖动时，先怀疑噪声，别急着找原因。

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
