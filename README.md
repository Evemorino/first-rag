# first-rag

个人学习记忆系统：自动采集 11 个 AI 编码工具的当日会话 + git 提交 + 手动快记，
LLM 蒸馏为结构化学习条目，幂等存入本地 Qdrant，支持带过滤的语义检索与
引用式问答。

```
11 个采集源（见下表）───────────────────┐
git 提交（config/repos.txt）            ├─→ collect → data/raw/快照 → distill（脱敏/熔断）→ ingest（幂等）→ Qdrant
手动快记（notes/inbox.md，轻路径）      ─┘                                                    ↓
                                                                      make ask（过滤检索 + 关联扩展 + 引用回答）
```

## 支持的数据源

| source | 产品目录 | 读法 |
|---|---|---|
| `claude_code` | `~/.claude/projects` | JSONL |
| `codex` | `~/.codex/sessions` | JSONL |
| `kimi_code` | `~/.kimi-code/sessions` | JSONL |
| `qoder` | `~/.qoder/projects` | JSONL |
| `qoder_cn` | `~/.qoder-cn/projects` | JSONL |
| `workbuddy_ai` | `~/.workbuddy-ai/projects` | JSONL |
| `trae` | `~/.trae/memory` | JSONL（pre-summarized）† |
| `trae_work_cn` | `~/.trae-cn/memory` | JSONL（pre-summarized）† |
| `opencode` | `~/.local/share/opencode/opencode.db` | SQLite 只读 |
| `zcode` | `~/.zcode/cli/db/db.sqlite` | SQLite 只读 |
| `hermes` | `~/.hermes/state.db` | SQLite 只读 |

† **pre-summarized 路径**：Trae 系自己已经总结过会话，条目由 `{intent, actions,
outcome, learned}` 轻量转换直并入库，**不走 LLM 蒸馏**（重复蒸馏既费 token 又会
再幻觉）。类型映射规则见 `config/schema.json` 的 `trae_type_map`。

**`~/.trae-cn` 与 `~/.trae` 是一对容易搞反的名字**：v0.7.1 把原 `trae` 插件按
名实重新命名为 `trae_work_cn`（它读的一直是 `~/.trae-cn`），`trae` 之名转给
`~/.trae`。改名会改变条目 ID（ID = `uuid5(source|date|content_hash)`，source 是
身份字段），存量 9 条由 `scripts/migrate_trae_source.py` 一次性迁移——**该脚本的
判据是 ref 路径而非 source 名**，且重跑会默认拒跑（退出码 2），详见文件头。

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
make sync D=2026-09-18 ALLOW_SHRINK=1  # 窄 scope 重跑同一天：明示允许用更小的快照覆盖
make log m="踩了个坑：..." t=error     # 随手快记（未标类型默认 reflection）
make ask Q="我在 qdrant 上踩过什么坑" ARGS="--type error --since 7d"
make ask Q="..." ARGS=--stream         # 流式：边生成边打（首字 ~0.7s，不必等整段）
make scope                             # 勾选采集哪些工具/项目（写 config/scope.json）
make redistill D=2026-09-18            # 改完蒸馏标准后对照 diff；加 APPLY=1 整组替换
make serve                             # 按需 API：/health /log /sync /sync/status /ask /ask/stream
```

> `ask` 的额外参数**必须经 `ARGS=` 传**（`ARGS="--type error --since 7d"`）：配方只转发
> `$(Q)` 和 `$(ARGS)`，直接写成 `make ask Q=… --type error` 会被 make 当成未知选项、
> 以退出码 2 停下。想绕过 make 就直接调 `uv run python -m src.ask Q="…" --type error`（见 AGENTS.md）。

## 质量指标

**装一次钩子，之后每次 `git commit` 自动跑**（详见 `.pre-commit-config.yaml`）：

```sh
make hooks        # = uv run pre-commit install
```

15 个钩子，按"从便宜到贵"排：大文件/冲突/JSON/YAML/AST → 行尾空白/文件末尾换行
（`end-of-file-fixer`）→
**私钥检测**（`detect-private-key`，只认 PEM）→ **令牌扫描**（API key/token 形态，
`src/secret_patterns.py` 那一套）→ 位置与分层 → 规模 → **写入边界** → pytest →
CRAP → 孤儿模块。

- **pytest 挂了会直接停下**（`fail_fast`）—— 否则 CRAP 会拿一份残缺的
  coverage.xml 判门禁，凭空报出一堆不存在的 crappy 函数。CRAP 与孤儿检查
  都吃这份数据，所以必须排在 pytest 之后。
- **提交之后还有一个提醒，不算门禁**（`scripts/mutation_reminder.py`）：
  改动若落在变异测试覆盖的文件里，会提示"该重跑 `make mutation` 了"。
  变异跑一遍半小时，进不了提交门禁，只能靠它 —— 本项目为此漏过两次。
  它必须设 `verbose: true`：pre-commit 对**成功**的钩子默认不打印输出，
  不开的话提醒是看不见的（装完第一次提交就发现了：只显示一行 Passed）。
- **分层与规模只管 `src/`**；`example/` 是示例代码，豁免质量钩子。
- **写入边界守的是不可恢复的那条**（`scripts/write_boundary_check.py`）：产品源目录
  里是真实的会话记录，写坏了补不回来。两级：写到由 `Path.home()` 派生的路径 = 硬违规，
  登记也不许豁免；`src/` 里每个写调用还必须先登记过（键是 `文件:函数`，不放行号 ——
  行号会随着上面加一行注释漂掉，登记表就会变成天天要改的摆设，那就没人改了）。
  登记意味着"停下来想一想这是不是第三种写入根"。全库目前只有一个这样的例外：
  `config/scope.json`，它已在宪法 V 里被写成**封闭枚举**的一条（v2.1.0：只此路径、
  只由人显式调用的 `make scope` 触发、全库只此一处），登记表就是它的可执行版本 ——
  要加第二处得先修宪，不许只往表里添一行。这条例外还带**调用方**约束：登记项上的
  `only_from` 写明只许 `src/scope.py:main` 调它 —— 光登记写入点挡不住别的模块
  import 这个函数去写 config/，那种绕过连写入点的名字都不变。
  `make boundary-list` 看全表。
- **同一套门禁在 CI 上再跑一遍**（`.github/workflows/ci.yml`）。pre-commit 挡不住
  `--no-verify`，也挡不住"换了台机器/另一个 agent 会话没装 hooks"，而本项目经常并行
  开好几个会话。CI 不新增任何判断标准，只跑那 15 个钩子 + `make gate-selftest` ——
  后者才是这个文件存在的理由：门禁自己坏了的时候（脚本改错、`files:` 过滤器写宽），
  本地和 CI 都会一路绿着放行，只有"植入违规看它红不红"能发现。
- **钩子自己也会坏，而且坏得很安静**。改了 `.pre-commit-config.yaml` 或
  `scripts/` 下任何一个检查脚本之后，跑 `make gate-selftest`（约 10 秒）：
  它给每个钩子植入一个已知违规，断言"必须红"，再拿一个干净仓库断言"必须绿"。

改 README 这类非 Python 文件不会触发测试；想临时跳过用 `git commit --no-verify`。

### 门禁自检

```sh
make gate-selftest                  # 33 个用例：18 个"该红" + 15 个"该绿"
uv run python scripts/gate_selftest.py --why    # 打印每个用例为什么这样设计
```

只测"该红时不红"是不够的 —— 一个永远报错的钩子也能通过。所以每个钩子都配了
一个干净仓库的对照组。`lint-layers` 有两条"该红"：一条测跨层 import，一条测
未登记的新目录 —— 同一个钩子上两条同期望的用例，临时仓库目录名要带上用例标题
的短哈希，否则第二条会把第一条覆盖掉。

自检本身也可以被验证，而且两个方向都要验：把 `scripts/lint_layers.py` 的
`main()` 开头塞一行 `return 0`，自检必须报"期望红 实际绿"并退出 1；反过来把
`layer_of()` 的兜底从 `return None` 改回 `return "编排层"`（旧的 fail-open
写法），**只有**"新目录没登记就免检"那一条该红，另两条必须还是绿的 —— 只红
一条，才说明这条用例钉住的是它自己那个行为，而不是"反正门禁坏了"。

canary 自己也会骗你，而且骗法很隐蔽：新加的"跨插件规则不依赖名单"那条用例，
第一版夹具写的是 `zed` import `trae` —— 而 `trae` 本来就在被删掉的那份名单里，
所以它在"退回手工名单"的 canary 下**照样绿**。用名单里的名字测"不依赖名单"，
等于什么都没测。判据是：夹具要落在**旧实现会放行、新实现必须拦住**的那一侧，
否则这条用例只是把现状复述了一遍。

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
make layers        # 位置与分层：src/ 的目录登记 + import 方向
make layers-list   # 打印已登记的位置（含理由、每层文件数）与各层允许 import 什么
make size          # 规模：src/ 单文件 ≤300 SLOC、单函数 ≤80 行
make size-top      # 摸底：看最长的文件与函数
```

**分层**（`scripts/lint_layers.py`，依赖只能向下）：插件不许互相依赖、
也不许反向依赖编排层；`api/` 只碰编排层；核心层（`similarity` / `ids` /
`ark_client` / `distill_prompt`）不许依赖编排层；`config` 不依赖任何 src。
写下时零违规 —— 加它是防半年后有人图省事破坏依赖方向。例外只有一条且写明
理由：`distill_prompt → src.collect`（只为取 `DayRaw`）。

**位置登记**（同一个脚本）：`src/` 下每个装着 `.py` 的目录都必须在
`DIRECTORIES` 里登记过属于哪一层 —— **没登记 = 报错，不给默认值**。

兜底给"拒绝"而不是"放行"，是这里唯一重要的设计。改之前 `layer_of()` 的最后
一行是 `return "编排层"`，而编排层的 `ALLOWED` 是 `None`（不限制），于是新建
一个目录就等于**默认拿到最大权限**：

- `src/retrieval/rerank.py` —— 旧行为：编排层 → 不限制；现在：报错，目录没登记
- `src/api2/handler.py` —— 旧行为：编排层 → 不限制；现在：报错，目录没登记
- `src/sync.py` —— 已登记，行为不变（平铺在 `src/` 根 = 编排层）
- `src/plugins/新插件/` —— 插件层，不用登记：插件是开放式扩展点，"它属于哪一层"
  在这里已经有答案了，真正要守的是"插件之间不许互相依赖"

门禁对"已经存在、已经被人 review 过的旧结构"严格、对"还没人想过的新结构"放行，
而后者恰恰是唯一需要门禁的时刻 —— 这是典型的 fail-open。行业里做得好的方案
兜底一律是拒绝：Go 的 `internal/`（目录名是编译期约束）、Bazel `visibility`
（默认 private，官方最佳实践原话是"避免把 `default_visibility` 设为 public……
随着代码库增长，无意中创建公共目标的风险会上升"）、Nx 的 project tag（官方文档
一句"Projects without any tags cannot depend on any other projects"）、tach
（允许放行，但必须在 `tach.toml` 里显式写 `unchecked: true`）。共同点不是"用哪个
工具"，而是**规则覆盖全体、默认拒绝、例外显式写出来**。

登记单位是**目录**而不是文件："新增一个文件"是给已有结构添砖，"新增一个目录"
是在引入一个新的架构单元 —— 只有后者值得先停下来想一步。核心层与 `config` 是
按文件登记的，因为层的边界穿过了 `src/` 这个目录本身。

"插件不许互相依赖"这条也顺手改掉了手工名单：原来靠一份 `PLUGIN_SUBPACKAGES`
枚举，新插件忘了加进去规则就静默失效；现在判据是"这个文件自己属于哪个插件"，
覆盖全体、不需要维护。同样是枚举"已知的"和覆盖"全体的"的区别。

`EXCEPTIONS` 里的例外也会被反向核对：文件还在、但已经不再 import 那个模块时
直接报错 —— 失效的例外会误导后面读的人，以为这里还有约束。

这些不变量由 `tests/unit/test_lint_layers.py` 钉住（兜底必须是 `None`、已登记
路径不受影响、跨插件规则不依赖名单、例外不能是死的、退出码）。它比钩子快得多，
而且报错时**指名道姓说是哪个文件** —— 钩子的报错要等一次 commit 才看得到。

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
  一眼看出该拆还是该补测试。当前基线（2026-09-26，844 用例全绿）：函数内语句
  覆盖 90.7%（口径只算函数体内语句，与 pytest 报的全量行覆盖 92% 不是一回事），
  241 个函数，均值 4.9，最高 21.1，**0 个 crappy**（`config.py:_validate`
  按 section 拆成 6 个小函数；`collect._cap` / `redistill._fetch_day_entries` /
  `claude_code` 错误提取补测试到 100% 覆盖；2026-09-25 从 `ask.py` 拆出的
  `ask_expand.py` 进来时是 100% 覆盖）。
  **这串数字是手写的，没有门禁盯着**——`make crap` 只保证"没有 crappy 函数"，
  不会因为你改了代码而告诉你 README 过期了（变异基线有 `baseline_check.py`，
  CRAP 没有对应的东西）。数字对不上时以 `make crap` 的输出为准。
- **孤儿模块**（`scripts/orphan_check.py`）补的是 CRAP 的盲区：CRAP 问的是
  "复杂的代码测够了吗"，问不了"这个模块有人碰过吗"。一个只有简单函数
  （复杂度 1）的模块，哪怕零测试，CRAP 也只有 1×(1-0)³+1 = **2** —— 离 30
  的阈值远得很。所以"新加了模块却一个测试都没写"能悄无声息地溜过门禁，
  只有这里会喊。豁免 `plugins/_template/`：那是给新插件照抄的骨架，两个函数
  都直接 `raise NotImplementedError`，没有测试才是对的。
- **变异测试**默认只打"改坏了会**静默**出问题"的链路：宪法 VII 点名的手写核心
  （ids / similarity / ark_client / distill_prompt）+ 幂等入库与编排
  （ingest / sync）+ 宪法 V 的脱敏边界（sanitize）+ 三条主流程
  （ask / collect / distill）+ 蒸馏装箱（distill_batches，2026-09-24 随分批一起
  纳入；`secret_patterns` 不加 —— 它只有模块级正则常量、零个函数，生不出变异体），
  见 `pyproject.toml` 的 `only_mutate`。
  当前基线：1919 个变异体
  被杀死、246 个存活、14 个无测试覆盖，**变异分数 88.6%**。

  往 `only_mutate` 里加模块时要注意：mutmut 只跑已有 `.meta` 里待检查的变异体，
  **新加的文件不会自动 collect**（它连 `collect` 子命令都没有），加完必须
  `mv mutants /tmp/…` 完整重建一遍才会真正生效 —— 否则就是"配置写了但没跑"，
  又是一个只有数字、没有实质的信号。

  同族的**第二个**坑（2026-09-26 实测撞上）：**给一个既有测试加断言去覆盖一个新
  函数，映射不会重学。** mutmut 只在出现**新测试名**时才增量重采"函数 → 覆盖它的
  测试"，于是新函数的变异体跑的是空测试集 → pytest 退出码 5 → 被记成「no tests」
  （`status_by_exit_code` 里 5 和 33 都是这个桶）。症状是"无测试"桶突然变大，看起来
  像覆盖变差，其实是**没验证**。修法不必全量重建：`rm mutants/mutmut-stats.json`
  触发一次全量重采（约 5 秒），再点名重跑 `mutmut run <name>…`。
  **判据：改了既有测试就得清 stats —— 除非你同时也加了新测试名。**

  这条判据现在有门禁兜着：`make mutation` 收尾的 `baseline_check.py` 会挑出"整个
  函数的变异体全被判 no tests"的函数，跟它的 `KNOWN_NO_TESTS` 登记表比对 —— 不在
  表里就红，并把上面那两条处置路径直接打出来；`--update` 也拒绝在这种状态下写数字
  （映射没判完，那份分数等于没核对过）。登记表里只有两条，都是真的从未被执行过的
  工厂：`ask_expand._client` / `ingest._client`。
  **名字认不出来也红**：分组全空会让这条检查永远报"没问题"，而门禁最坏的死法是
  一直绿着。

  还有一个反过来咬人的坑：mutmut 是**原地**改源码跑批的，跑完源码还原了，
  `src/__pycache__` 里却可能留着按变异体字节码编译的 `.pyc`。Python 只比 mtime，
  于是下一次 `pytest` 会在**干净的源码上**执行变异体逻辑 —— 2026-09-24 就这么把
  一条本来绿着的装箱测试读成了 `[60,120,60]`（那正是 `used = 0` → `1` 那个变异体
  的行为）。`make mutation` 现在收尾自动清 `__pycache__`；手工改过 src/ 之后如果
  测试红得说不通，第一件事是清缓存再看，别急着相信自己读到的失败。

  **上面那份"存活 28 个逐条看过"的分类已经作废**，留在这里只当方法参考。
  2026-09-23 第一次按 `mv mutants /tmp/…` 全量重建，结果是 **1344 杀 / 238 活 /
  24 无覆盖 → 85.0%**。差这么多不是代码变差了，而是**旧数字从来没被完整判过**：
  mutmut 3.x 按函数哈希复用判定，之后每次 `make mutation` 都只重跑改过的那几个，
  于是 98% 这个数一路靠缓存维持 —— 和上面"新加文件不会 collect"是同一个坑，
  只是这次的方向是反的（缓存让分数**虚高**）。

  抽查过两条，确认新数字不是测量事故：

  - `collect.x_gather__mutmut_6`（`collected_at` 的 `tz=config.TZ` → `tz=None`）
    手工打进源码后 **409 个测试全绿** —— 真存活，`collected_at` 的时区确实没人断言。
    **2026-09-26 订正（编号已漂）**：此例当时成立，之后 `0eec544` 补了
    `test_gather_collected_at_is_shanghai_aware`（断言偏移量，不看"是否相等"—— 本机
    时区恰好也是 +08，相等判据测不到），该变异体现在**被杀**；它在新编号里是
    `collect.x_gather__mutmut_8`（`mutmut_6` 已换成"漏传 `day=`"）。见下面第四次跑批。
  - `sync.x__try_lock__mutmut_*` 共 14 条：那是 `except ImportError` 里的 Windows
    `msvcrt` 分支，macOS 上根本不可达。`make mutation-selfcheck` 里本来就记着
    "抓住它就是假杀"，所以这 14 条属预期。

  **这张表是本轮重判的，不再继承上一轮。** 判法换了：不信 mutmut 的人话报告，而是
  把每条存活变异体**手工打进真实源码**、跑一遍全量测试，看有没有任何测试响
  （oracle 是"真实改动会不会被抓"，与 mutmut 的 trampoline 机制无关）。212 条逐个跑完，
  耗时约 20 分钟。

  | 族 | 条数 | 本轮实测结论 |
  |---|---|---|
  | 程序读的字符串被改 | 41 | `"collected_at"` → `"COLLECTED_AT"`、`kind="commit"`、payload 键名这类。**不是文案**：改了行为就变，而测试从没断言过这些键。上一轮把其中大部分混进了"文案 84 条"，于是那一桶看着"不值得杀"，其实是有信号没人接 |
  | 关键字参数/默认值/计算值被动过 | 29 | 含 `check=True` 被删（git 失败不再抛错，stderr 会被当成提交正文）、`--until` 变 None（采到未来日期的提交）、`scope.get("tools", {})` → `None`（缺键的 scope.json 会让整次 sync 崩） |
  | 没抓住形状，未逐条判 | 62 | 诚实结论：**没有判**。要接就按族切 |
  | `ensure_ascii` | 22 | 当前送蒸馏的素材文本恰好没有需要转义的非 ASCII；真跑数据里有中文时会变，属"数据一变就是信号" |
  | 纯文案（人读的字符串） | 32 | usage、日志格式串、prompt 话术。杀掉只能把整段文案抄进断言，成本高于收益 —— 这一族才是上一轮"84 条"里真正的那部分 |
  | 装箱与截断的边界比较符 | 5 | **本轮补测**：`_cap` 的 4 条（`>`/`>=`、`remaining > 100`/`>=100`/`>101`、`used +=`→`used =`）已用 5 条新断言杀掉；第 5 条（`total <= max_chars` → `<`）判为**真等价** —— `total` 正好等于上限时两条路径产出同样的素材列表，唯一差别是列表被重建，而没有调用方观察列表身份 |
  | 数据文件名大小写 | 7 | `"repos.txt"` / `"scope.json"` / `"INBOX.MD"` 形态。其中两条见下面的机制差异 |
  | 快照排版与编码 | 7 | `encoding=` / `indent=` 被动过。其中 `"utf-8"` → `"UTF-8"` 那几条是**语言层面等价**（Python 编解码器名不敏感），任何测试都杀不死 |
  | 平台不可达 | 7 | `sync._try_lock` 的 Windows `msvcrt` 分支，macOS 走不到。selfcheck 里记着"抓住它就是假杀" |
  | 合计 | **212** | |

  **2026-09-26 第二次全量重建（v0.7.8，快照写保护收紧）**：合计 **239
  条存活**（被杀 1854 / 无测试 14 → **88.6%** —— 开头那行基线由
  `baseline_check.py` 维护，显示的是**最新一轮**，不是本轮的 1854）。
  分族：

  | 族 | 条数 |
  |---|---|
  | 没抓住形状（未判） | 89 |
  | 纯文案（人读的字符串） | 73 |
  | 关键字参数/默认值/计算值被动过 | 28 |
  | `ensure_ascii`（数据相关） | 22 |
  | 快照排版与编码 | 8 |
  | 平台不可达（Windows 分支） | 7 |
  | 数据文件名大小写 | 7 |
  | 程序读的字符串被改（键名/枚举值） | 3 |
  | 边界与比较符 | 2 |
  | 合计 | **239** |

  这一轮存活数从 214 涨到 239，**推大分母的是两条新的守卫错误信息**：
  `collect._existing_snapshot` 新增了两条 raise（"结构不对"与"条目不对"），
  里面的解释性散文产生约 24 条新存活，全部落进"纯文案"族。

  **同一轮里新增的解析逻辑，逻辑类存活为 0。** 逐条核对过：`save_snapshot` +
  `_existing_snapshot` 的 58 条存活中 13 条是逻辑类（改了代码记号而非字符串内容），
  **这 13 条全部落在本次未改动的 HEAD 行上** —— `path.parent.mkdir(exist_ok=)` ×3、
  `encoding="utf-8"` ×3、`json.dumps` 的三个参数默认值 ×6、`isinstance(doc, dict)` ×1。
  上一轮点名的那两个口子（结构不对当空的放行、条目坏掉读不出 source）已随之关掉。

  **教训（留给下一轮）**：在这个仓库里，往错误信息里多写一句解释，代价是一条存活
  变异体。分母对解释性文案是敏感的 —— 所以看分数时必须同时看族构成，否则"把话说清楚"
  会被读成绩效下滑。本项目选择不把文案抄进断言（见"纯文案"族的裁语），
  于是这个代价是**已知且接受**的，不是待修的缺陷。

  **2026-09-26 第三次跑批（v0.7.9，AC-015 计数落地）**：**被杀 1918 / 存活 246 /
  无测试 14 → 88.6%**（分母 2107 → 2178，多出的 71 条全落在本版改动过的
  distill / ingest / sync 三个模块：新函数 + 被改动的行）。首跑报的是
  1906 / 245 / 27，其中"无测试 23"是上面那个兄弟坑造成的假象 —— 清 stats 重采后
  13 条拿到真判决（12 杀、1 活），该桶回到 14（无测试 10 + 超时 4）。
  分族已用 `mutant_recheck.py --summary` 刷新：未判族 89 → 95、关键字/默认值族
  28 → 29，其余七族不变。
  **逐条判决（`--run`）没有重跑**：上面那 212 条只覆盖它自己那一轮，本轮多出的 7 条
  **只有族归属、没有判决**，别读成"已看过没问题"。

  **2026-09-26 第四次跑批（v0.7.11，`gather` 不落盘落地）**：**被杀 1919 / 存活 246 /
  无测试 14 → 88.6%**（分母 2178 → 2179）。多出来的那 1 条来自新增的 `persist` 参数
  默认值（`True` → `False`，被新用例杀掉）；本次改动里的 `if persist:` **没有变异体**
  —— mutmut 3.x 的算子集不给裸布尔条件生成 `if not x:`，这条分支在报告里是隐形的。
  补法是它自己的 oracle：手工把守卫**删掉**（改成无条件落盘）→ 3 条用例红；把条件
  **取反** → 6 条红 —— 两向都可观测，只是这不体现在分数上。存活数没变，分族计数也
  与上一轮逐项相同（`mutant_recheck.py --summary` 复算：未判 95 / 纯文案 73 /
  关键字 29 / `ensure_ascii` 22 / 排版编码 8 / 平台 7 / 文件名大小写 7 / 程序读的字符串 3 /
  边界 2）。

  **上面这些族计数由 `scripts/mutant_recheck.py --summary` 生成**（入库工具，不再是
  我一次性探针里的临时规则）：它读 `mutants/src/*.meta` 挑出存活变异体，从插桩副本里
  算出"这条到底把源码改成了什么"，再按形状归族。判"是不是程序读的字符串"只看
  **真的变了的那个字面量** —— 第一版扫整段 diff，把 `"content": (…)` 里没动的
  `content` 当成被改的键，于是 33 条纯文案被错归进键名族，41/32 这两个数当时是假的。
  族是按规则切的粗筛，用来看"该往哪儿补测试"，**不是逐条判决**；判决在下一段。

  逐条判决（`--run`，手工打进源码跑全量测试）本轮跑了 212 条：**208 条"手工也杀不掉"**
  （连真实改动都没任何测试响，这才是缺口池）、2 条手工可杀（机制差异，见下）、
  2 条我的应用器锚点不唯一（`continue`、`created_at=created_at,` 这类到处都有的行）
  —— 判不了是探针的缺陷，不是变异体的性质，所以记"未判"不记"没问题"。

  **两条"机制差异"必须单列**：`collect.x__git_materials__mutmut_4` 与
  `sync.x__load_scope__mutmut_4` 手工打进源码会被 `tests/unit/test_path_literals.py`
  抓到，mutmut 却判存活 —— 它给每个变异体生成整份函数副本走 trampoline 分发，原字面量
  一直留在文件里，所以"读源码文本断言字面量"的测试对它**结构性无效**。
  那条测试仍然值得留（它守的是人和 AI 真改错一个字母），但它在变异报告里杀不掉任何东西。
  **教训：说"这条变异体已被杀掉"之前，先说清用的是哪个 oracle。**

  本轮补测当场杀掉 8 条（`_cap` 4 + scope 缺键 4），所以**下一次全量重建前，上面的
  212 / 87.0% 已是过期数字**：按算术应为 204 存活、1422 被杀 → 约 87.4%。
  这只是算术，不是测量 —— 等重建跑出来再写。

  **第三桶"24 个无测试覆盖"其实是三种毛病**（本轮按 mutmut 自己的
  `status_by_exit_code` 拆开：无测试 10、段错误 10、超时 4）。只有那 10 条是覆盖盲区
  （`ask_expand._client` / `ingest._client` 两个工厂），另外 14 条是变异体把进程跑崩或跑超时 ——
  拿"补测试"去处置崩溃是错的。`scripts/baseline_check.py` 现在每次报数都把这个构成打出来，
  并按 `KNOWN_NO_TESTS` 核对"整函数全判 no tests"的那种（映射过期会长得像覆盖盲区，
  见上文同族的第二个坑）。

  第一次分诊时真缺口是 **17 条**，聚成三个主题，前两个各是一条系统性的盲区：

  1. **本地时区从来没被断言过**（7 条）：`datetime.now(tz=config.TZ)` → `tz=None`
     在 `sync.run`、`sync.cleanup_raw`、`collect.gather`、`collect._dated_note_files`、
     `distill.distill` 上全部存活。也就是说"归属日按 Asia/Shanghai 算"这条契约
     （PRD 的时区约束）一旦写错，测试不会响。
  2. **循环里"跳过坏数据"没有测试**（7 条）：`continue` → `break` 在
     `sync.cleanup_raw`、`collect.gather`、`collect._git_materials`、
     `collect._note_materials`、`distill._direct_entries`、`distill._dedupe_entries`
     上存活 —— 一个坏文件/坏条目会让整批后续数据被静默丢弃，而今天的测试全都只放
     一条坏数据，看不出 `break` 与 `continue` 的差别。**这条与本项目已有的
     "夹具要变多个元素"教训同源**。
  3. **零散真缺口**（3 条）：`collect._git_materials` 的 `if not repo or
     repo.startswith("#")` → `and`（注释行会被当成仓库路径）、
     `collect._repo_commits` 的 `ts=None`（提交条目会丢掉日期）、
     `distill_prompt._build_user_prompt` 与 `collect._iso` 的真值判断
     （`if x` → `if x or True`，等于把兜底分支变成永远走不到）。

  还有一条当时**拼不出锚点所以没算**的，第二次重建拿到完整行后已验证：
  `collect._repo_commits` 的 `timeout=30` → `timeout=None` 仍然存活 —— git 子进程
  的超时**完全没被测过**，一旦某个仓库挂住，sync 会永久卡死（直接违反 NFR-006
  的 5 分钟预算）。所以真缺口是 5 条不是 4 条。

  **上面 17 条里的 13 条已补测试**（2026-09-23），每条都不是"写完就算"——都用
  把变异重新打进源码、确认对应测试变红、再还原的方式验证过：

  - 时区（5 条）：`collect.gather` 的 `collected_at`、`collect._dated_note_files`
    的 `ts`、`distill` 的 `created_at`、`sync.run` 与 `sync.cleanup_raw` 的默认日。
    后两条需要一个**假装本地是 UTC 的钟**才测得动：本机时区就是 +08，
    `tz=None` 与 `tz=config.TZ` 在真实钟上给出同一个日期，任何不控钟的断言都
    区分不了（`_UtcLocalClock` 干的就是这件事）。
  - 跳过式循环（8 条）：`collect._note_materials` 的两处、`collect.gather` 的
    discover 失败、`collect._git_materials` 的注释行、`sync.cleanup_raw`、
    `distill._direct_entries` 的两处、`distill._dedupe_entries`。
    夹具统一改成**坏数据在前、好数据在后**，并且坏数据必须真能进入那个循环 ——
    `cleanup_raw` 那条第一版夹具用了 `not-a-date.json`，它连 `????-??-??.json`
    这个 glob 都不匹配，等于什么都没测；换成 `0000-00-00.json` 才可区分。

  补完这些测试后**第二次全量重建**（2026-09-24）确认了效果：多杀 16 个变异体，
  存活 238 → 222，分数 85.0% → 86.0%。

  当时剩下 5 条真缺口：`sync.run` 调用 `cleanup_raw` 时传的 `tz=None`、
  `collect._iso` 与 `distill_prompt._build_user_prompt` 的 `if x` → `if x or True`
  （真值兜底被写成恒真）、`collect._repo_commits` 的 `ts=None`（提交条目会丢日期）、
  以及上面那条 `timeout=None`（git 挂住无人管）。

  最后 5 条也已补测试（2026-09-24，同样用"重新植入变异 → 对应测试变红 → 还原"
  验证）。**第三次全量重建证实了效果**：多杀 10 个变异体，存活 222 → 212，
  86.0% → 86.6%，真缺口这一桶清空。一条测试常能杀掉同一行的多个变体，所以
  5 条测试对应 10 个变异体是正常的。

  方法论上记两条：一是**不要用增量结果核对分类数字** —— 增量跑会复用旧判定，
  这正是 98% 假数的成因，只有 `mv mutants` 全量重建的数才算数；二是每次更新基线
  要**连同变异体名一起记**，上一版只写"28 个"导致这轮完全无法 diff 新增项。

  验证方式说明：分桶是规则化的（比较改动前后的字符串字面量、运算符、参数），
  每个桶都抽了样本**手工把变异打进源码再跑测试**确认（例如
  `collect.x_gather__mutmut_6` 手工植入后 409 个测试全绿 = 真存活
  —— 该例 2026-09-26 已被补测杀掉、编号也漂了，见上面那条订正；
  `sync.x__load_scope__mutmut_4` 同理）。名单以变异体名记录，不再只留数量 ——
  上一版只写"28 个"导致这次完全无法 diff 新增项。

  另外，原来列在"已证等价"里的 `distill._direct_type` 默认值那条，现在**被测试杀掉了**：
  它声称"等价"的依据只是返回值不变，但大写 `REFLECTION` 会落进未知类型兜底、
  多打一条对用户说谎的 WARNING。`test_direct_type_defaults_to_reflection_without_note_type`
  现在同时断言"不产生告警"，所以它不再是等价变异体。其余等价判断（codec 名大小写、
  `filter_novel` 的 `+1/+2`）依然成立。

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

### 补采第二天的真跑：输出被截断，整天 0 条入库

2026-09-24 补跑历史日（`make sync D=2026-09-18`）退出码 1，报
`DistillError: LLM returned invalid JSON`，重试那次也报同一句。第一反应是"我把
kimi 素材加进来，把那天撑大了"——**这条直觉是错的**，对着两个快照量一下就推翻
了：成功那批输入 1,332,282 字符，失败这批只有 234,021 字符，差 5.7 倍且方向相反。

真正决定炸不炸的是**输出**。单独打一发"给我 400 条 JSON"的请求看服务端返回：
`finish_reason='length'`，6894 字符 / 6865 completion tokens 处被掐断，
`json.loads` 报 `Unterminated string starting at column 6876` —— 与失败那两次
（断在 8370 / 8651 字符）同一签名。而 `_parse_or_retry` 的重试是**原样再发一遍
同一个 prompt**，所以对截断型失败它注定重复失败：一次注定断的调用 + 一次同样注定
断的重试，218 秒和一堆 token 换来 0 条条目。

修法是切批：`config/schema.json` 新增 `distill.batch_max_chars`（必填，<1 直接
拒绝加载），`src/distill_batches.py` 贪婪装箱，每批一次往返、条目合并后再走
`max_entries_per_day` 熔断；只含直并入素材（快记/trae）的批次跳过不发请求。
修完同一天的实测：188.06s、2 次 chat 往返、27 条入库、库内 39 点。

留这一节是因为它同时记着两件事：**慢和错都可能来自你以为的输入侧，而实际在输出
侧**；以及"重试"如果重的是同一个请求，它就不是容错，只是把同一次失败花两遍。

### 同日重跑重叠度（只读诊断）

`make sync` 同一天重跑会往库里**累加**（PRD §9 的 FR-007 待澄清项）。多出来的
条目到底是不是"与前面近似重复"，直接决定该改哪一边：若是重复，要改的是判据
或阈值；若不是，要改的就是 AC-003 那句"points 数不变"的口径。这条报告把它量
出来，**不改任何东西**：

```sh
make rerun-overlap                        # 按日：簇内（IN）与跨运行（EX）两组最近邻并排
make rerun-overlap D=2026-09-24           # 另出该日的分次运行明细
make rerun-overlap ARGS="--threshold 0.80"   # 换阈值试算（不改配置）
```

每天给**两列**，缺一不可：

- **IN**（同一次运行内部）：同一次 `distill` 写出的不同条目，**必须共存** ——
  这是判据不该碰的地方，也是"阈值能降到多低"的下界。
- **EX**（跨运行）：`filter_novel` 要看的那个量（本批之外的最近邻）。

读法是**先看 IN 再看 EX**：EX 整片在阈值以下 → 没拦住是对的；**IN max 高过
EX max → 问题不在阈值** —— 降阈值先误伤真条目，而对真正冗余的那一对（同一次
运行内部的）依然无能为力，因为 `filter_novel` 把本批 ID 排除在外，同批之间
根本不比。2026-09-26 实测就是这个形状（全库 221 点：IN max 0.906 > EX max
0.813，且过 0.82 的 12 条**全部**在簇内）。

判据与 `src/similarity.py:filter_novel` 同源：阈值读 `config/schema.json` 的
`novelty_threshold`（不抄第二份硬编码），比较的是"每条条目与本簇之外最近邻的
cosine"——正是 `filter_novel` 排除了本批 ID 之后要看的那个量。同一次 `distill`
调用写出的条目共享同一个 `created_at`，秒级前缀就是一次运行的指纹。

它只做 Qdrant 的 `scroll`/`query_points`，不碰 `data/raw/`、不需要
`ALLOW_SHRINK`、不落任何文件；连不上 Qdrant 时退出码 2 并提示先 `make up`。
这个数字是**一次性事实**（随库里内容变），所以它没有门禁、不进基线 —— 只在下
一次要裁决 FR-007 的口径时重新量一遍。

### 只读采集探针（不写任何文件）

AC-015 验一个采集源时要回答两件事：**这个源今天有素材吗**、**入库路径通不通**。第二件
必须真跑 `make sync`（它本来就要往真库 upsert）；第一件却不需要写盘 —— 而在 v0.7.11
之前，问第一件的唯一办法就是跑一次 sync，那会写 `data/raw/<day>.json`，也就是
`make redistill` 的重放基线（本仓库弄丢过它，v0.7.4，其中几天不可恢复）。探针把这个
形状断掉：

```sh
make probe D=2026-09-25              # 全源：逐源素材条数
make probe D=2026-09-25 S=qoder      # 只看一个源
```

它跑的就是 `collect.gather(..., persist=False)`：采集照跑、逐源计数照给（与落盘版看到
的素材完全一致），一个字节都不写。因为这条路径**没有门禁盯着**（写入边界的扫描面是
`src/`），探针跑前跑后自己比对 `data/raw/<day>.json` 的存在性与大小/mtime，变了就退出
1 —— "探针不写盘"不是靠注释承诺的。源目录仍是只读（宪法 V，SQLite 三个源一律
`mode=ro`），不需要 Qdrant，也不调用任何 LLM。**素材 0 条是成功而不是故障**：那就是
这个源今天真的没有东西可采。

`S=<源>` 的"只看一个源"是**合计口径**上的：其余插件用 `scope` 关掉（省时间），而
`scope` 关不掉的两类素材 —— git 提交（`projects` 节）与 `notes/` 手动快记（`gather` 里
没有对应开关）—— 会列在一条分隔线下面并标明"未计入合计"。它们当天确实被采到了，
只是不回答"这个源有素材吗"。

## 隐私与数据流向

- **源目录只读**：采集器对 11 个产品根（`~/.claude`、`~/.codex`、`~/.kimi-code`、
  `~/.qoder`、`~/.qoder-cn`、`~/.workbuddy-ai`、`~/.trae`、`~/.trae-cn`、
  `~/.local/share/opencode`、`~/.zcode`、`~/.hermes`）零写入、零标记；去重状态只
  依赖库内幂等 ID —— 不写水位文件，所以"没采到"和"采过了"不靠源目录里的痕迹区分。
  由 `scripts/write_boundary_check.py` 守（产品根写入是硬法，登记也豁免不了）。
- **SQLite 源按 `mode=ro` 打开**：三个库（opencode / zcode / hermes）一律用
  `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`。**已实测的边界**：`mode=ro`
  不改 `db` 文件本身（逐字节不变），但 SQLite **不保证不落 `-wal`/`-shm`** —— 产品
  干净退出、sidecar 已被删除时，`mode=ro` 会把它重建出来。这是 SQLite 的固有行为，
  **没有任何连接参数能同时做到"读得到 WAL 里的最新内容"与"不落 sidecar"**
  （`immutable=1` 零落盘但会静默丢掉 `-wal` 里的数据，所以不采用）。该规则同样由
  `write_boundary_check` 机械强制：非 `mode=ro` 的 `sqlite3.connect()` 判红。
- **凭据不进采集面**：按**路径**（`~/.zcode/v2/credentials*.json`、`~/.hermes/` 下的
  `.env` 与 `auth.json`、`~/.workbuddy/connectors/`、`~/.qoder-cn/` 下的 `state.json`）
  与**表**（B 族各有 `ALLOWED_TABLES`：opencode `session_message`、zcode
  `message`/`part`、hermes `messages`/`sessions`，不整库遍历）双向排除。已实证的是
  hermes 那一对：跑完全源采集后 `~/.hermes/.env` 与 `auth.json` 的 mtime 未变；
  其余路径是依据"只开库、不碰文件系统其余部分"的实现推断，**未逐条实测**。
- **写入边界**：运行时产物只写 `data/`（向量库与 raw 快照）和 `notes/`（快记），
  其余位置零写入；测试 fixture 全走系统临时目录。
- **密钥**：只从 `.env`（已 gitignore）读取，不出现在代码与提交物中；素材在送往
  蒸馏服务前先做正则脱敏（密钥/令牌/密码模式替换为 `[REDACTED]`）。
  这条边界 2026-09-24 前其实是虚的：脱敏的令牌名单只有 `sk-`/`ghp_`/`AKIA`/JWT，
  而本项目真实用的是方舟的 `ark-<UUID>`；赋值式规则又以 `\b` 起头，而 `_` 是单词
  字符，所以连 `ARK_API_KEY=…` 都不匹配。AC-012 的夹具用的恰好是 `sk-test-` 形态，
  于是"脱敏已验证"这个结论从来没覆盖到我们实际那把钥匙。现在两类都补上了，
  `tests/unit/test_sanitize.py` 也钉住了反向的误伤（`task-lifecycle.ts`、
  `landmark-2` 这类含 `sk-`/`ark-` 中段的正常文本必须原样通过）。
  同一份清单现在还是第 15 道提交门禁（`scripts/secret_scan.py`）的规则来源 ——
  运行时脱敏与"别提交进库"是两个判断，但"什么算密钥"只能有一份，否则就会像这次
  一样两边各自漂。它扫 git 跟踪的提交物，且赋值式规则只作用于非源码文件
  （`api_key=config.env("…")` 是代码，不是密钥）：第一次全库自扫出 29 处命中，
  **全是误报**，收紧到 0 之后才敢接进钩子 —— 一道永远红的门禁等于没有门禁。
- **蒸馏数据流向**：会话素材会发送到火山方舟（Ark）做蒸馏与嵌入。你本就通过
  方舟代理使用这些编码工具，数据流向与现有使用方式一致，无新增暴露面（PRD §9）。
- **`data/raw/<day>.json` 是重放基线，所以写入有闸门**：同一份快照若会被更少的素材数覆盖，
  默认**拒写**（`SnapshotShrinkError`）——它不报错才最要命：`make redistill D=<day>` 会拿
  一份更小的日去对照库里的实际点，**静默给出错误的 diff**。窄 scope 重跑同一天是合法操作，
  加 `ALLOW_SHRINK=1` 放行。这条闸门是 2026-09-26 真踩之后补的：当时"每个源挑一天单独验证"
  的那批跑，把 09-18（8 条）和 09-23（26 条）换成了 1 条的单来源快照（Qdrant 没事，
  受害的是基线）。**已知残留**：闸门只看素材数，"同数量但换了来源"的覆盖拦不住。
- **备份/迁移**：备份 = 复制 `data/`；换机器 = 复制 `data/` + `config/` + `notes/`。

## 更多

- 需求与验收：[PRD.md](PRD.md)（唯一事实来源）
- 实施原则：[.specify/memory/constitution.md](.specify/memory/constitution.md)
- 任务与进度：[specs/001-learning-memory-rag/tasks.md](specs/001-learning-memory-rag/tasks.md)
- AI 协作速查：[AGENTS.md](AGENTS.md)
