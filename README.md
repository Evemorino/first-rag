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

顺序是：大文件/冲突/JSON/YAML/AST → 行尾空白 → **密钥扫描** → pytest → CRAP。
改 README 这类非 Python 文件不会触发测试；想临时跳过用 `git commit --no-verify`。

覆盖率只能说明"这行跑过没有"，说明不了"改坏了会不会被发现"。所以再加两层：

```sh
make cov           # 行覆盖率 → coverage.xml（下面两项的输入）
make crap          # CRAP = 复杂度² × (1-覆盖)³ + 复杂度，≥30 视为 crappy
make mutation-selfcheck  # 先跑这个：确认"改坏源码 → 测试会红"这条链路真的通
make mutation      # 变异测试：改坏源码，看测试能不能抓到（内部会先跑自检）
```

- **CRAP** 把复杂度和覆盖率乘在一起：复杂度 23、覆盖 74% 的函数 CRAP 是 32.8，
  一眼看出该拆还是该补测试。当前基线：函数内语句覆盖 88.5%（口径只算函数体内
  语句，与 pytest 报的全量行覆盖 90% 不是一回事），142 个函数，均值 4.6，
  **0 个 crappy**（`config.py:_validate` 按 section 拆成 6 个小函数；
  `collect._cap` / `redistill._fetch_day_entries` / `claude_code` 错误提取
  补测试到 100% 覆盖）。
- **变异测试**默认只打核心链路（ids / similarity / ark_client / distill_prompt /
  ingest / sync，见 `pyproject.toml` 的 `only_mutate`）。当前基线：383 个变异体
  被杀死、71 个存活、5 个无测试覆盖，**变异分数 84.4%**。

  存活的 71 个里，约 34 个在 `sync.__try_lock` —— 那是 Windows 的 `msvcrt`
  分支，在 macOS 上根本执行不到；约 31 个是 prompt 里的字段名与示例文案
  （`"source"` 改成 `"SOURCE"` 这种）。这两类都不该靠测试去杀：前者得造假
  平台环境，后者得把整段 prompt 抄进断言，维护成本远高于收益。
  所以 `pyproject.toml` 里用 `do_not_mutate_patterns` 排除了纯文案行与日志行
  ——变异测试该验证逻辑，不是验证文案。核心的 `similarity.filter_novel` 与
  `ingest.upsert` 只剩 2 个存活，都是等价变异（改了行为不变）。

两者都依赖 coverage 数据，所以顺序是 `cov → crap / mutation`。

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
