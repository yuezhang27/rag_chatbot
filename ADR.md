# 架构决策记录 — 完整版 v2

> 🟢 = 充分讨论过，推导完整

---

## 产品与功能决策

---

### 🟢 文档上传方式

- **选择：** 手动脚本，按需运行
- **原因：** HR文档极少更新（数月甚至一年一次）；HRBP不参与操作；上传频率极低ROI极差；POC核心价值在检索和生成，不在上传体验
- **其他选项：** 管理后台UI，HRBP自助上传
- **为什么不选：** 上传频率极低，专门做一个UI的ROI为负；HRBP没有义务操作技术工具

---

### 🟢 原始PDF存储

- **选择：** 保留在公司内部，不迁移到云
- **原因：** Citation只需记录文件名和页码，不需要链接到原始文件；系统核心是向量数据库里的内容，不是原始PDF
- **其他选项：** 迁移到Azure Blob Storage
- **为什么不选：** 公司已有内部API可获取文档；重复搬运增加存储成本和维护复杂度

---

### 🟢 流式响应

- **选择：** SSE逐字streaming输出
- **原因：** 用户体验显著更好；LLM生成需10-30秒，等待整段出现体验极差；Streaming把HTTP连接压力分散，高并发下服务器更稳
- **其他选项：** 等LLM生成完毕一次性返回
- **为什么不选：** 同步等待体验差；HTTP连接长期占用，高并发下服务器撑不住

---

### 🟢 对话记录存档

- **选择：** 上云后实现，本地阶段跳过（YAGNI）；生产环境每次请求覆盖写入Azure Blob Storage，不向员工暴露
- **原因：** 两周复盘一次，无实时性要求；前端每次请求都带完整history，后端收到时直接覆盖写入即可，无需检测"对话结束"；员工不登录，存档只供开发团队调试
- **实现方式：** 用conversation_id作为文件名覆盖写入，最后一次写入天然就是完整对话
- **其他选项：** 实时存入数据库 / 检测对话结束再存 / 完全不存
- **为什么不选：** 实时存数据库过度设计；检测对话结束不可靠（浏览器关闭无法感知）；完全不存则无法调试

---

### 🟢 多轮对话上下文管理

- **选择：** 完整历史由前端维护，每次请求全量传给后端和LLM
- **原因：** 预计10轮以内，token完全塞得下；前端维护状态最简单，后端无需存session；前端每次带完整history也天然支持对话存档（顺手覆盖写入）
- **其他选项：** 后端存历史 / 滑动窗口 / Summarization
- **为什么不选：** 为不存在的问题过度设计；保险问答场景对话轮数极有限

---

### 🟢 Chat vs Ask模式接口设计

- **选择：** 同一个接口，Ask = history为空的Chat请求
- **原因：** Ask模式本质上是history=[]的Chat请求；后端收到两者的请求在结构上完全一致，无需区分；history为空时处理逻辑自然退化为单轮问答
- **其他选项：** 两个独立接口
- **为什么不选：** 两者后端处理无本质差异，拆成两个接口是过度设计

---

### 🟢 conversation_id生成

- **选择：** 后端生成UUID，首次请求（不带conversation_id）时创建并返回给前端
- **原因：** 新对话的创建时机由后端控制，逻辑清晰；前端无ID = 新对话，有ID = 续集，无需额外字段标识；前端生成虽然也能保证唯一性，但后端无法区分"新对话"和"已有对话"，需要额外的is_new字段或每次查数据库
- **其他选项：** 前端生成UUID
- **为什么不选：** 前端生成导致后端无法区分新对话，引入额外复杂度

---

### 🟢 API请求结构

- **选择：** `{ conversation_id?, message, history[] }`，前端维护history；首次请求不带conversation_id，后端返回新生成的conversation_id
- **原因：** 前端维护history，后端不需要读数据库查历史；history存在React state，刷新即清空，符合"员工不需要跨session记忆"的需求；后端只需要写（存档），不需要读
- **其他选项：** 后端维护history，前端只传message和conversation_id
- **为什么不选：** 后端维护history需要每次请求读写数据库，增加延迟和复杂度；员工不需要跨session记忆，没有必要

---

### 🟢 SSE协议设计

- **选择：** 先发`citation_data` event，再持续发`response_text` event
- **原因：** 检索结果在LLM开始生成前已知；两种数据类型分开传输清晰；前端可以提前渲染引用面板
- **其他选项：** 等LLM输出完再一起返回citation
- **为什么不选：** 破坏streaming体验；citation面板要等整个回答结束才能渲染

---

### 🟢 后端架构

- **选择：** 单体FastAPI
- **原因：** 只有一个核心功能（回答问题）；无独立扩缩容需求；POC阶段YAGNI
- **其他选项：** 微服务
- **为什么不选：** 高并发不等于微服务；POC阶段过度设计；MQ/Redis等暂无必要

---

### 🟢 RAG质量评估

- **选择：** RAGAS
- **原因：** 本质是自动化测试工具，每次改动（prompt/chunk size/检索策略）后跑一次，快速量化好坏；pre阶段跑结果给业务方看；比人工测试效率高、可重复、可量化
- **测试集来源：** 复用之前邮件里SA和HR确认的30个问题+标准答案，直接作为ground_truth
- **使用时机：** 本地开发每次改动后跑；pre阶段正式跑一次；上线后不跑（无ground_truth）
- **评估维度：** Faithfulness（有没有胡说）、Answer Relevancy（答非所问）、Context Precision（检索精准度）
- **其他选项：** 纯人工测试 / 自建评估脚本
- **为什么不选：** 人工测试慢、不可重复；自建脚本要重新造轮子

---

### 🟢 监控分层

- **选择：** Langfuse（LLM调用层）+ Azure Application Insights（服务层）+ Thumbs Down用户反馈
- **原因：** 两个工具监控不同层面，互补不重叠；Application Insights看请求量/错误率/延迟（服务健康）；Langfuse看完整调用链路/token消耗/prompt输入输出（LLM内部）；Application Insights无法回放LLM调用内部细节；Thumbs Down捕捉用户主观感受，是Langfuse看不到的维度
- **其他选项：** 只用Application Insights / 只用Langfuse
- **为什么不选：** Application Insights无法追踪LLM调用内部；只用Langfuse则缺少服务层监控

---

## 技术选型

---

### 🟢 后端框架

- **选择：** Python + FastAPI
- **原因：** AI工程领域最主流；async支持好；生态丰富；与Python AI库天然兼容
- **其他选项：** Django / Flask / Node.js
- **为什么不选：** Django过重；Flask无async原生支持；Node.js与Python AI生态不兼容

---

### 🟢 前端框架

- **选择：** React
- **原因：** 流式文字渲染场景下虚拟DOM高效；AI工程领域最主流；参考实现多
- **其他选项：** Vue / Angular / 纯HTML
- **为什么不选：** 未深入讨论，但React在SSE流式渲染场景下是业界标准选择

---

### 🟢 LLM

- **选择：** Azure OpenAI gpt-4o-mini
- **原因：** HR问答场景低创造性、高准确性要求；LLM主要工作是从给定chunks中组织语言，不需要gpt-4o的推理能力；便宜+快是核心需求；已验证可用；在Azure生态内
- **其他选项：** gpt-4o / Claude / 开源模型
- **为什么不选：** gpt-4o能力过剩且贵；开源模型需自行部署，维护复杂度高，POC阶段ROI为负

---

### 🟢 Embedding模型

- **选择：** Azure OpenAI text-embedding-ada-002
- **原因：** 已验证可用；与LLM同一Azure账户管理；选型方法论：看向量维度、MTEB榜单精度、成本、context窗口
- **选型方法论：** 向量维度（语义精度）、MTEB leaderboard排名、成本、context窗口大小（本项目问题短，此项不是瓶颈）
- **其他选项：** text-embedding-3-small / 开源BGE
- **为什么不选：** text-embedding-3-small可按方法论对比后决定是否切换；BGE需本地部署，POC阶段增加复杂度

---

### 🟢 向量数据库（本地开发）

- **选择：** ChromaDB
- **原因：** 轻量、好启动、够用；本地开发不需要生产级分布式功能；通过Adapter Pattern与生产环境隔离，切换无需改上层代码
- **其他选项：** Qdrant / FAISS
- **为什么不选：** FAISS无持久化无HTTP服务，只是library，不适合作为服务运行；Qdrant功能更强但本地开发场景过重

---

### 🟢 向量数据库（生产）

- **选择：** Azure AI Search
- **原因：** 支持Hybrid Search（BM25+向量+RRF）；内置Semantic Ranker（精排）；在Azure生态内；Adapter Pattern切换无需改上层代码
- **其他选项：** Qdrant云版 / Pinecone
- **为什么不选：** Qdrant/Pinecone不在Azure生态内，需要额外管理；Azure AI Search一站式解决检索需求

---

### 🟢 PDF解析

- **选择：** PyMuPDF
- **原因：** 主流、快、开源免费；HR文档是电子PDF无需OCR
- **其他选项：** pdfplumber / Azure Document Intelligence
- **为什么不选：** pdfplumber表格提取强但速度慢，本项目无复杂表格；Azure DI按页收费，且文档非扫描件，杀鸡用牛刀。未来有扫描件需求再引入Azure DI

---

### 🟢 检索策略

- **选择：** 本地ChromaDB用向量检索；生产Azure AI Search用Hybrid Search（BM25+向量+RRF）+ Semantic Ranker精排
- **原因：** 纯关键词BM25缺语义理解（dental ≠ teeth）；纯向量搜索缺精确匹配（条款编号）；Hybrid Search兼顾两者；RRF解决不同量纲无法直接合并的问题（统一转成排名再融合）；Semantic Ranker是粗排后的精排，POC 50个文档量级用Azure内置即可，不需要自部署Cross-Encoder
- **粗排：** Hybrid Search（向量+BM25，RRF融合）→ top-20
- **精排：** Azure AI Search Semantic Ranker → top-5
- **其他选项：** 纯关键词 / 纯向量 / 自部署Cross-Encoder精排
- **为什么不选：** 纯关键词/纯向量各有盲区；自部署Cross-Encoder POC阶段过度设计，文档量大了再引入

---

### 🟢 Chunking策略

- **选择：** 固定大小+overlap；chunk size=400 tokens，overlap=80 tokens（约20%）
- **原因：** HR文档短段落多，400 tokens约3-5段，粒度合适；overlap防止语义在边界处硬断；参数不拍死，RAGAS跑完看Context Precision，分数低就调小chunk size
- **其他选项：** 语义分块 / 按标题分块
- **为什么不选：** 语义分块实现复杂，POC阶段YAGNI；按标题分块依赖文档结构，HR文档格式不统一

---

### 🟢 Ingestion Pipeline详细设计

- **选择：** PyMuPDF解析 → 400 tokens/80 overlap分块 → batch 20个chunk调embedding → 写入向量数据库
- **原因：** 每步职责清晰；batch减少网络往返次数（每次HTTP请求有overhead）；batch size=20由API限制倒推：400 tokens × 20 = 8000 tokens，在text-embedding-ada-002单次8191 token上限以内
- **其他选项：** 逐个chunk embedding
- **为什么不选：** 逐个调用网络往返次数多，速度慢

---

### 🟢 Adapter Pattern设计

- **选择：** 只需要一个SearchClient，封装向量数据库的CRUD操作；本地实现ChromaDBSearchClient，生产实现AzureAISearchClient
- **原因：** 本项目只有一处本地/生产用不同服务：向量数据库；PDFParser本地和生产都用PyMuPDF，不需要Adapter
- **接口细节：** 在Micro Spec阶段定义
- **其他选项：** 额外封装PDFParser等
- **为什么不选：** PDFParser无需切换，封装它没有意义，过度设计

---

### 🟢 LLM链路追踪工具

- **选择：** Langfuse
- **原因：** 不绑定LangChain（我们不用LangChain）；支持完整Trace/Span回放调用链路；开源可自部署，数据留在内部符合企业安全要求；功能覆盖token统计、延迟、完整prompt输入输出
- **其他选项：** LangSmith / Helicone / 自建
- **为什么不选：** LangSmith与LangChain深度绑定，我们不用LangChain则很别扭；Helicone功能简单，主要是token统计，不支持完整调用链路回放

---

### 🟢 服务监控工具

- **选择：** Azure Application Insights
- **原因：** Azure原生，零配置集成；监控请求量、响应时长、错误率；不需要额外部署和维护
- **其他选项：** Prometheus + Grafana / Datadog
- **为什么不选：** Prometheus+Grafana需自己运维；Datadog贵；两者在Azure原生集成上不如Application Insights；YAGNI

---

### 🟢 本地开发编排

- **选择：** Docker Compose
- **原因：** 一键启动FastAPI+React+ChromaDB；服务间网络自动配置；环境一致性
- **其他选项：** 手动启动各进程
- **为什么不选：** 每次开发要开多个terminal；环境不一致；协作困难

---

### 🟢 生产部署

- **选择：** Azure Container Apps
- **原因：** POC阶段200人并发；托管服务屏蔽Kubernetes复杂度；够用即可
- **其他选项：** AKS（Azure Kubernetes Service）/ Azure App Service
- **为什么不选：** AKS配置复杂，POC阶段杀鸡用牛刀；App Service是单服务PaaS，多容器场景下需要手动管理多个实例和网络，运维成本高于Container Apps

---

### 🟢 Strategy Pattern

- **选择：** 不引入
- **原因：** Ask = history为空的Chat，同一套处理逻辑，history为空时自然跳过query改写步骤；无需两套策略类
- **其他选项：** 引入Strategy Pattern区分Chat和Ask处理逻辑
- **为什么不选：** YAGNI；两种模式的处理逻辑差异不足以支撑引入Strategy Pattern的复杂度

---

### 🟢 LLM编排框架

- **选择：** 不引入LangChain，自己写
- **原因：** RAG流程只有三步（检索→拼prompt→调LLM），直线无分支；LangChain价值在多步骤多工具编排，此处ROI为负；自己写三个函数调用更清晰可控易调试
- **其他选项：** LangChain / LlamaIndex
- **为什么不选：** LangChain适合复杂多步骤流程；LlamaIndex同理；两者在本项目都是过度引入
