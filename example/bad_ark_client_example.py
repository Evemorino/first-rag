"""坏例子：把常见错误集中在一个不可执行的代码片段中。

这个文件不会真的执行坏例子。
坏代码放在 BAD_EXAMPLE 字符串里，因为它包含故意写错的 Python 语法。
"""

# ===== 坏例子：常见错误 =====
#
# 这个例子故意展示下面这些问题：
# 1. 引入不必要的 LangChain 依赖；
# 2. 在模块 import 时创建 client；
# 3. 硬编码模型名；
# 4. 把密钥写进源码；
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
