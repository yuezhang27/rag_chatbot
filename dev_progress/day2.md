### Day 2: 向量检索——让搜索 "懂语义"

**目标：** 引入 Embedding + 向量数据库，替换掉 Day 1 的关键词搜索。

**你要做的事：**

1. 注册 Azure OpenAI，部署 `text-embedding-ada-002` 模型（从今天起就开始用 Azure 资源）
2. 修改 `prepdocs.py`：对每个 chunk 调用 Azure OpenAI Embedding API → 拿到向量
3. 引入 ChromaDB（本地向量数据库），将 chunk 文本 + 向量存入 ChromaDB
4. 修改检索逻辑：用户提问 → 把问题也转为向量 → 在 ChromaDB 中做 cosine similarity 搜索 → 返回最相关的 top-K chunks
5. 把 LLM 也切到 Azure OpenAI（`gpt-4o-mini`）

```text
我正在完成Day 2的内容，首先是各种准备工作：
1. 注册 Azure OpenAI，部署 `text-embedding-ada-002` 模型（从今天起就开始用 Azure 资源）
5. 把 LLM 也切到 Azure OpenAI（`gpt-4o-mini`）

- 我现在已经登陆了Azure portal，然后呢，这两个怎么完成？另外我想这两个应该是有费用的，我每天开发工作结束，是不是要删除他们？另外，我记得terraform可以部署到云，这两个注册工作，和llm的切换，可以用terraform完成吗？
- 对于第二个，LLM切换到Azure OpenAI（`gpt-4o-mini`），这个和我本地配置OpenAI的API，然后本地实例化一个gpt-4o-mini的model的区别在哪里？为什么要这样做？是因为未来用户只能用云端的资源（或者远程虚拟机资源）但无法用我本地的key，因此会无法实例化，对吗？

3. 引入 ChromaDB（本地向量数据库），将 chunk 文本 + 向量存入 ChromaDB
- 这个需要命令行下载什么吗？下载完需要配置什么？
```

```text
我当前的实现方式是，在app.py的admin_upload_pdf函数里，会调用scripts/textsplitter.py里的split_text函数，把上传的文件拆成chunks。然后，app.py里的insert_chunks_into_docs，会把chunks直接存入数据库

现在，我希望，在scripts文件夹下面，

1. 修改：对每个 chunk， 调用 Azure OpenAI Embedding API → 拿到向量. 这里涉及到Azure资源相关的配置，请阅读.env文件
2. 引入 ChromaDB（本地向量数据库），将 chunk 文本 + 向量存入 ChromaDB
3. 修改检索逻辑：用户提问 → 把问题也转为向量 → 在 ChromaDB 中做 cosine similarity 搜索 → 返回最相关的 top-K chunks
4. 把 LLM 也切到 Azure OpenAI（`gpt-4o-mini`）。
```

**引导思考：**

> 现在再试 "What is the dental coverage?" ——它应该能搜到 "teeth cleaning" 相关内容了！  
> 但试试搜 "policy number ABC-123"——纯向量搜索反而搜不到了，因为向量擅长语义但不擅长精确匹配。  
> **问题**：向量搜索和关键词搜索各有优劣。  
> **这就是为什么实际生产中要用 Hybrid Search——Day 5 会引入。**

**Day 2 结束时你应该能：**

- 文档被 embed 后存入 ChromaDB
- 提问时用向量相似度检索，效果比 Day 1 显著提升
- 从今天起 LLM 和 Embedding 都走 Azure OpenAI

**AI 概念：** Embedding、向量空间、Cosine Similarity、Vector Database