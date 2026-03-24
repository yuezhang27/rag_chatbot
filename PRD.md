# RAG Chatbot 开发计划

> 以 ADR v2 为准更新。主要变更：范围收窄为 HR 问答 POC，移除多项过度设计功能，简化部署方案。

---

## Part 1: PRD Lite — 功能清单

### 1. 文档上传（手动脚本）

- 通过 `scripts/prepdocs.py` 手动按需运行，不提供 UI 上传界面
- 脚本执行完整 Ingestion Pipeline：PDF 解析 → 分块 → Embedding → 写入向量数据库
- 原始 PDF 保留在公司内部，不迁移云端

> **变更原因（ADR）：** HR 文档极少更新（数月甚至一年一次），专门做上传 UI 的 ROI 为负；POC 核心价值在检索和生成，不在上传体验。

### 2. 文档处理流水线（Ingestion Pipeline）

- PDF 解析：PyMuPDF 提取纯文本（无需 OCR，HR 文档均为电子 PDF）
- 文本分块：固定大小 + overlap（chunk size=400 tokens，overlap=80 tokens）
- 向量化：batch 调用 Azure OpenAI text-embedding-ada-002（每批 20 个 chunk）
- 索引写入：本地写 ChromaDB，生产写 Azure AI Search

> **变更原因（ADR）：** 语义分块/按标题分块 POC 阶段 YAGNI；PyMuPDF 满足需求，无需 Azure Document Intelligence；batch 20 由 API token 上限倒推，减少网络往返。

### 3. Chat 对话（多轮）

- 用户输入问题，系统基于文档内容生成回答
- 支持多轮对话，上下文历史由前端维护，每次请求全量传给后端
- 每条回答附带 citations（文件名 + 页码，不链接原始 PDF）
- 对话历史存储在 React state，刷新即清空（不支持跨 session 记忆）

> **变更原因（ADR）：** 预计 10 轮以内，token 完全塞得下，无需滑动窗口或 Summarization；员工不需要跨 session 记忆，前端维护 history 最简单，后端无需读数据库查历史。

### 4. Ask 单轮问答

- 本质是 history=[] 的 Chat 请求，走同一个后端接口
- 不保留上下文

> **变更原因（ADR）：** Chat 和 Ask 后端处理无本质差异，拆成两个接口是过度设计；Ask = history 为空的 Chat，同一套逻辑自然退化。

### 5. 检索策略

- 本地开发（ChromaDB）：纯向量检索
- 生产（Azure AI Search）：Hybrid Search（BM25 + 向量 + RRF）粗排取 top-20，再经 Semantic Ranker 精排取 top-5
- **不提供 UI 切换检索策略**

> **变更原因（ADR）：** UI 切换检索策略是原项目的教学功能，POC 面向真实用户，策略由工程决定，无需暴露给用户；生产固定使用 Hybrid + Semantic Ranker 最优配置。

### 6. LLM 回答

- 模型：Azure OpenAI gpt-4o-mini（低创造性、高准确性场景，成本优先）
- **不提供 temperature 调节、system prompt override、检索策略切换等配置 UI**

> **变更原因（ADR）：** POC 阶段 YAGNI；LLM 行为配置由工程团队管理，无需暴露给 HR 员工。

### 7. Streaming 流式响应（SSE）

- SSE 协议：先发 `citation_data` event，再持续发 `response_text` event
- 前端可在 LLM 开始生成前提前渲染引用面板

> **变更原因（ADR）：** 先发 citation 可提前渲染引用面板；两种数据类型分开传输清晰。

### 8. 对话记录存档（仅生产环境）

- 本地开发阶段跳过（YAGNI）
- 生产环境：每次请求以 conversation_id 为文件名覆盖写入 Azure Blob Storage，供开发团队调试，不向员工暴露
- **不向员工提供历史对话查看功能**

> **变更原因（ADR）：** 员工不登录，存档只供调试；覆盖写入天然得到完整对话，无需检测对话结束；无需 PostgreSQL。

### 9. API 设计

- 单一接口，请求结构：`{ conversation_id?, message, history[] }`
- 首次请求不带 conversation_id，后端生成 UUID 返回
- 后端只写（存档），不读历史

### 10. React 前端

- Chat 界面（多轮对话 + 流式输出）
- Ask 界面（单轮问答）
- Citation 面板（引用来源展示，文件名 + 页码）
- **不含**：Settings 面板、文档管理页面、历史对话侧边栏

### 11. 本地开发环境

- Docker Compose：FastAPI + React + ChromaDB 一键启动

### 12. 生产部署

- Azure Container Apps（托管服务，屏蔽 Kubernetes 复杂度）
- **不使用 AKS，不引入 Terraform，不配置 CI/CD**（POC 阶段 YAGNI）

> **变更原因（ADR）：** POC 200 人并发，Container Apps 够用；AKS + Terraform + GitHub Actions 是过度设计。

### 13. LLM 可观测性与 RAG 评估

- Langfuse：追踪每次 LLM 调用的 input/output/latency/token/cost
- RAGAS：评估维度为 Faithfulness、Answer Relevancy、Context Precision
  - 测试集：复用已确认的 30 个问题 + 标准答案
  - 使用时机：本地每次改动后跑；pre 阶段正式跑一次；上线后不跑
- Azure Application Insights：监控请求量、响应时长、错误率
- Thumbs Down 用户反馈按钮：捕捉用户主观感受

> **变更原因（ADR）：** Prometheus + Grafana 替换为 Azure Application Insights（Azure 原生，零配置）；Context Recall 不在核心评估维度（ADR 未提及）。

---

## Part 2: 开发计划（精简版）

> **原则**：每天结束能跑，从问题出发逐步引入技术。聚焦 POC 核心价值：检索质量 + 生成质量 + 可观测性。

### Day 1：理解 RAG 基本流程

**目标：** 在现有 MVP 上体验 RAG 核心流程，理解每一步在做什么。

1. 准备 3-5 个 HR 相关 PDF（员工手册、福利政策等）
2. 写 `scripts/prepdocs.py`：读取 PDF → 提取文本 → 固定大小切分 → 存入 SQLite
3. 修改 `/v1/chat/answer`：从 SQLite 用 `LIKE` 关键词搜索相关 chunks → 拼进 prompt → 调 OpenAI 生成回答
4. 回答中返回 citations（引用了哪些 chunk）

**引导思考：**

> 试着问 "What is the dental coverage?" 再问 "Tell me about vision benefits"。  
> 你会发现 `LIKE` 搜不到 "teeth cleaning reimbursement"。  
> **关键词搜索无法理解语义——这就是为什么需要 Vector Search。**

**Day 1 结束：** `prepdocs.py` 跑通，`curl POST /v1/chat/answer` 能基于文档回答

**AI 概念：** RAG 基本流程、文档 Chunking、Prompt Stuffing

---

### Day 2：向量检索——让搜索"懂语义"

**目标：** 引入 Embedding + ChromaDB，替换关键词搜索。

1. 注册 Azure OpenAI，部署 `text-embedding-ada-002` 和 `gpt-4o-mini`
2. 修改 `prepdocs.py`：对每个 chunk 调 Embedding API → 拿到向量；batch size=20（400 tokens × 20 = 8000，在 8191 上限内）
3. 引入 ChromaDB，存储 chunk 文本 + 向量
4. 修改检索逻辑：问题 → 向量化 → ChromaDB cosine similarity 检索 → top-K chunks

**引导思考：**

> "dental coverage" 现在能搜到 "teeth cleaning" 了！  
> 但试试 "policy number ABC-123"——纯向量搜不到精确匹配。  
> **向量搜索和关键词搜索各有盲区——这就是为什么生产要用 Hybrid Search（Day 5）。**

**Day 2 结束：** 向量检索跑通，效果明显优于 Day 1

**AI 概念：** Embedding、向量空间、Cosine Similarity、Vector Database

---

### Day 3：Chat 前端 + 多轮对话

**目标：** React 前端，多轮对话，Citation 展示，Docker Compose。

1. React + TypeScript 搭建前端：消息气泡、输入框、发送按钮
2. 后端加 conversation history 支持：前端每次请求带完整 history，后端直接传给 LLM
3. Citation 展示：每条回答下方显示文件名 + 页码，可展开查看原文片段
4. Docker Compose：FastAPI + React + ChromaDB 一键启动

**API 设计确认：**

- 请求结构：`{ conversation_id?, message, history[] }`
- 首次请求不带 conversation_id，后端生成 UUID 返回
- History 由前端维护，刷新清空（无需持久化）

**引导思考：**

> 多轮对话直接把所有历史塞给 LLM——本项目预计 10 轮以内，token 完全塞得下，无需滑动窗口或 Summarization。  
> **HR 问答场景对话轮数有限，为不存在的问题过度设计是大忌。**

**Day 3 结束：** 浏览器打开能多轮对话，带 citations，`docker-compose up` 一键启动

**技术栈：** React、TypeScript、Docker Compose

---

### Day 4：SSE 流式响应 + Ask 单轮问答

**目标：** 逐字输出；Ask 模式。

1. 后端改为 SSE：FastAPI `StreamingResponse` + OpenAI `stream=True`
2. **SSE 协议**：先发 `citation_data` event（检索结果已知），再持续发 `response_text` event
3. 前端：`EventSource` 逐字渲染；提前渲染引用面板（citation_data 先到）
4. Ask 页面：前端传 `history=[]`，走同一个后端接口，无需新建接口

**引导思考：**

> Streaming 时 citation 如何处理？  
> **答案：先发 citation_data event，前端提前渲染引用面板；再流式发 response_text。两种数据类型分开传输，清晰且体验好。**

**Day 4 结束：** Chat 逐字流式输出；Ask 单轮问答；引用面板在 LLM 开始生成前已渲染

**AI 概念：** SSE、Streaming、Prompt Engineering

---

### Day 5：Hybrid Search + Azure AI Search

**目标：** 升级到生产检索策略。

1. 创建 Azure AI Search 资源
2. 实现 `AzureAISearchClient`（Adapter Pattern），封装检索的 CRUD；`ChromaDBSearchClient` 本地继续用
3. 修改 `prepdocs.py`：将 chunks 推送到 Azure AI Search Index（文本字段 + 向量字段）
4. 生产检索策略：Hybrid Search（BM25 + 向量 + RRF）→ top-20；Semantic Ranker 精排 → top-5
5. **不做 UI 切换**，检索策略写死在配置里

**Adapter Pattern：**

```
SearchClient（抽象接口）
├── ChromaDBSearchClient（本地开发）
└── AzureAISearchClient（生产）
```

切换只需改环境变量，上层代码不动。

**引导思考：**

> 对比 Day 2 的纯向量检索：Hybrid Search 对 "dental coverage" 和 "policy number ABC-123" 都能搜到了。  
> **Hybrid = 关键词的精确性 + 向量的语义理解，RRF 解决不同量纲合并问题（统一转排名再融合）。**

**Day 5 结束：** 生产检索走 Azure AI Search Hybrid + Semantic Ranker；本地仍用 ChromaDB；Adapter 切换无需改上层代码

**AI 概念：** Hybrid Search、BM25、RRF、Semantic Ranker（Azure 内置 Cross-Encoder 精排）、Adapter Pattern

---

### Day 6：Prompt Engineering + LLM 回答质量优化

**目标：** 系统化优化 Prompt，压制幻觉，提升回答质量。

1. 设计结构化 System Prompt：
   - 角色设定（HR 知识库助手）
   - 回答规则（必须基于提供的 context，不知道就说不知道）
   - Citation 格式规则
2. 实现 Chain-of-Thought 引导：先分析 context 有哪些相关信息，再组织回答
3. 用 `prepdocs.py` 的 chunk size（400/80 overlap）跑一轮手动测试，验证检索质量

**引导思考：**

> 故意问一个文档里没有的问题——系统编造了答案吗？  
> **Prompt 约束可以缓解幻觉，但无法 100% 杜绝——这就是为什么需要 RAGAS 自动评估（Day 8）。**  
> 你改了好几版 prompt，但怎么知道哪版更好？凭感觉不够——需要量化指标。

**Day 6 结束：** LLM 回答质量明显提升；有结构化 System Prompt

**AI 概念：** Prompt Engineering、Chain-of-Thought、Hallucination、Grounding

---

### Day 7：Langfuse 可观测性

**目标：** 追踪每次 LLM 调用链路，看到完整的 input/output/token/latency。

1. 部署 Langfuse（docker-compose 加一个 langfuse 服务，或用 Langfuse Cloud 免费版）
2. 后端集成 Langfuse SDK：用 `@observe()` 装饰器包裹 RAG 链路
   - 记录：输入 query、检索到的 chunks、拼好的 prompt、LLM 输出、token 用量、延迟
3. 在 Langfuse Dashboard 中查看：完整调用链路（Trace）、每步耗时分解、token 成本
4. Azure Application Insights：监控请求量、响应时长、错误率（Azure 原生零配置接入）

> **为什么不用 Prometheus + Grafana？** Application Insights 在 Azure 原生，零配置，不需要额外部署和维护，YAGNI。

**引导思考：**

> 在 Langfuse 里可以对比不同 prompt 版本的完整调用链路——这是数据驱动优化的基础。  
> **大厂 AI Engineer 的日常：改一版 prompt → 看 trace → 用 RAGAS 量化 → 再改。**

**Day 7 结束：** Langfuse Dashboard 上能看到每次对话完整链路；Application Insights 接入

**技术栈：** Langfuse、Azure Application Insights

**AI 概念：** LLM Observability、Trace/Span、Token 成本分析

---

### Day 8：RAGAS 评估 + 参数优化

**目标：** 自动化评估 RAG 质量，数据驱动优化 chunking 和检索参数。

1. 安装 `ragas`
2. 测试集：复用已确认的 30 个问题 + 标准答案作为 ground_truth
3. 编写评估脚本：对每个问题跑完整 RAG pipeline → 收集 (question, answer, contexts, ground_truth)
4. RAGAS 计算三个维度：
   - **Faithfulness**：回答是否忠实于检索到的 context（不编造）
   - **Answer Relevancy**：回答是否切题
   - **Context Precision**：检索到的 context 是否精准
5. 基于评估结果优化：
   - Context Precision 低 → 调小 chunk size 重新跑
   - Faithfulness 低 → 改 Prompt 再跑
   - Answer Relevancy 低 → 检查检索策略

**引导思考：**

> 有了 RAGAS 数据，你的每次改动都有量化依据：  
> "chunk size 从 400 → 300，Context Precision 从 0.72 → 0.81"——这比"感觉好像好一点"靠谱多了。  
> **这就是 pre 阶段正式跑一次评估给业务方看的核心价值。**

**Day 8 结束：** RAGAS 评估脚本跑通，三个维度有基准分数；能基于数据调参

**技术栈：** RAGAS

**AI 概念：** RAG Evaluation、Faithfulness、Answer Relevancy、Context Precision

---

### Day 9：Azure Container Apps 生产部署

**目标：** 部署到 Azure 生产环境，200 人并发可用。

1. 构建 Docker 镜像，推送到 Azure Container Registry（ACR）
2. 部署到 Azure Container Apps（FastAPI 后端 + React 前端）
3. 生产环境连接 Azure AI Search + Azure OpenAI + Azure Blob Storage（对话存档）
4. 接入 Azure Application Insights（生产监控）
5. 接入 Langfuse（生产 LLM 链路追踪）
6. 配置 Thumbs Down 用户反馈按钮（捕捉用户主观满意度）

**为什么不用 AKS？**

> POC 200 人并发，Azure Container Apps 托管服务够用，屏蔽了 Kubernetes 的配置复杂度。  
> AKS + Terraform + CI/CD 是 POC 阶段不需要的复杂度——等 POC 验证成功后再引入。  
> **YAGNI 原则：不为不存在的需求做设计。**

**Day 9 结束：** 应用部署在 Azure Container Apps，有公网 URL；三层监控（Application Insights + Langfuse + Thumbs Down）接入

**技术栈：** Azure Container Apps、Azure Container Registry

---

## Part 3: 完成后你将实现什么

### 一个面向真实用户的 HR RAG 问答系统：

**功能：**

- ✅ 手动脚本处理 HR PDF 文档，自动解析、分块、Embedding、索引
- ✅ Chat（多轮对话）和 Ask（单轮问答）两种模式，走同一接口
- ✅ 回答带 Citations（文件名 + 页码）
- ✅ 流式响应（SSE），先发 citation_data，再流式发 response_text
- ✅ 对话存档到 Azure Blob Storage（覆盖写入，供调试）
- ✅ 完整 LLM 链路可观测性（Langfuse）
- ✅ 自动化 RAG 质量评估（RAGAS，三维度）
- ✅ 服务层监控（Azure Application Insights）
- ✅ 用户满意度反馈（Thumbs Down）

**技术能力：**

- ✅ 设计并实现完整 RAG Pipeline（Ingestion + Retrieval + Generation）
- ✅ 使用 Embedding 做向量化，理解向量检索原理
- ✅ 实现 Hybrid Search（BM25 + 向量 + RRF）+ Semantic Ranker 精排
- ✅ 理解 Chunking 策略的 tradeoff，用 RAGAS 数据驱动参数优化
- ✅ 使用 Langfuse 做 LLM 可观测性，追踪完整调用链路
- ✅ 使用 RAGAS 做 RAG 质量评估，实现数据驱动优化
- ✅ Adapter Pattern 封装向量数据库（ChromaDB ↔ Azure AI Search 无缝切换）
- ✅ Docker Compose 本地多服务开发环境
- ✅ Azure Container Apps 生产部署

**面试能力：**

- ✅ 能解释 RAG 完整流程：为什么需要、每步在做什么、有什么 tradeoff
- ✅ 能回答 "为什么不直接把文档全塞给 LLM"（context window 限制、成本、准确性）
- ✅ 能解释 Embedding 是什么，为什么能做语义搜索
- ✅ 能说清 Text Search vs Vector Search vs Hybrid Search 的区别和适用场景
- ✅ 能解释 Semantic Ranker / Re-ranking 的原理（粗排 + 精排两阶段）
- ✅ 能回答 Chunking 策略的 tradeoff（chunk 太大→不精准、太小→丢上下文）
- ✅ 能解释 Hallucination 问题以及如何用 Grounding + Prompt Engineering 缓解
- ✅ 能回答 LLM 可观测性为什么重要、Langfuse 追踪什么
- ✅ 能说清 RAGAS 的评估维度及其含义
- ✅ 能回答 Streaming 的实现方式（SSE）和 Citation 在 Stream 中的处理（先发 citation_data event）
- ✅ 能说清 Adapter Pattern 的设计动机（SearchClient 本地/生产切换）
- ✅ 能解释为什么不引入 LangChain（RAG 三步直线，框架 ROI 为负）
- ✅ 能解释为什么不用 AKS（POC 阶段 YAGNI，Container Apps 够用）
- ✅ 能讲出 Docker Compose vs 云部署的区别和适用场景

---

## 技术栈总览

| 类别               | 技术                                  | 用途                                                |
| ------------------ | ------------------------------------- | --------------------------------------------------- |
| 后端               | Python, FastAPI                       | Web 框架, API 开发                                  |
| 前端               | React, TypeScript                     | Chat / Ask / Citation UI                            |
| LLM                | Azure OpenAI (gpt-4o-mini)            | 对话生成                                            |
| Embedding          | Azure OpenAI (text-embedding-ada-002) | 文本向量化                                          |
| 向量数据库（本地） | ChromaDB                              | 本地开发向量存储与检索                              |
| 搜索引擎（生产）   | Azure AI Search                       | Hybrid Search + Semantic Ranker                     |
| 文档解析           | PyMuPDF                               | PDF 文本提取                                        |
| 对话存档           | Azure Blob Storage                    | 覆盖写入，供调试                                    |
| 容器化             | Docker, Docker Compose                | 本地开发环境编排                                    |
| 生产部署           | Azure Container Apps                  | 托管容器服务                                        |
| 镜像仓库           | Azure Container Registry              | Docker 镜像存储                                     |
| 服务监控           | Azure Application Insights            | 请求量/响应时长/错误率                              |
| LLM 可观测性       | Langfuse                              | LLM 调用链路追踪、token/cost 分析                   |
| RAG 评估           | RAGAS                                 | Faithfulness / Answer Relevancy / Context Precision |
| 测试               | pytest                                | 单元测试、集成测试                                  |

---

## 设计模式

| 模式             | 在项目中的体现                                                   |
| ---------------- | ---------------------------------------------------------------- |
| Adapter Pattern  | SearchClient 封装向量数据库（ChromaDB / Azure AI Search 可切换） |
| Pipeline Pattern | Ingestion 流水线：解析 → 分块 → Embedding → 索引                 |

> **注：** Strategy Pattern（Approach 类）已移除——Ask = history 为空的 Chat，同一套逻辑自然退化，无需两套策略类。LangChain 已移除——RAG 三步直线无分支，框架 ROI 为负。

---

## ATS 技术栈覆盖度

| Tier      | 技术关键词                  | 是否覆盖                        |
| --------- | --------------------------- | ------------------------------- |
| 🔴 Tier 1 | Python                      | ✅ FastAPI 后端                 |
| 🔴 Tier 1 | LLM, RAG, Transformer, NLP  | ✅ 整个项目核心                 |
| 🔴 Tier 1 | FastAPI / REST API          | ✅ Day 1 起                     |
| 🔴 Tier 1 | Docker                      | ✅ Day 3 起                     |
| 🔴 Tier 1 | Azure（云平台）             | ✅ Day 2 起                     |
| 🔴 Tier 1 | Git                         | ✅ 全程                         |
| 🟠 Tier 2 | ChromaDB（向量数据库）      | ✅ Day 2                        |
| 🟠 Tier 2 | OpenAI Embeddings           | ✅ Day 2                        |
| 🟠 Tier 2 | RAGAS（Eval 框架）          | ✅ Day 8                        |
| 🟡 Tier 3 | Langfuse（LLM 可观测性）    | ✅ Day 7                        |
| 🟡 Tier 3 | Prompt Engineering          | ✅ Day 6                        |
| ⚪ 移除   | PyTorch / HuggingFace       | ❌ POC 阶段不引入本地模型       |
| ⚪ 移除   | LangChain                   | ❌ RAG 直线流程，ROI 为负       |
| ⚪ 移除   | Kubernetes (AKS)            | ❌ 改用 Container Apps          |
| ⚪ 移除   | Terraform                   | ❌ POC 阶段 YAGNI               |
| ⚪ 移除   | Prometheus + Grafana        | ❌ 改用 Application Insights    |
| ⚪ 移除   | PostgreSQL / Redis / Celery | ❌ 无需异步任务和持久化对话历史 |

## 移除的功能（相比 v2，以下内容已从范围中删除）

| 功能                                                        | 移除原因                                                  |
| ----------------------------------------------------------- | --------------------------------------------------------- |
| 文档上传 UI（前端）                                         | 上传频率极低，ROI 为负                                    |
| Azure Blob Storage 存文档                                   | 原始 PDF 留内部，citation 只需文件名+页码                 |
| Settings 面板（temperature、prompt override、检索策略切换） | POC 不面向用户配置                                        |
| 历史对话持久化（用户可见）                                  | 员工不登录，存档仅供调试                                  |
| Celery + Redis 异步任务队列                                 | 上传走脚本，无需异步处理                                  |
| PostgreSQL                                                  | 无需存对话历史，改用 Blob Storage 覆盖写入                |
| LangChain / LCEL                                            | RAG 三步直线流程，引入框架 ROI 为负，自己写三个函数更清晰 |
| Strategy Pattern（Approach 类）                             | Ask = history 为空的 Chat，无需两套策略类                 |
| HuggingFace / 本地 Embedding                                | POC 阶段统一用 Azure OpenAI，不引入额外复杂度             |
| 本地 Cross-Encoder Re-ranking                               | 生产用 Azure Semantic Ranker，POC 阶段无需自部署          |
| AKS + Terraform + GitHub Actions CI/CD                      | 改用 Azure Container Apps，POC 阶段过度设计               |
| Prometheus + Grafana                                        | 改用 Azure Application Insights（原生零配置）             |
| "Thought Process" 展示面板                                  | 面向调试，POC 用户无需此功能                              |
| UI 检索策略切换                                             | 策略由工程决定，不暴露给用户                              |

---
