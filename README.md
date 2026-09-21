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
