"""坏例子：把脱敏、类型校验和熔断全部省略的蒸馏编排。

反例依据：
1. OWASP LLM Prompt Injection Prevention Cheat Sheet：
   https://github.com/OWASP/www-project-top-10-for-large-language-model-applications
   未经隔离的外部素材可能把指令和敏感信息一起带进模型。
2. openai/openai-cookbook：
   https://github.com/openai/openai-cookbook
   如果调用方直接信任自由文本，就无法稳定处理 JSON 解析和类型错误。

下面的代码片段是根据常见错误整理的教学反例，不是从其他项目复制的坏代码。
"""

# ===== 坏例子：常见错误 =====
#
# 这个例子故意展示下面这些问题：
# 1. 原始素材直接发送给 LLM，密钥可能进入第三方服务；
# 2. 假设模型一定返回合法 JSON，解析失败直接崩溃；
# 3. 不校验 type，配置外类型会被写入数据库；
# 4. 未知类型无限重试，可能造成费用和延迟失控；
# 5. 不做 max_entries_per_day 熔断；
# 6. 手动快记和 trae 记录也重复调用 LLM，违反轻处理路径；
# 7. 日志直接打印完整素材，敏感信息会进入日志。

BAD_EXAMPLE = r'''
import json

def distill(day_raw):
    text = "\n".join(m["text"] for m in day_raw["materials"])
    print(text)  # 坏：日志泄露完整素材。

    raw = call_llm(text)  # 坏：没有先脱敏，也没有把素材标为不可信数据。
    entries = json.loads(raw)["entries"]  # 坏：假设 JSON 一定合法。

    while True:  # 坏：没有重试上限。
        bad = [e for e in entries if e["type"] not in TYPES]
        if not bad:
            break
        entries = json.loads(call_llm(text))["entries"]

    return entries  # 坏：没有类型校验后的丢弃策略，也没有每日条数熔断。
'''
