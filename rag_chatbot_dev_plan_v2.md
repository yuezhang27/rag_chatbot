# RAG Chatbot 开发计划 v2

## 项目概述

复刻 Azure Search OpenAI Demo：一个基于 RAG（Retrieval-Augmented Generation）的企业知识库聊天应用。用户可以上传文档，系统自动索引，然后通过对话式界面基于文档内容回答问题，并附带引用来源。

---

## Part 1: PRD Lite — 功能清单

### 1. 文档上传与管理

- 支持上传 PDF / TXT / MD 等格式的文档
- 上传后自动触发文档处理流水线
- 文档列表展示（查看已上传文档、删除文档）

### 2. 文档处理流水线（Ingestion Pipeline）

- 文档解析：从 PDF 等格式中提取纯文本
- 文本分块（Chunking）：将长文档切分为适合检索的片段
- 向量化（Embedding）：调用 Embedding 模型将每个 chunk 转为向量
- 索引存储：将 chunk 文本 + 向量写入向量数据库

### 3. Chat 对话（多轮）

- 用户输入问题，系统基于文档内容生成回答
- 支持多轮对话，带上下文记忆
- 每条回答附带 citations（引用来源 chunk + 文档页码/标题）
- 展示 "思考过程"（Thought Process）：显示检索到了哪些 chunk、用了什么 prompt

### 4. Ask 单轮问答

- 单次提问，不保留上下文
- 同样附带 citations 和思考过程展示

### 5. 检索策略可配置

- 支持关键词检索（Text Search）
- 支持向量检索（Vector Search）
- 支持混合检索（Hybrid = Text + Vector）
- UI 上可切换检索策略

### 6. LLM 回答行为可配置

- 可调 temperature
- 可设置 system prompt override
- 可选择是否排除特定类别内容
- 可切换是否使用 semantic ranker（对检索结果做二次排序）

### 7. Streaming 流式响应

- LLM 回答通过 SSE 流式返回前端，逐字显示

### 8. Chat History 持久化

- 对话记录存入数据库，用户可查看历史对话

### 9. React 前端

- Chat 界面（多轮对话）
- Ask 界面（单轮问答）
- Settings 面板（调节检索策略、LLM 参数）
- Citation 面板（点击引用查看原文片段）
- 文档管理页面（上传、列表、删除）

### 10. Docker 容器化 + Kubernetes 编排

- 后端 + 前端 + 依赖服务通过 Docker Compose 本地一键启动
- 生产环境使用 Kubernetes 编排部署

### 11. Azure 云部署

- 使用 Terraform 实现基础设施即代码
- 部署到 Azure Kubernetes Service (AKS)
- 使用 Azure OpenAI、Azure AI Search、Azure Blob Storage

### 12. LLM 可观测性与 RAG 评估

- 使用 Langfuse 追踪每次 LLM 调用的 input/output/latency/token/cost
- 使用 RAGAS 框架评估 RAG 质量：faithfulness、answer relevancy、context precision
- 应用性能监控（Prometheus + Grafana）

### 13. 本地模型支持

- 支持 HuggingFace 开源 Embedding 模型作为 Azure OpenAI Embedding 的替代
- 支持本地 Cross-Encoder 做 Re-ranking（PyTorch 推理）

---

## Part 2: 13 天开发计划

> **原则**：每一天结束时都能跑起来。从最简单的实现出发，通过"发现问题→引入新概念"的方式逐步升级。Azure 资源从 Day 1 就开始接触，而不是最后才引入。

### 你目前已有的基础

你的 `rag_chatbot` repo 已经有：一个 FastAPI 后端、SQLite 存储、本地 `policy.txt` 作为文档源、简单的文本切分写入 `docs` 表、一个 `POST /v1/chat/answer` 接口调用 OpenAI 生成回答。本质上是一个**最简 RAG MVP**：把文本塞进 prompt context → 让 LLM 回答。

---

### Day 1: 搞清楚 "RAG 到底在干什么"

**目标：** 在你现有 MVP 上，亲手体验 RAG 的核心流程，理解每一步在做什么。

**你要做的事：**

1. 准备 3-5 个 PDF 文档作为知识库（比如一家虚构公司的员工手册、福利政策、岗位描述——和原项目一致）
2. 写一个 `scripts/prepdocs.py` 脚本：读取 PDF → 提取文本 → 按段落/固定长度切分成 chunks → 存入 SQLite
3. 修改你现有的 `/v1/chat/answer`：从 SQLite 中用 `LIKE` 关键词搜索最相关的 chunks → 拼进 prompt → 调 OpenAI 生成回答
4. 在回答中返回 citations（引用了哪些 chunk）

**引导思考：**

> 试着问你的系统："What is the dental coverage?" 然后再问 "Tell me about vision benefits"。  
> 你会发现，用 `LIKE` 关键词搜索的效果很差——"dental coverage" 搜不到 "teeth cleaning reimbursement" 相关的内容。  
> **问题**：关键词搜索无法理解语义，同义词、换一种说法就搜不到了。  
> **这就是为什么我们需要 Vector Search——这是明天要解决的。**

**Day 1 结束时你应该能：**

- `python scripts/prepdocs.py` 把 PDF 处理成 chunks 存入数据库
- `curl POST /v1/chat/answer` 能基于文档内容回答问题（虽然检索效果差）
- 理解 RAG = Retrieval + Augmented Generation，你亲手实现了最朴素的版本

**AI 概念：** RAG 基本流程、文档 Chunking、Prompt Stuffing

---

### Day 2: 向量检索——让搜索 "懂语义"

**目标：** 引入 Embedding + 向量数据库，替换掉 Day 1 的关键词搜索。

**你要做的事：**

1. 注册 Azure OpenAI，部署 `text-embedding-ada-002` 模型（从今天起就开始用 Azure 资源）
2. 修改 `prepdocs.py`：对每个 chunk 调用 Azure OpenAI Embedding API → 拿到向量
3. 引入 ChromaDB（本地向量数据库），将 chunk 文本 + 向量存入 ChromaDB
4. 修改检索逻辑：用户提问 → 把问题也转为向量 → 在 ChromaDB 中做 cosine similarity 搜索 → 返回最相关的 top-K chunks
5. 把 LLM 也切到 Azure OpenAI（`gpt-4o-mini`）

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

---

### Day 3: Chat 前端 + 多轮对话

**目标：** 搭建 React 前端，实现多轮对话和 Citation 展示。

**你要做的事：**

1. 用 React + TypeScript 搭建前端（参考原项目的 Chat 页面布局）
2. 实现 Chat 界面：消息气泡、输入框、发送按钮
3. 后端加 conversation history 支持：每次请求带上之前的对话记录，LLM 能理解上下文
4. 实现 Citation 展示：每条回答下方显示引用来源，点击可展开查看原文片段
5. Docker Compose：前端 + 后端 + ChromaDB 一键启动

**引导思考：**

> 多轮对话你直接把所有历史消息都塞给 LLM 了。聊 10 轮之后，试试——是不是 token 超限了？  
> **问题**：直接塞全部 history 会超出 context window。  
> **这就引出了 "对话历史管理" 的问题——后面我们会用 summarization 或滑动窗口来解决。**

**Day 3 结束时你应该能：**

- 浏览器打开 `localhost:3000` 看到 Chat 界面
- 可以多轮对话，回答带有 citations
- `docker-compose up` 一键启动所有服务

**技术栈：** React、TypeScript、Docker Compose

---

### Day 4: 流式响应 + Ask 单轮问答

**目标：** LLM 回答从 "等半天一次性返回" 变成 "逐字蹦出来"。加入 Ask 模式。

**你要做的事：**

1. 后端改为 SSE（Server-Sent Events）流式返回：FastAPI 的 `StreamingResponse` + OpenAI 的 `stream=True`
2. 前端实现流式接收：用 `EventSource` 或 `fetch` 的 ReadableStream 逐字渲染
3. 新增 Ask 页面（单轮问答，不带 conversation history）
4. 新增 "Thought Process" 展示面板：显示检索到了哪些 chunks、使用的 prompt 模板

**引导思考：**

> 现在用户体验好多了——回答不用等了。  
> 但你注意到 streaming 的时候 citations 怎么处理？你是等流结束才返回 citations，还是 inline 标注？  
> **问题**：Streaming + Citation 的协调不简单。原项目的做法是在 stream 中 inline 标注 `[doc1]` 这种 marker，最后一个 chunk 发送完整的 citation 数据。  
> **想想你要怎么设计这个协议。**

**Day 4 结束时你应该能：**

- Chat 页面回答逐字流式输出
- Ask 页面可单轮问答
- 可以展开看 "思考过程"

**AI 概念：** Streaming、SSE、Prompt Engineering（system prompt 的设计）

---

### Day 5: Hybrid Search + Azure AI Search

**目标：** 从本地 ChromaDB 升级到 Azure AI Search，并引入 Hybrid Search。

**你要做的事：**

1. 创建 Azure AI Search 资源
2. 修改 `prepdocs.py`：将 chunks 推送到 Azure AI Search Index（包含文本字段 + 向量字段）
3. 实现三种检索策略，后端根据参数切换：
   - Text Search（关键词，BM25）
   - Vector Search（向量相似度）
   - Hybrid Search（两者结合 + RRF 融合排序）
4. 开启 Semantic Ranker（Azure AI Search 内置的语义重排序功能）
5. 前端 Settings 面板：让用户选择检索策略

**引导思考：**

> 用三种策略分别试 "What is the dental coverage?" 和 "policy number ABC-123"。  
> Hybrid Search 两个都能搜到！而且开了 Semantic Ranker 之后，排序更准了。  
> **概念**：Hybrid Search = 关键词的精确性 + 向量的语义理解。Semantic Ranker 是第二阶段排序（re-ranking），用 cross-encoder 模型对候选结果重新打分。  
> **这就是企业级 RAG 的标准做法。**

**Day 5 结束时你应该能：**

- 检索走 Azure AI Search
- Settings 面板可切换 Text / Vector / Hybrid
- 开启 Semantic Ranker，明显感受排序质量提升

**AI 概念：** Hybrid Search、BM25、RRF（Reciprocal Rank Fusion）、Semantic Ranking / Re-ranking、Cross-Encoder vs Bi-Encoder

---

### Day 6: 文档上传 + Ingestion Pipeline 自动化

**目标：** 用户可以从前端上传新文档，系统自动走完 解析→分块→Embedding→入索引 的全流程。

**你要做的事：**

1. 后端新增文档上传 API：`POST /v1/documents/upload` 接收文件，存到 Azure Blob Storage
2. 实现完整 Ingestion Pipeline：
   - PDF 解析（用 `PyMuPDF` 或 Azure Document Intelligence）
   - Chunking（固定大小 + overlap，或按段落/标题语义分块）
   - Embedding（调 Azure OpenAI）
   - 写入 Azure AI Search Index
3. 新增文档管理 API：`GET /v1/documents`（列表）、`DELETE /v1/documents/{id}`（删除并从索引移除）
4. 前端新增文档管理页面

**引导思考：**

> 上传一个 50 页的 PDF，你会发现处理要好几分钟。用户上传后在那里干等着。  
> **问题**：Ingestion 是个耗时操作。  
> **你会怎么解决？** 两个方向：① 异步处理（后台任务 + 状态轮询）② 给用户展示进度。  
> **Day 9 会用 Celery / 后台任务来处理这个问题。**
>
> 另外，回头看你写的 Ingestion 代码——检索逻辑、LLM 调用、文档处理全混在一起了。这些步骤（解析→分块→Embedding→索引）像不像一条流水线？  
> 你手动用 for 循环把它们串起来，如果某一步改了，整个代码都要动。  
> **问题**：有没有一个工具可以帮你把这种 "多步骤流水线" 管理得更清晰？  
> **这就是 LangChain 要解决的——明天引入。**

**Day 6 结束时你应该能：**

- 从前端上传 PDF，等处理完后就能对它提问
- 文档管理页面可以查看、删除文档
- 文档存在 Azure Blob Storage，索引在 Azure AI Search

**技术栈：** Azure Blob Storage、Azure Document Intelligence（可选）

---

### Day 7: 用 LangChain 重构 RAG 链路 ⭐ 新增

**目标：** 用 LangChain 替换你手写的 "检索→拼 prompt→调 LLM" 流程，体会框架化 RAG 开发的效率。

**你要做的事：**

1. 安装 `langchain`、`langchain-openai`、`langchain-community`
2. 用 LangChain 的 `AzureChatOpenAI` 替换你手写的 OpenAI API 调用
3. 用 LangChain 的 `AzureOpenAIEmbeddings` 替换你手写的 Embedding 调用
4. 用 LangChain 的 `AzureSearch` VectorStore 封装 Azure AI Search 的检索
5. 用 LCEL（LangChain Expression Language）构建 RAG Chain：
   ```
   retriever | format_docs | prompt_template | llm | output_parser
   ```
6. 用 LangChain 的 `ConversationBufferWindowMemory` 或 `ConversationSummaryMemory` 管理多轮对话的 history（解决 Day 3 发现的 token 超限问题）

**引导思考：**

> 对比一下：你之前手写的检索 + prompt 拼接 + LLM 调用大概多少行代码？用 LangChain 之后呢？  
> LangChain 的价值不只是少写代码——它提供了**标准化的抽象**。你的 retriever、llm、memory 都是可插拔的组件。  
> 换一个 retriever？换一行代码。换一个 LLM？换一行代码。
>
> 但你也要注意：LangChain 是一个有 "意见" 的框架，它的抽象不一定完美。  
> 比如你会发现，当你想做一些自定义的 citation 处理逻辑时，LangChain 的 Chain 反而会碍事。  
> **关键认知**：LangChain 适合快速搭建标准 RAG pipeline，但复杂的自定义逻辑（比如 Approach Pattern）可能还是要自己写。  
> **大厂的做法**：先用 LangChain 快速验证原型，生产环境根据需要决定是保留 LangChain 还是抽出来自己实现。  
> **Day 10 重构时，你可以有意识地决定哪些保留 LangChain，哪些自己写。**

**Day 7 结束时你应该能：**

- Chat 和 Ask 的 RAG 链路由 LangChain LCEL 驱动
- 多轮对话用 LangChain Memory 管理，不再 token 超限
- 所有功能正常工作，代码量比之前少很多

**技术栈：** LangChain、LCEL（LangChain Expression Language）

**AI 概念：** RAG Chain、Retriever 抽象、Memory 管理、LLM 编排框架

---

### Day 8: Prompt Engineering + LLM 行为调优

**目标：** 深入优化 Prompt 设计，让 LLM 回答质量显著提升。

**你要做的事：**

1. 设计结构化的 System Prompt：
   - 角色设定（"你是一个企业知识库助手"）
   - 回答规则（必须基于提供的 context、不知道就说不知道、不要编造）
   - Citation 格式规则（用 `[doc1]` 标注引用）
   - Few-shot 示例（在 prompt 中给出 "好的回答" 的范例）
2. 实现 Prompt 模板系统：用 LangChain 的 `ChatPromptTemplate` 管理不同场景的 prompt
3. 实现 Chain-of-Thought 引导：在 prompt 中加入 "先分析 context 中有哪些相关信息，再组织回答" 的引导
4. 前端 Settings：可自定义 system prompt override、调节 temperature、设置 "排除类别" 过滤

**引导思考：**

> 试试故意问一个文档里没有的问题。你的系统是不是瞎编了答案？  
> **问题**：这就是 "幻觉"（Hallucination）。  
> **解法**：在 prompt 中明确指示 "如果提供的 context 中没有相关信息，回答 '我不知道'"。  
> 但这只是 prompt 层面的约束，不能 100% 杜绝幻觉。
>
> 另一个问题：你改了好几版 prompt，但你怎么知道哪版更好？凭感觉试了几个问题？  
> **问题**：你没有系统化的方式来衡量 prompt 的效果。  
> **这就引出两个需求：** ① 我需要看到每次 LLM 调用的完整输入输出（可观测性）② 我需要一个自动化的评估框架。  
> **Day 12 会用 Langfuse + RAGAS 来解决这两个问题。**

**Day 8 结束时你应该能：**

- LLM 回答质量明显提升，不乱编
- Settings 面板可调 temperature、prompt override
- 使用 Few-shot 和 Chain-of-Thought 技巧

**AI 概念：** Prompt Engineering、Few-shot Learning、Chain-of-Thought、Hallucination（幻觉）、Grounding

---

### Day 9: 后台任务 + Chat History 持久化

**目标：** 文档处理改为异步后台任务；对话记录持久化到数据库。

**你要做的事：**

1. 引入 Celery + Redis 作为后台任务队列
2. 文档上传后立即返回 `task_id`，Ingestion 在后台异步执行
3. 新增状态查询 API：`GET /v1/documents/{task_id}/status`
4. 前端上传后轮询状态，显示处理进度
5. Chat History 持久化：
   - 用 PostgreSQL 存储对话记录（conversation_id → messages）
   - 新增 API：`GET /v1/conversations`（历史列表）、`GET /v1/conversations/{id}`（恢复对话）、`DELETE /v1/conversations/{id}`
6. 前端侧边栏显示历史对话列表

**引导思考：**

> 现在你的 Docker Compose 文件里有：FastAPI、React、Redis、Celery Worker、PostgreSQL。  
> 服务越来越多了。想一想：**你的代码结构还合理吗？**  
> 检索逻辑、LLM 调用逻辑、文档处理逻辑是不是全挤在一起了？  
> 而且你用了 LangChain，但 LangChain 的 Chain 和你自己写的 Approach 逻辑混在一起，边界不清晰。  
> **这就引出了 Day 10 的代码重构。**

**Day 9 结束时你应该能：**

- 上传文档后立即返回，后台异步处理，前端显示进度
- 历史对话可恢复
- `docker-compose up` 包含所有服务

**技术栈：** Celery、Redis、PostgreSQL

---

### Day 10: 代码重构 — Approach Pattern + 适配器模式

**目标：** 按"检索策略"和"服务依赖"拆分代码，让系统可扩展。

**你要做的事：**

1. 实现 **Approach Pattern**（参考原项目的核心架构）：
   - 定义 `Approach` 基类：`run()` 和 `run_stream()` 方法
   - 实现 `ChatReadRetrieveRead`：Chat 模式的 approach（检索→读取→生成→可能再检索）
   - 实现 `RetrieveThenRead`：Ask 模式的 approach（检索→生成，一次性）
2. 实现 **适配器模式** 处理外部服务：
   - `SearchClient` 适配器：封装 Azure AI Search 调用，本地开发可切换为 ChromaDB
   - `LLMClient` 适配器：封装 Azure OpenAI / LangChain 调用，方便切换模型或 provider
   - `StorageClient` 适配器：封装 Blob Storage，本地可用文件系统
   - `EmbeddingClient` 适配器：封装 Embedding 调用（为 Day 11 引入本地模型做准备）
3. 拆分项目结构：`approaches/`、`clients/`、`routes/`、`models/`
4. 决定 LangChain 的边界：哪些用 LangChain（比如 Memory、prompt template），哪些自己写（比如 Approach 的编排逻辑）

**引导思考：**

> 拆完之后，如果老板说 "我们要加一个 GPT-4o with Vision 的 approach，可以看图片回答"——你只需要新增一个 `Approach` 子类就行了，不用改任何现有代码。  
> **这就是 Open-Closed Principle。**  
> 如果要把 Azure OpenAI 换成 Claude？只需要新增一个 `LLMClient` 适配器。  
> **这就是 Adapter Pattern 的价值。**
>
> 注意你拆出来的 `EmbeddingClient` 适配器——现在它只有一个 Azure OpenAI 实现。  
> 但 Azure OpenAI Embedding 是收费的，每次调用都花钱。  
> **问题**：如果你要处理大量文档，Embedding 成本会很高。如果网络不好，API 延迟还高。  
> **有没有可能跑一个本地的 Embedding 模型？** 不花钱、速度快、不依赖网络。  
> **这就是 Day 11 要做的——用 HuggingFace Sentence Transformers 跑本地 Embedding。**

**Day 10 结束时你应该能：**

- 代码按 Approach 和 Client 清晰分层
- 新增检索策略只需加一个类
- 所有功能照常工作，没拆坏

**设计模式：** Strategy Pattern（Approach）、Adapter Pattern（Clients）、Open-Closed Principle

---

### Day 11: HuggingFace + PyTorch — 本地 Embedding 和 Re-ranking ⭐ 新增

**目标：** 用开源模型替代/补充云端 API，理解 Transformer 模型在 RAG 中的实际应用。

**你要做的事：**

1. 安装 `sentence-transformers`（底层依赖 PyTorch + HuggingFace Transformers）
2. 实现 `LocalEmbeddingClient`（接入你 Day 10 拆出的 `EmbeddingClient` 适配器）：
   - 加载 `BAAI/bge-small-en-v1.5`（轻量级但效果好的开源 Embedding 模型）
   - 本地推理生成向量，不调任何 API
3. 实现本地 Cross-Encoder Re-ranker：
   - 加载 `cross-encoder/ms-marco-MiniLM-L-6-v2`
   - 在 Hybrid Search 返回候选 chunks 后，用 Cross-Encoder 对 (query, chunk) 逐对打分重排序
   - 这是一个用你自己控制的模型来替代 Azure Semantic Ranker 的方案
4. 前端 Settings 面板：新增 "Embedding 来源" 和 "Re-ranker 来源" 的切换选项（Azure vs Local）
5. 写一个简单的 benchmark 脚本：对比 Azure OpenAI Embedding vs 本地 BGE 的检索质量和速度

**引导思考：**

> 跑完 benchmark，你会发现：
>
> - 本地 BGE 模型在大多数英文查询上效果和 Azure OpenAI Embedding 差不多，但完全免费。
> - 本地 Cross-Encoder Re-ranker 效果也不差，但速度比 Azure Semantic Ranker 慢（因为要对每对 query-chunk 做推理）。
> - **Tradeoff**：云端 API 贵但快、开源模型免费但要自己管推理资源。
>
> 现在你对 Transformer 模型有了直观认知——它不是黑盒 API，你可以直接加载权重在本地跑。  
> **Bi-Encoder（Embedding 模型）** 和 **Cross-Encoder（Re-ranker 模型）** 的区别你现在也亲手体验了：
>
> - Bi-Encoder：query 和 doc 分别编码为向量，速度快但信息交互少
> - Cross-Encoder：query 和 doc 拼在一起输入，信息交互充分但速度慢（只能用于 re-ranking，不能用于全库检索）
>
> **思考**：你现在改了好多次 prompt、换了 embedding 模型、调了 re-ranker——但你怎么知道系统整体变好了还是变差了？  
> 你需要一个系统化的方式来**追踪每次 LLM 调用的完整链路**，以及**自动评估 RAG 质量**。  
> **这就是 Day 12 要引入 Langfuse 和 RAGAS 的原因。**

**Day 11 结束时你应该能：**

- Settings 面板可在 Azure OpenAI Embedding 和 Local BGE Embedding 之间切换
- Re-ranking 可选 Azure Semantic Ranker 或 Local Cross-Encoder
- 有 benchmark 数据对比两种方案的效果和速度

**技术栈：** PyTorch、HuggingFace Transformers、Sentence-Transformers

**AI 概念：** Bi-Encoder vs Cross-Encoder、开源 vs 闭源模型 Tradeoff、本地模型推理、Transfer Learning（预训练模型直接用于下游任务）

---

### Day 12: Langfuse 可观测性 + RAGAS 评估 ⭐ 新增

**目标：** 给你的 RAG 系统装上 "X 光机"——追踪每次调用链路，自动化评估回答质量。

**你要做的事：**

**Part 1: Langfuse 可观测性**

1. 部署 Langfuse（docker-compose 加一个 langfuse 服务，或用 Langfuse Cloud 免费版）
2. 在后端集成 Langfuse SDK：
   - 用 `@observe()` 装饰器或手动 `trace` 包裹你的 RAG 链路
   - 记录每次调用的：输入 query、检索到的 chunks、拼好的 prompt、LLM 输出、token 用量、延迟
   - 如果用了 LangChain，直接用 `CallbackHandler` 集成（一行代码）
3. 在 Langfuse Dashboard 中查看：
   - 每次对话的完整调用链路（Trace）
   - 每个步骤的耗时分解
   - Token 消耗和成本统计
   - 按时间段聚合的趋势图

**Part 2: RAGAS 评估**

4. 安装 `ragas`
5. 准备评估数据集：20-30 个 (question, ground_truth_answer) 对
6. 编写评估脚本：对每个问题跑完整 RAG pipeline → 收集 (question, answer, contexts, ground_truth) → 用 RAGAS 计算：
   - **Faithfulness**：回答是否忠实于检索到的 context（不编造）
   - **Answer Relevancy**：回答是否切题
   - **Context Precision**：检索到的 context 是否精准（没有太多无关内容）
   - **Context Recall**：检索到的 context 是否覆盖了回答所需的信息
7. 输出评估报告，结果也推送到 Langfuse 用于可视化追踪
8. 用评估结果回去优化：调 chunking 参数、改 prompt、换 retrieval 策略，再跑评估看分数变化

**引导思考：**

> 你现在可以做一个实验：  
> ① 把 Embedding 从 Azure OpenAI 切到本地 BGE → 跑 RAGAS → 看 Context Precision 和 Recall 有没有掉  
> ② 把 Re-ranker 从 Azure Semantic Ranker 切到本地 Cross-Encoder → 跑 RAGAS → 看 Faithfulness 有没有变  
> ③ 改一版 prompt → 跑 RAGAS → 看 Faithfulness 和 Answer Relevancy 有没有涨
>
> 这就是 **数据驱动的 RAG 优化**——不是凭感觉调参，而是有量化指标。  
> 在 Langfuse 里你可以对比不同配置下的 trace，看到每个步骤的详细输入输出。  
> **这是大厂 AI Engineer 的日常工作方式。**

**Day 12 结束时你应该能：**

- Langfuse Dashboard 上看到每次对话的完整调用链路
- RAGAS 评估脚本输出 Faithfulness / Relevancy / Precision / Recall 分数
- 能基于评估结果有针对性地优化 RAG pipeline

**技术栈：** Langfuse、RAGAS

**AI 概念：** LLM Observability（可观测性）、Trace / Span、RAG Evaluation、Faithfulness、Context Precision / Recall

---

### Day 13: Azure 部署 + Kubernetes + Terraform + CI/CD

**目标：** 部署到 Azure 生产环境，用 Kubernetes 编排，用 Terraform 管理基础设施，用 GitHub Actions 自动化。

**你要做的事：**

**Part 1: Terraform 基础设施**

1. 编写 Terraform 配置，创建所有 Azure 资源：
   - Azure Kubernetes Service (AKS)：容器编排平台
   - Azure Container Registry (ACR)：Docker 镜像仓库
   - Azure OpenAI：GPT + Embedding 模型
   - Azure AI Search：检索服务
   - Azure Blob Storage：文档存储
   - Azure Database for PostgreSQL：对话历史
   - Azure Cache for Redis：Celery 任务队列
2. `terraform plan` → `terraform apply` 一键创建所有资源

**Part 2: Kubernetes 部署**

3. 编写 Kubernetes 部署文件：
   - `deployment.yaml`：FastAPI 后端（2+ replicas）
   - `deployment.yaml`：Celery Worker（可独立扩缩）
   - `deployment.yaml`：React 前端（nginx 静态服务）
   - `service.yaml` + `ingress.yaml`：暴露服务
   - `configmap.yaml` / `secret.yaml`：配置管理
4. 理解 Kubernetes 的核心概念：Pod、Deployment、Service、Ingress、ConfigMap、Secret、HPA（Horizontal Pod Autoscaler）
5. 配置 HPA：根据 CPU/内存自动扩缩 Worker 数量

**Part 3: CI/CD**

6. 编写 GitHub Actions workflow：
   - Push to main → Build Docker images → Push to ACR → Deploy to AKS
   - 可选：PR 触发 RAGAS 评估（评估结果作为 PR comment）
7. 配置 Prometheus + Grafana 监控：
   - 从 K8s 集群收集指标
   - 自定义 Dashboard：请求延迟、LLM 调用耗时、检索延迟、token 消耗、Worker 队列深度、Pod 数量

**引导思考：**

> 对比 Day 3 的 Docker Compose 和今天的 Kubernetes——从 `docker-compose up` 到 `kubectl apply`。  
> **Docker Compose 的局限**：只能单机跑，不能自动扩缩容，没有健康检查和自动重启，服务发现靠 compose 网络。  
> **Kubernetes 解决了什么**：多节点调度、自动扩缩（HPA）、滚动更新（zero-downtime deploy）、服务发现和负载均衡、配置和密钥管理。  
> **在大厂**：Docker Compose 只用于本地开发，生产环境 100% 是 K8s（或 managed K8s 如 AKS/EKS/GKE）。
>
> 你终于有了一个完整的、从本地开发到生产部署的 AI 应用。  
> 每一个技术决策你都知道 "为什么" ——因为你是从问题出发，一步步引入解决方案的。

**Day 13 结束时你应该能：**

- 应用部署在 AKS 上，有公网 URL 可访问
- `terraform apply` 一键创建所有 Azure 资源
- GitHub push 触发自动部署到 AKS
- Grafana Dashboard 可看到集群和应用的关键指标
- Celery Worker 可根据负载自动扩缩

**技术栈：** Kubernetes (AKS)、Terraform、GitHub Actions、Prometheus + Grafana

---

## Part 3: 完成后你将实现什么

### 一个完整的企业级 RAG 知识库聊天应用：

**功能：**

- ✅ 用户通过 Web 界面上传 PDF 等文档，系统自动解析、分块、Embedding、索引
- ✅ 支持 Chat（多轮对话）和 Ask（单轮问答）两种模式
- ✅ 回答带 Citations，可点击查看原文来源
- ✅ 可展示 "思考过程"（检索了哪些内容、用了什么 prompt）
- ✅ 支持流式响应（SSE），逐字输出
- ✅ 支持 Text / Vector / Hybrid 三种检索策略，UI 可切换
- ✅ 支持 Semantic Ranker 对检索结果二次排序（Azure 或本地 Cross-Encoder 可选）
- ✅ 支持 Azure OpenAI Embedding 和本地 HuggingFace Embedding 可切换
- ✅ LLM 行为可配置（temperature、system prompt override、Few-shot）
- ✅ 对话历史持久化，可恢复历史对话
- ✅ 文档管理（上传、列表、删除），后台异步处理
- ✅ 完整的 LLM 调用链路可观测性（Langfuse）
- ✅ 自动化 RAG 质量评估（RAGAS）
- ✅ 容器化本地开发 + Kubernetes 生产部署

**技术能力：**

- ✅ 设计并实现完整的 RAG Pipeline（Ingestion + Retrieval + Generation）
- ✅ 使用 Embedding 模型做向量化，理解向量检索原理
- ✅ 实现 Hybrid Search（关键词 + 向量 + 语义重排序）
- ✅ 使用 LangChain 构建标准化 RAG Chain
- ✅ 使用 PyTorch + HuggingFace 加载开源模型做本地推理
- ✅ 使用 Cross-Encoder 做 Re-ranking，理解 Bi-Encoder vs Cross-Encoder
- ✅ 使用 Langfuse 做 LLM 可观测性，追踪完整调用链路
- ✅ 使用 RAGAS 做 RAG 质量评估，实现数据驱动优化
- ✅ 使用 Celery + Redis 做异步任务处理
- ✅ 使用 Adapter Pattern 封装多个外部服务依赖
- ✅ 使用 Strategy Pattern（Approach）支持多种问答策略
- ✅ 使用 Docker Compose 编排本地多服务开发环境
- ✅ 使用 Kubernetes (AKS) 编排生产环境，理解 Pod / Deployment / HPA
- ✅ 使用 Terraform 做基础设施即代码，部署到 Azure
- ✅ 使用 GitHub Actions 实现 CI/CD 自动化部署
- ✅ 配置 Prometheus + Grafana 做应用与集群监控

**面试能力：**

- ✅ 能解释 RAG 的完整流程：为什么需要 RAG、每一步在干什么、有什么 tradeoff
- ✅ 能回答 "为什么不直接把文档全塞给 LLM"（context window 限制、成本、准确性）
- ✅ 能解释 Embedding 是什么、为什么能做语义搜索、Bi-Encoder vs Cross-Encoder
- ✅ 能说清 Text Search vs Vector Search vs Hybrid Search 的区别和适用场景
- ✅ 能解释 Semantic Ranker / Re-ranking 的原理和价值
- ✅ 能回答 Chunking 策略的 tradeoff（chunk 太大→不精准、太小→丢上下文）
- ✅ 能解释 Hallucination 问题以及如何用 Grounding + Prompt Engineering 缓解
- ✅ 能讲出 LangChain 的优劣：快速原型 vs 生产自定义的 tradeoff
- ✅ 能解释开源 vs 闭源模型的 tradeoff（成本、延迟、隐私、效果）
- ✅ 能回答 LLM 可观测性为什么重要、Langfuse 追踪什么
- ✅ 能说清 RAGAS 的评估维度及其含义
- ✅ 能回答 Streaming 的实现方式（SSE）和 Citation 在 Stream 中的处理
- ✅ 能清楚说明 Approach Pattern / Strategy Pattern / Adapter Pattern 的设计动机
- ✅ 能说清楚异步任务处理的架构（为什么需要消息队列、Celery 的角色）
- ✅ 能解释 Docker Compose vs Kubernetes 的区别和适用场景
- ✅ 能讲出监控的关键指标和为什么需要 RAG 评估

---

### 技术栈总览

| 类别               | 技术                                                    | 用途                                          |
| ------------------ | ------------------------------------------------------- | --------------------------------------------- |
| 后端               | Python, FastAPI                                         | Web 框架, API 开发                            |
| 前端               | React, TypeScript                                       | Chat / Ask / Settings / 文档管理 UI           |
| LLM                | Azure OpenAI (GPT-4o-mini)                              | 对话生成                                      |
| Embedding（云端）  | Azure OpenAI (text-embedding-ada-002)                   | 云端文本向量化                                |
| Embedding（本地）  | HuggingFace Sentence-Transformers (BGE)                 | 本地文本向量化（PyTorch 推理）                |
| Re-ranking（本地） | HuggingFace Cross-Encoder (ms-marco-MiniLM)             | 本地检索结果重排序                            |
| ML 框架            | PyTorch, HuggingFace Transformers                       | 开源模型加载与推理                            |
| LLM 编排框架       | LangChain / LCEL                                        | RAG Chain 构建、Memory 管理、Prompt 模板      |
| 向量数据库         | ChromaDB                                                | 本地开发时的向量存储与检索                    |
| 搜索引擎           | Azure AI Search                                         | 生产环境 Hybrid Search + Semantic Ranker      |
| 文档存储           | Azure Blob Storage                                      | 上传文件的持久化存储                          |
| 文档解析           | PyMuPDF / Azure Document Intelligence                   | PDF 文本提取                                  |
| 异步任务           | Celery + Redis                                          | 后台文档处理任务                              |
| 数据库             | PostgreSQL                                              | 对话历史、文档元数据                          |
| 容器化             | Docker, Docker Compose                                  | 本地开发环境编排                              |
| 容器编排           | Kubernetes (AKS)                                        | 生产环境编排、自动扩缩                        |
| 云平台             | Azure (OpenAI, AI Search, AKS, Blob, PostgreSQL, Redis) | 全套云服务                                    |
| 基础设施           | Terraform                                               | 基础设施即代码                                |
| CI/CD              | GitHub Actions                                          | 自动化构建与部署                              |
| 应用监控           | Prometheus + Grafana                                    | 指标收集与可视化                              |
| LLM 可观测性       | Langfuse                                                | LLM 调用链路追踪、token/cost 分析             |
| RAG 评估           | RAGAS                                                   | Faithfulness / Relevancy / Precision / Recall |
| 测试               | pytest                                                  | 单元测试、集成测试                            |

### 涉及的 AI Engineer 核心概念

| 概念                                 | 在项目中的体现                                                              |
| ------------------------------------ | --------------------------------------------------------------------------- |
| RAG (Retrieval-Augmented Generation) | 整个项目的核心架构模式                                                      |
| Embedding / 向量化                   | 文档 chunk 和用户 query 的向量表示（Azure OpenAI + HuggingFace 双实现）     |
| Vector Search                        | 基于 cosine similarity 的语义检索                                           |
| Hybrid Search                        | 关键词 + 向量混合检索，RRF 融合排序                                         |
| Semantic Ranking / Re-ranking        | Azure Semantic Ranker + 本地 Cross-Encoder 双实现                           |
| Bi-Encoder vs Cross-Encoder          | Embedding 模型（快但弱交互） vs Re-ranker 模型（慢但强交互）                |
| Chunking 策略                        | 固定大小 + overlap / 语义分块                                               |
| Prompt Engineering                   | System prompt、Few-shot Learning、Chain-of-Thought                          |
| Hallucination / Grounding            | 通过 prompt 约束 + RAGAS 评估检测幻觉                                       |
| Streaming (SSE)                      | 流式响应提升用户体验                                                        |
| LLM Observability                    | Langfuse 追踪 Trace / Span / Token / Cost                                   |
| RAG Evaluation                       | RAGAS: Faithfulness / Answer Relevancy / Context Precision / Context Recall |
| Token 管理                           | LangChain Memory（滑动窗口 / Summarization）控制 context 长度               |
| Document Intelligence                | 结构化文档解析（表格、标题提取）                                            |
| Transfer Learning                    | 使用预训练 Transformer 模型直接做 Embedding / Re-ranking                    |
| 开源 vs 闭源模型 Tradeoff            | 成本、延迟、隐私、效果的权衡                                                |

### 涉及的设计模式

| 模式                        | 在项目中的体现                                                          |
| --------------------------- | ----------------------------------------------------------------------- |
| Strategy Pattern (Approach) | Chat 和 Ask 使用不同的 Approach 类                                      |
| Adapter Pattern             | SearchClient / LLMClient / EmbeddingClient / StorageClient 封装外部依赖 |
| Open-Closed Principle       | 新增 Approach、Client、Embedding Provider 不需修改现有代码              |
| Repository Pattern          | 数据库操作抽象为 Repository 层                                          |
| Pipeline Pattern            | Ingestion 流水线：解析 → 分块 → Embedding → 索引                        |
| Observer / Polling          | 前端轮询后台任务状态                                                    |

### ATS 技术栈覆盖度

| Tier      | 技术关键词                        | 是否覆盖                       |
| --------- | --------------------------------- | ------------------------------ |
| 🔴 Tier 1 | Python                            | ✅ FastAPI 后端                |
| 🔴 Tier 1 | PyTorch                           | ✅ Day 11 本地模型推理         |
| 🔴 Tier 1 | LLM, RAG, Transformer, NLP        | ✅ 整个项目核心                |
| 🔴 Tier 1 | FastAPI / REST API                | ✅ Day 1 起                    |
| 🔴 Tier 1 | Docker, Kubernetes                | ✅ Docker Day 3 起, K8s Day 13 |
| 🔴 Tier 1 | Azure (云平台)                    | ✅ Day 2 起                    |
| 🔴 Tier 1 | Git                               | ✅ 全程                        |
| 🟠 Tier 2 | LangChain                         | ✅ Day 7                       |
| 🟠 Tier 2 | ChromaDB (向量数据库)             | ✅ Day 2                       |
| 🟠 Tier 2 | OpenAI Embeddings, HuggingFace    | ✅ Day 2 (Azure), Day 11 (HF)  |
| 🟠 Tier 2 | Prometheus + Grafana              | ✅ Day 13                      |
| 🟠 Tier 2 | RAGAS (Eval 框架)                 | ✅ Day 12                      |
| 🟠 Tier 2 | HuggingFace Transformers          | ✅ Day 11                      |
| 🟡 Tier 3 | PostgreSQL, Redis                 | ✅ Day 9                       |
| 🟡 Tier 3 | Prompt Engineering, CoT, Few-shot | ✅ Day 8                       |
| 🟡 Tier 3 | GitHub Actions CI/CD              | ✅ Day 13                      |
| 🟡 Tier 3 | Langfuse (LLM 可观测性)           | ✅ Day 12                      |
