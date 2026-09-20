"""坏例子：把 rubric、类型和素材边界全部写坏的 prompt 拼装。

反例依据：
1. OWASP LLM Prompt Injection Prevention Cheat Sheet：
   https://github.com/OWASP/www-project-top-10-for-large-language-model-applications
   把外部内容直接拼进指令区，会让内容中的文字有机会覆盖系统规则。
2. openai/openai-cookbook：
   https://github.com/openai/openai-cookbook
   需要程序解析输出时，应明确结构化契约；自由文本会让解析逻辑脆弱。

下面的代码片段是根据常见错误整理的教学反例，不是从其他项目复制的坏代码。
"""

# ===== 坏例子：常见错误 =====
#
# 这个例子故意展示下面这些问题：
# 1. 类型枚举和 rubric 硬编码，改配置后 prompt 不会变化；
# 2. 素材直接拼进 system prompt，没有 untrusted data 边界；
# 3. 不要求 source_refs，蒸馏结果无法追溯；
# 4. 没有 JSON 输出契约，调用方只能猜格式；
# 5. 模块 import 时就调用 LLM，产生网络副作用；
# 6. 把“保留有价值内容”写成模糊口号，没有可配置信号和示例。

BAD_EXAMPLE = r'''
from openai import OpenAI

client = OpenAI()

SYSTEM = """
你是蒸馏器，只能输出 progress/error/idea/reflection。
请保留有价值的内容，丢掉没价值的内容。
"""

def distill(materials):
    raw_text = "\n".join(material["text"] for material in materials)
    response = client.chat.completions.create(
        model="some-model",
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": raw_text},
        ],
    )
    return response.choices[0].message.content
'''
