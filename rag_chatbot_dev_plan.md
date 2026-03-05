# RAG Chatbot 开发计划

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

### 10. Docker 容器化
- 后端 + 前端 + 依赖服务（向量数据库等）通过 Docker Compose 一键启动

### 11. Azure 云部署
- 使用 Terraform / Bicep 实现基础设施即代码
- 部署到 Azure App Service / Container Apps
- 使用 Azure OpenAI、Azure AI Search、Azure Blob Storage

### 12. 监控与评估
- 应用性能监控（Application Insights / 日志）
- RAG 质量评估：groundedness、relevance、coherence 指标

---

## Part 2: 10 天开发计划

> **原则**：每一天结束时都能跑起来。从最简单的实现出发，通过"发现问题→引入新概念"的方式逐步升级。Azure 资源从 Day 1 就开始接触，而不是最后才引入。

### 你目前已有的基础

你的 `rag_chatbot` repo 已经有：一个 FastAPI 后端、SQLite 存储、本地 `policy.txt` 作为文档源、简单的文本切分写入 `docs` 表、一个 `POST /v1/chat/answer` 接口调用 OpenAI 生成回答。本质上是一个**最简 RAG MVP**：把文本塞进 prompt context → 让 LLM 回答。

---

### Day 1: 搞清楚 "RAG 到底在干什么"

**目标：**  在你现有 MVP 上，亲手体验 RAG 的核心流程，理解每一步在做什么。

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
> **Day 8 会用 Celery / 后台任务来处理这个问题。**

**Day 6 结束时你应该能：**
- 从前端上传 PDF，等处理完后就能对它提问
- 文档管理页面可以查看、删除文档
- 文档存在 Azure Blob Storage，索引在 Azure AI Search

**技术栈：** Azure Blob Storage、Azure Document Intelligence（可选）

---

### Day 7: Prompt Engineering + LLM 行为调优

**目标：** 深入优化 Prompt 设计，让 LLM 回答质量显著提升。

**你要做的事：**

1. 设计结构化的 System Prompt：
   - 角色设定（"你是一个企业知识库助手"）
   - 回答规则（必须基于提供的 context、不知道就说不知道、不要编造）
   - Citation 格式规则（用 `[doc1]` 标注引用）
2. 实现 Prompt 模板系统：用 Jinja2 或 f-string 模板管理不同场景的 prompt
3. 前端 Settings：可自定义 system prompt override、调节 temperature、设置 "排除类别" 过滤
4. 处理多轮对话的 history 管理：实现滑动窗口（只保留最近 N 轮）或 history summarization（用 LLM 压缩历史）

**引导思考：**

> 试试故意问一个文档里没有的问题。你的系统是不是瞎编了答案？  
> **问题**：这就是 "幻觉"（Hallucination）。  
> **解法**：在 prompt 中明确指示 "如果提供的 context 中没有相关信息，回答 '我不知道'"。  
> 但这只是 prompt 层面的约束，不能 100% 杜绝幻觉。**这就引出了 RAG 评估的需求——Day 10 会做。**

**Day 7 结束时你应该能：**
- LLM 回答质量明显提升，不乱编
- Settings 面板可调 temperature、prompt override
- 多轮对话不再 token 超限

**AI 概念：** Prompt Engineering、Hallucination（幻觉）、Grounding、System Prompt 设计、Token 管理

---

### Day 8: 后台任务 + Chat History 持久化

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

> 现在你的 Docker Compose 文件里有：FastAPI、React、ChromaDB（虽然已被 Azure AI Search 替代但本地开发可能还在用）、Redis、Celery Worker、PostgreSQL。  
> 服务越来越多了。想一想：**你的代码结构还合理吗？**  
> 检索逻辑、LLM 调用逻辑、文档处理逻辑是不是全挤在一起了？  
> **这就引出了 Day 9 的代码重构。**

**Day 8 结束时你应该能：**
- 上传文档后立即返回，后台异步处理，前端显示进度
- 历史对话可恢复
- `docker-compose up` 包含所有服务

**技术栈：** Celery、Redis、PostgreSQL

---

### Day 9: 代码重构 — Approach Pattern + 适配器模式

**目标：** 按"检索策略"和"服务依赖"拆分代码，让系统可扩展。

**你要做的事：**

1. 实现 **Approach Pattern**（参考原项目的核心架构）：
   - 定义 `Approach` 基类：`run()` 和 `run_stream()` 方法
   - 实现 `ChatReadRetrieveRead`：Chat 模式的 approach（检索→读取→生成→可能再检索）
   - 实现 `RetrieveThenRead`：Ask 模式的 approach（检索→生成，一次性）
2. 实现 **适配器模式** 处理外部服务：
   - `SearchClient` 适配器：封装 Azure AI Search 调用，本地开发可切换为 ChromaDB
   - `LLMClient` 适配器：封装 Azure OpenAI 调用，方便切换模型或 provider
   - `StorageClient` 适配器：封装 Blob Storage，本地可用文件系统
3. 拆分项目结构：`approaches/`、`clients/`、`routes/`、`models/`

**引导思考：**

> 拆完之后，如果老板说 "我们要加一个 GPT-4o with Vision 的 approach，可以看图片回答"——你只需要新增一个 `Approach` 子类就行了，不用改任何现有代码。  
> **这就是 Open-Closed Principle。**  
> 如果要把 Azure OpenAI 换成 Claude？只需要新增一个 `LLMClient` 适配器。  
> **这就是 Adapter Pattern 的价值。**

**Day 9 结束时你应该能：**
- 代码按 Approach 和 Client 清晰分层
- 新增检索策略只需加一个类
- 所有功能照常工作，没拆坏

**设计模式：** Strategy Pattern（Approach）、Adapter Pattern（Clients）、Open-Closed Principle

---

### Day 10: Azure 部署 + 监控 + RAG 评估

**目标：** 部署到 Azure，加监控，加 RAG 质量评估。

**你要做的事：**

1. **Terraform 基础设施：**
   - Azure App Service / Container Apps（部署后端 + 前端）
   - Azure OpenAI（GPT + Embedding 模型）
   - Azure AI Search
   - Azure Blob Storage
   - Azure Cosmos DB（Chat History，替换 PostgreSQL）或继续用 PostgreSQL on Azure
2. **CI/CD：**
   - GitHub Actions：push 后自动 build Docker image → deploy 到 Azure
3. **监控：**
   - 集成 Application Insights：记录每次请求的 latency、token usage、error
   - 或自建 Prometheus + Grafana 仪表盘
   - 关键指标：请求延迟、LLM 调用耗时、检索延迟、token 消耗、错误率
4. **RAG 评估：**
   - 准备测试问答对（question + expected_answer）
   - 实现评估脚本：对每个问题跑 RAG pipeline → 用 LLM 评判：
     - **Groundedness**：回答是否基于 context
     - **Relevance**：回答是否切题
     - **Coherence**：回答是否通顺
   - 输出评估报告

**引导思考：**

> 部署完之后，你终于有了一个完整的生产级 RAG 应用。  
> 但看看你的监控面板：某些问题的回答延迟特别高。为什么？  
> 可能是检索慢（index 太大）、可能是 LLM 慢（prompt 太长）、可能是网络延迟。  
> **有了监控数据，你才能做出有依据的优化决策，而不是瞎猜。**

**Day 10 结束时你应该能：**
- 应用部署在 Azure 上，有公网 URL 可访问
- GitHub push 触发自动部署
- 监控仪表盘可看到关键指标
- RAG 评估脚本输出质量报告

**技术栈：** Terraform、GitHub Actions、Application Insights / Prometheus + Grafana

---

## Part 3: 完成后你将实现什么

### 一个完整的企业级 RAG 知识库聊天应用：

**功能：**
- 用户通过 Web 界面上传 PDF 等文档，系统自动解析、分块、Embedding、索引
- 支持 Chat（多轮对话）和 Ask（单轮问答）两种模式
- 回答带 Citations，可点击查看原文来源
- 可展示 "思考过程"（检索了哪些内容、用了什么 prompt）
- 支持流式响应，逐字输出
- 支持 Text / Vector / Hybrid 三种检索策略，UI 可切换
- 支持 Semantic Ranker 对检索结果二次排序
- LLM 行为可配置（temperature、system prompt override）
- 对话历史持久化，可恢复历史对话
- 文档管理（上传、列表、删除）
- 容器化一键启动，可部署到 Azure 云

**技术能力：**
- 设计并实现完整的 RAG Pipeline（Ingestion + Retrieval + Generation）
- 使用 Embedding 模型做向量化，理解向量检索原理
- 实现 Hybrid Search（关键词 + 向量 + 语义重排序）
- 使用 Celery + Redis 做异步任务处理
- 使用 Adapter Pattern 封装多个外部服务依赖
- 使用 Strategy Pattern（Approach）支持多种问答策略
- 使用 Docker Compose 编排多服务开发环境
- 使用 Terraform 做基础设施即代码，部署到 Azure
- 使用 GitHub Actions 实现 CI/CD 自动化部署
- 配置 Application Insights / Prometheus + Grafana 做应用监控
- 实现 RAG 质量评估（Groundedness、Relevance、Coherence）

**面试能力：**
- 能解释 RAG 的完整流程：为什么需要 RAG、每一步在干什么、有什么 tradeoff
- 能回答 "为什么不直接把文档全塞给 LLM"（context window 限制、成本、准确性）
- 能解释 Embedding 是什么、为什么能做语义搜索
- 能说清 Text Search vs Vector Search vs Hybrid Search 的区别和适用场景
- 能解释 Semantic Ranker / Re-ranking 的原理和价值
- 能回答 Chunking 策略的 tradeoff（chunk 太大→不精准、太小→丢上下文）
- 能解释 Hallucination 问题以及如何用 Grounding + Prompt Engineering 缓解
- 能回答 Streaming 的实现方式（SSE）和 Citation 在 Stream 中的处理
- 能清楚说明 Approach Pattern / Strategy Pattern / Adapter Pattern 的设计动机
- 能说清楚异步任务处理的架构（为什么需要消息队列、Celery 的角色）
- 能讲出监控的关键指标和为什么需要 RAG 评估

---

### 技术栈总览

| 类别 | 技术 | 用途 |
|------|------|------|
| 后端 | Python, FastAPI | Web 框架, API 开发 |
| 前端 | React, TypeScript | Chat / Ask / Settings / 文档管理 UI |
| LLM | Azure OpenAI (GPT-4o-mini) | 对话生成 |
| Embedding | Azure OpenAI (text-embedding-ada-002) | 文本向量化 |
| 向量数据库（本地） | ChromaDB | 本地开发时的向量存储与检索 |
| 搜索引擎（生产） | Azure AI Search | Hybrid Search + Semantic Ranker |
| 文档存储 | Azure Blob Storage | 上传文件的持久化存储 |
| 文档解析 | PyMuPDF / Azure Document Intelligence | PDF 文本提取 |
| 异步任务 | Celery + Redis | 后台文档处理任务 |
| 数据库 | PostgreSQL | 对话历史、文档元数据 |
| 容器化 | Docker, Docker Compose | 本地开发环境编排 |
| 云部署 | Azure App Service / Container Apps | 生产环境部署 |
| 基础设施 | Terraform | 基础设施即代码 |
| CI/CD | GitHub Actions | 自动化构建与部署 |
| 监控 | Application Insights / Prometheus + Grafana | 指标收集与可视化 |
| 测试 | pytest | 单元测试、集成测试 |

### 涉及的 AI Engineer 核心概念

| 概念 | 在项目中的体现 |
|------|----------------|
| RAG (Retrieval-Augmented Generation) | 整个项目的核心架构模式 |
| Embedding / 向量化 | 文档 chunk 和用户 query 的向量表示 |
| Vector Search | 基于 cosine similarity 的语义检索 |
| Hybrid Search | 关键词 + 向量混合检索，RRF 融合排序 |
| Semantic Ranking / Re-ranking | Azure AI Search 的 cross-encoder 二次排序 |
| Chunking 策略 | 固定大小 + overlap / 语义分块 |
| Prompt Engineering | System prompt 设计、Few-shot、指令约束 |
| Hallucination / Grounding | 通过 prompt 约束 + evaluation 检测幻觉 |
| Streaming (SSE) | 流式响应提升用户体验 |
| RAG Evaluation | Groundedness / Relevance / Coherence 指标 |
| Token 管理 | 滑动窗口 / History Summarization 控制 context 长度 |
| Document Intelligence | 结构化文档解析（表格、标题提取） |

### 涉及的设计模式

| 模式 | 在项目中的体现 |
|------|----------------|
| Strategy Pattern (Approach) | Chat 和 Ask 使用不同的 Approach 类 |
| Adapter Pattern | SearchClient / LLMClient / StorageClient 封装外部依赖 |
| Open-Closed Principle | 新增 Approach 或 Client 不需修改现有代码 |
| Repository Pattern | 数据库操作抽象为 Repository 层 |
| Pipeline Pattern | Ingestion 流水线：解析 → 分块 → Embedding → 索引 |
| Observer / Polling | 前端轮询后台任务状态 |
