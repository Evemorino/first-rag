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

## 故障速查

- Qdrant 未启动 → sync/ask 报连接错误，`make up` 后重试（幂等保证安全）
- 某工具目录消失 → 正常情况，该来源 no-op，看日志确认
- 重跑任何 sync 均安全：幂等 ID 保证无重复（PRD FR-014）
