好，我直接给你一份完整的"Day 2 知识地图"。我会告诉你**每个概念要理解到什么程度**，以及**为什么是这个程度**，而不是泛泛地列知识点。

---

# Day 2 知识要求：Embedding + 向量检索

## 层次说明

我用三个层次区分：

- **会说**：能在面试/设计讨论中准确描述
- **会用**：能写出正确的代码，踩坑时知道往哪查
- **会判断**：遇到 tradeoff 时知道怎么选，以及为什么

---

## 一、Embedding 是什么

### 要达到的理解

**Embedding = 把任意文本映射到一个固定维度的实数向量空间。**

你需要能准确回答这几个问题：

**1. 为什么要转成向量？**

计算机没有办法直接比较两段文字的"意思有多接近"。但向量有数学工具：两个向量方向越接近，它们的 cosine similarity 越高。所以 embedding 的核心目标是：**语义相近的文本，在向量空间里位置也相近。**

"dental coverage" 和 "teeth cleaning reimbursement" 在关键词层面没有重叠，但经过 embedding 模型处理后，它们的向量 cosine similarity 会很高——因为模型在海量语料上训练后，"理解"了这两个表达语义上是同一件事。

**2. 向量长什么样？**

`text-embedding-ada-002` 输出 1536 维的 float 数组。就是 `[0.0023, -0.0154, 0.0891, ...]`，一个有 1536 个数字的列表。**维度本身没有人类可解释的含义**，是模型在训练过程中自动学出来的表示。

**3. 为什么同一个词在不同上下文里 embedding 可能不同？**

因为现代 embedding 模型（包括 `ada-002`）是 contextual 的——"bank"（银行/河岸）在不同句子里会有不同的向量。这和早期的 Word2Vec 每个词只有一个固定向量不同。

**程度要求**：会说 + 会用。不需要知道 Transformer 的数学细节，但要能解释"为什么 embedding 能捕获语义"。

---

## 二、Cosine Similarity

### 要达到的理解

这是向量检索的核心相似度度量。

**公式**：`cos(θ) = (A · B) / (|A| × |B|)`，值域 [-1, 1]，越接近 1 表示越相似。

**为什么用 cosine 而不用欧式距离？**

这是一个真实面试题。答案是：对于 embedding 向量，我们关心的是**方向**（语义方向），不是**长度**（长度受词频等因素影响，不代表语义）。Cosine similarity 通过归一化消除了长度影响，只比较方向。

举例：一个句子重复两遍，得到的向量长度会变大，但语义没变。用欧式距离会认为它们"不像"，但 cosine similarity 会正确判断它们几乎相同。

**程度要求**：会说 + 能在面试中正确解释 cosine vs 欧式距离的选择理由。不需要手写公式。

---

## 三、向量数据库（ChromaDB）

### 要达到的理解

**向量数据库解决的核心问题**：你有 100 万个 1536 维向量，用户来一个 query 向量，你要在毫秒级内找到最相近的 top-K 个。暴力算法要算 100 万次 cosine similarity，太慢。

**ANN（Approximate Nearest Neighbor）算法**：向量数据库用近似算法（如 HNSW — Hierarchical Navigable Small World）来加速搜索。牺牲极少量精度，换来几个数量级的速度提升。

你不需要懂 HNSW 的内部实现。但要知道：

- 向量数据库不是精确搜索，是近似搜索（这就是 "Approximate" 的含义）
- 这个近似在实际 RAG 应用中完全可以接受，因为 top-5 中偶尔漏掉一个也没关系
- ChromaDB 适合本地开发，但生产环境用 Azure AI Search / Pinecone / Weaviate 等

**ChromaDB 的核心操作**你要熟练：

```python
# 创建 collection
collection = client.create_collection("docs")

# 存入（文本 + 向量 + metadata + id，四个字段）
collection.add(
    documents=["chunk 原文"],
    embeddings=[[0.1, 0.2, ...]],  # 你自己算好传进来，或让 chroma 帮你算
    metadatas=[{"source": "policy.pdf", "page": 3}],
    ids=["chunk_001"]
)

# 检索
results = collection.query(
    query_embeddings=[[...]],  # query 的向量
    n_results=5
)
```

**程度要求**：会用。理解它解决什么问题，能写正确代码，知道它在 RAG 流程中的位置。

---

## 四、Embedding 的实际工程细节

这些是真正区分"会用"和"只知道概念"的地方。

### 4.1 Embedding 要分两个阶段调用，且两次调用要用同一个模型

- **Ingestion 时**：对每个 chunk 调用 embedding API，把向量存进数据库
- **Query 时**：对用户的问题调用同一个 embedding 模型，把 query 转成向量，再去数据库检索

**关键点**：如果 ingestion 用 `ada-002`，query 时也必须用 `ada-002`。换了模型，之前的向量全部失效，需要重新 ingest。这是新手最容易忽略的约束。

### 4.2 Embedding 是按 token 收费的

Azure OpenAI `text-embedding-ada-002`：$0.0001 / 1K tokens。一个 500-token 的 chunk，embedding 一次花 $0.00005。听起来便宜，但如果你有 10 万个 chunk，就是 $5。重复 ingest 会累积成本。

**工程实践**：ingest 后把向量持久化，不要每次启动都重新生成。

### 4.3 Embedding 有 token 上限

`ada-002` 最大输入 8191 tokens。如果你的 chunk 超过这个长度，API 会报错。这是 Chunking 策略需要控制 chunk 大小的原因之一（Day 1 引入的问题，Day 2 需要意识到）。

### 4.4 批量调用 vs 逐条调用

你有 1000 个 chunk 要 embed。逐条调用 API 很慢（网络 RTT 累积）。Azure OpenAI Embedding API 支持批量传入多个文本，一次调用处理多个 chunk。

```python
# 批量（推荐）
response = client.embeddings.create(
    input=["chunk1 text", "chunk2 text", "chunk3 text"],
    model="text-embedding-ada-002"
)
# response.data[0].embedding, response.data[1].embedding ...
```

**程度要求**：会用，知道这些坑的存在。

---

## 五、完整 Day 2 数据流

这是你要能清楚描述的完整流程图，面试中经常被问到：

```
【Ingestion 阶段】
PDF → 文本 → chunks
每个 chunk → Azure OpenAI Embedding API → 1536维向量
(chunk文本, 向量, metadata) → ChromaDB

【Query 阶段】
用户问题 → Azure OpenAI Embedding API → 1536维向量
向量 → ChromaDB.query(top_k=5) → [最相关的5个chunks]
chunks + 原始问题 → prompt → Azure OpenAI GPT → 回答
```

你要能解释这个流程里每一步的**输入是什么、输出是什么、为什么要这一步**。

---

## 六、你现在不需要知道的（边界很重要）

避免过度学习浪费时间：

- ❌ Transformer 的 attention 机制数学细节
- ❌ HNSW 算法的图结构原理
- ❌ Word2Vec / GloVe 等早期 embedding 方法
- ❌ Fine-tuning embedding 模型
- ❌ 各种向量数据库的横向对比（Pinecone vs Weaviate vs Qdrant）
- ❌ 量化（quantization）降低向量存储成本

这些都是真实的知识，但 Day 2 不需要。你会在 Day 11（本地 HuggingFace 模型）自然地接触到更底层的内容。

---

## 七、一道自测题

Day 2 结束后，你应该能清晰地回答这道面试题：

> **"你们的 RAG 系统检索是怎么实现的？为什么用向量检索而不是关键词搜索？有什么局限性？"**

标准答案的关键要素：

1. 文档在 ingest 时被转成向量存入向量数据库
2. 用户 query 也被转成向量，用 cosine similarity 找最近邻
3. 能捕获语义相似性，不依赖词面匹配
4. 局限：不擅长精确字符串匹配（policy number、专有名词）——这就是为什么后面需要 Hybrid Search

如果你能把这个答案说清楚，并加上一句"所以后来我们引入了 Hybrid Search 来结合两者优势"，这个问题你就回答得比大多数候选人好了。

---
