### Day 1: 搞清楚 "RAG 到底在干什么"

**目标：** 在你现有 MVP 上，亲手体验 RAG 的核心流程，理解每一步在做什么。

**需求引导**

- 在Day 0的极简MVP之后，我们已经有了

1. 准备好的 policy.txt文档作为知识库
2. 函数`load_policy_into_docs`:读取policy.txt文档 → 按固定长度（400）切分成 chunks → 存入 SQLite
3. 函数`retrieve_docs`：从 SQLite 中用 `LIKE` 关键词搜索最相关的 chunks

```python
pattern = f"%{question}%"
    cursor.execute(
        """
        SELECT id, title, chunk
        FROM docs
        WHERE chunk LIKE ? OR title LIKE ?
        LIMIT ?
        """,
        (pattern, pattern, top_k),
    )
    rows = cursor.fetchall()
```

4. `chat_answer`（接收POST请求的API`"/v1/chat/answer"`） 会调用上面的函数`retrieve_docs`，找到和question最相关（用LIKE）的top_k个结果→ 拼进 prompt → 调 OpenAI 生成回答

5. 在`chat_answer`函数里，在回答中返回 citations（引用了哪些 chunk）

```python
citations.append(
            Citation(
                doc_id=row["id"],
                title=row["title"],
                snippet=snippet,
            )
        )
```

**你要做的事：**

1. 准备 3-5 个 PDF 文档作为知识库（比如一家虚构公司的员工手册、福利政策、岗位描述——和原项目一致）
2. 写一个 `scripts/prepdocs.py` 脚本：读取 PDF → 提取文本 → 按段落/固定长度切分成 chunks → 存入 SQLite

**Day 1 结束时你应该能：**

- `python scripts/prepdocs.py` 把 PDF 处理成 chunks 存入数据库
- `curl POST /v1/chat/answer` 能基于文档内容回答问题（虽然检索效果差）
- 理解 RAG = Retrieval + Augmented Generation，你亲手实现了最朴素的版本

**AI 概念：** RAG 基本流程、文档 Chunking、Prompt Stuffing

**引导思考：**

> 试着问你的系统："What is the dental coverage?" 然后再问 "Tell me about vision benefits"。  
> 你会发现，用 `LIKE` 关键词搜索的效果很差——"dental coverage" 搜不到 "teeth cleaning reimbursement" 相关的内容。  
> **问题**：关键词搜索无法理解语义，同义词、换一种说法就搜不到了。  
> **这就是为什么我们需要 Vector Search——这是明天要解决的。**
