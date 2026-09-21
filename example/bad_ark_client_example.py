"""坏例子：把常见错误集中在一个不可执行的代码片段中。

安全反例依据来自 OWASP 开源项目：
https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Secrets_Management_Cheat_Sheet.md

OWASP 明确指出，API key 等秘密常被以明文硬编码在源码中，
这是需要治理的泄露风险。下面的代码片段是根据本项目用户草稿
整理的教学反例，不是从其他开源项目复制来的坏代码。
"""

# ===== 坏例子：常见错误 =====
#
# 这个例子故意展示下面这些问题：
# 1. 硬编码 API key —— OWASP Secrets Management 明确列为安全风险；
# 2. 引入不必要的 LangChain 依赖；
# 3. 在模块 import 时创建 client；
# 4. 硬编码模型名；
# 5. 使用只支持单条文本的 embed_query；
# 6. 使用 JavaScript/TypeScript 的 const；
# 7. 在模块顶层打印测试结果。

BAD_EXAMPLE = r'''
from langchain.embeddings import OpenAIEmbeddings  # 坏：引入了不必要的依赖。

embedding_client = OpenAIEmbeddings(  # 坏：模块 import 时就创建对象。
    model="text-embedding-3-large",  # 坏：模型名被硬编码。
    openai_api_key="<hardcoded-secret-do-not-do-this>",  # 坏：密钥进入源码。
    chunk_size=1,  # 坏：与当前任务无关的配置。
)

const embedding = embedding_client.embed_query("hello")  # 坏：const 不是 Python。

print(embedding)  # 坏：模块顶层产生测试副作用。
'''
