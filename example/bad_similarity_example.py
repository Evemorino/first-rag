"""坏例子：用文本相等替代语义去重，并破坏幂等重跑。

反例依据：
1. qdrant-client 开源仓库：
   https://github.com/qdrant/qdrant-client
   向量查询的价值在于发现“表达不同但语义相近”的条目。
2. Qdrant 官方文档：
   https://qdrant.tech/documentation/concepts/search/
   搜索返回 score；是否重复应由阈值判断，而不是靠字符串完全相等。

下面的代码片段是根据常见错误整理的教学反例，不是从其他项目复制的坏代码。
"""

# ===== 坏例子：常见错误 =====
#
# 这个例子故意展示下面这些问题：
# 1. 只比较 text 是否完全相同，漏掉语义重复；
# 2. 阈值硬编码在业务代码里，改配置不会生效；
# 3. 不检查 collection 是否存在，首次运行可能直接报错；
# 4. 查到相同 Point ID 也直接跳过，破坏同日重跑幂等；
# 5. 过滤 entries 后没有同步过滤 vectors，导致条目和向量错位；
# 6. 每条候选重新 embedding，浪费一次已经做过的批量向量。

BAD_EXAMPLE = r'''
def filter_novel(entries, vectors):
    kept = []
    seen_texts = set()

    for entry in entries:
        if entry["text"] in seen_texts:
            continue  # 坏：只能发现完全相同的文本。
        if qdrant.search(entry["text"]):  # 坏：重新 embedding，且阈值写死。
            continue
        seen_texts.add(entry["text"])
        kept.append(entry)

    return kept  # 坏：vectors 没有同步过滤，调用方无法安全对齐。
'''
