# 待测试清单

> Day 6 / 7 / 8 / 9 实现已完成，尚未验证。明天统一按此清单测试。

---

## 前置：启动服务

```bash
docker compose build backend   # Day 7/8/9 新增了依赖，需要重新 build
docker compose up -d
docker exec rag-backend python scripts/prepdocs.py --input-dir data --pattern "test*.pdf" --parser local
```

---

## Day 6 — Prompt Engineering（无需额外配置）

### E2E 测试

用Powershell

**测试 1：有依据的问题（正向）**

直接问题： What dental benefits are covered?

或者

```bash
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/answer" -ContentType "application/json" -Body '{"message":"What spinal benefits are covered?","history":[]}'
```

预期：回答包含具体内容，末尾有 `（来源：xxx.pdf，第 X 页）` 格式引用。

**测试 2：文档里没有的问题（幻觉压制）**

直接问：Does the company provide shuttle bus service?

或者

```bash
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/answer" -ContentType "application/json" -Body '{"message":"Does the company provide shuttle bus service?","history":[]}'
```

预期：回答包含"根据现有资料无法确认"，不编造答案。

**测试 3：流式接口 SSE 顺序回归**

```bash
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/stream" -ContentType "application/json" -Body '{"message":"What is the vision coverage?","history":[]}'
```

预期：第一个 SSE event 是 `event: citation_data`，随后 `event: response_text`，最后 `event: done`。

---

## Day 7 + 8 — Langfuse 可观测性 & RAGAS 评估

### 第一步：配置 Langfuse（注册 + 填 .env）

1. 浏览器打开 https://cloud.langfuse.com，注册账号，创建一个 Project
2. 进入 Project → Settings → API Keys → 复制 **Public Key**（`pk-lf-...`）和 **Secret Key**（`sk-lf-...`）
3. 用编辑器打开项目根目录下的 `.env` 文件，添加以下三行（替换成你的真实 key）：

```
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx
LANGFUSE_HOST=https://cloud.langfuse.com
```

4. 保存 `.env`，然后在 PowerShell 里重启容器让新配置生效：

```powershell
docker compose up -d
```

5. 确认容器已重启、无报错（看最后几行日志）：

```powershell
docker compose logs backend --tail 20
```

---

### 第二步：填写 RAGAS 评估数据集

打开 `data/eval_dataset.json`，把 5 道占位题目替换成基于你的测试 PDF 的真实问答对，格式如下：

```json
[
  {
    "question": "What spinal benefits are covered?",
    "ground_truth": "（从 PDF 里抄对应段落的正确答案）"
  },
  {
    "question": "第二道真实问题",
    "ground_truth": "对应标准答案"
  }
]
```

> 至少填满 5 题才能跑出有意义的分数。`ground_truth` 必须是真实文本，不能留空或写 TODO。

---

### 第三步：E2E 测试（按顺序跑完）

**测试 1 — Langfuse 非流式 Trace**

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/answer" -ContentType "application/json" -Body '{"message":"What dental benefits are covered?","history":[]}'
```

然后打开浏览器 → Langfuse Dashboard → 左侧 **Traces**，检查：

- [ ] 出现一条名为 `rag-chat-answer` 的 Trace
- [ ] 点开后下挂 3 个 Span：`retrieve` / `build_prompt` / `llm_generate`
- [ ] `retrieve` Span 的 output 包含文件名 + 页码
- [ ] `llm_generate` Span 的 output 是完整回答文本（不为空）
- [ ] `llm_generate` Span 右侧显示 token 用量（input / output tokens）

**测试 2 — Langfuse 流式 Trace**

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/stream" -ContentType "application/json" -Body '{"message":"What is the vision coverage?","history":[]}'
```

Langfuse → Traces：

- [ ] 出现 `rag-chat-stream` Trace，`llm_generate` Span 有完整输出（不是空的）

**测试 3 — Langfuse 挂掉不影响主链路**

1. 用编辑器打开 `.env`，在 `LANGFUSE_PUBLIC_KEY` 这行行首加 `#` 注释掉它，保存
2. 重启容器：`docker compose up -d`
3. 再发一次请求，确认正常返回答案：

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/answer" -ContentType "application/json" -Body '{"message":"What dental benefits are covered?","history":[]}'
```

4. 查看后端日志确认无 Exception：`docker compose logs backend --tail 20`
5. 测完后把 `#` 去掉恢复配置，再重启：`docker compose up -d`

- [ ] 无 Langfuse key 时请求正常返回，日志无 Exception

**测试 4 — RAGAS 评估跑通**

确保第二步的 `eval_dataset.json` 已填写完毕，然后运行：

```powershell
docker exec rag-backend python scripts/evaluate.py --dataset data/eval_dataset.json --top-k 5
```

- [ ] 控制台输出三个维度分数（0~1 之间的数字，不是 N/A 或 NaN）
- [ ] 项目目录 `data/` 下生成 `eval_results_YYYYMMDD_HHMMSS.json` 文件

**测试 5 — RAGAS 调参对比（可选，验证 chunk_size 影响检索质量）**

```powershell
docker exec rag-backend python scripts/evaluate.py --dataset data/eval_dataset.json --top-k 5
```

记录上面输出的 `context_precision` 分数，然后用 chunk_size=300 重新入库再评估：

```powershell
docker exec rag-backend python scripts/prepdocs.py --input-dir data --pattern "test*.pdf" --chunk-size 300
```

```powershell
docker exec rag-backend python scripts/evaluate.py --dataset data/eval_dataset.json --top-k 5
```

- [ ] 两次 `context_precision` 数值不同（说明参数确实影响了检索质量）

---

### 常见问题

- **RAGAS 分数全为 NaN** → `data/eval_dataset.json` 的 `ground_truth` 不能为空，必须是真实文本
- **RAGAS 调用 LLM 失败** → 检查容器内能看到 Azure 配置：`docker exec rag-backend env | grep AZURE`
- **`No module named 'langchain_openai'`** → 镜像没有新依赖，运行 `docker compose build backend` 再 `docker compose up -d`
- **Langfuse Trace 没出现** → 等 10~30 秒刷新，Langfuse Cloud 有延迟；也可查后端日志有无 Langfuse 报错

---

## Day 9 — 生产部署 + Blob 存档 + Thumbs Down

### 需要配置

#### A. 本地验证 Blob 写入（可选，不影响主链路）

在 Azure Portal 创建一个 Storage Account，进入 **Access Keys** 复制 Connection String，填入 `.env`：

```
AZURE_BLOB_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=xxx;AccountKey=xxx;EndpointSuffix=core.windows.net
AZURE_BLOB_CONTAINER_NAME=conversation-logs
```

容器会自动创建，不用手动建。

#### B. 生产部署到 Azure Container Apps（一次性操作）

**前置：需要有 Azure Container Registry（ACR）**

```bash
# 1. 登录 ACR
az acr login --name <your-acr-name>

# 2. Build & push backend
docker build -t <your-acr-name>.azurecr.io/rag-backend:latest .
docker push <your-acr-name>.azurecr.io/rag-backend:latest

# 3. Build & push frontend（先填好生产 backend URL）
# 在 frontend/.env.production 里写：
# VITE_API_BASE=https://<your-backend>.azurecontainerapps.io
docker build -t <your-acr-name>.azurecr.io/rag-frontend:latest ./frontend
docker push <your-acr-name>.azurecr.io/rag-frontend:latest

# 4. 在 Azure Portal → Container Apps 创建两个 App：
#    - rag-backend：镜像用 backend:latest，端口 8000，配置所有 .env 里的环境变量
#    - rag-frontend：镜像用 frontend:latest，端口 3000
```

**生产环境必须配置的环境变量（在 Container Apps → Environment Variables 里设置）：**

```
AZURE_OPENAI_ENDPOINT
AZURE_OPENAI_API_KEY
AZURE_OPENAI_CHAT_DEPLOYMENT
AZURE_OPENAI_EMBEDDING_DEPLOYMENT
AZURE_OPENAI_API_VERSION
SEARCH_BACKEND=azure
AZURE_SEARCH_ENDPOINT
AZURE_SEARCH_API_KEY
AZURE_SEARCH_INDEX_NAME
AZURE_SEARCH_SEMANTIC_CONFIG
AZURE_BLOB_CONNECTION_STRING
AZURE_BLOB_CONTAINER_NAME=conversation-logs
LANGFUSE_PUBLIC_KEY
LANGFUSE_SECRET_KEY
LANGFUSE_HOST=https://cloud.langfuse.com
APPLICATIONINSIGHTS_CONNECTION_STRING
```

### E2E 测试

#### 本地验证（先跑，再部署）

**测试 1：Blob 写入**

```bash
# 配好 AZURE_BLOB_CONNECTION_STRING 后重启
docker compose up -d

# 发一条消息（非流式最简单）
curl -s -X POST http://localhost:8000/v1/chat/answer \
  -H "Content-Type: application/json" \
  -d '{"message":"What dental benefits are covered?","history":[]}'
```

预期：

- [ ] 回答正常返回
- [ ] Azure Portal → Storage Account → Containers → conversation-logs → 出现 `{uuid}.json` 文件
- [ ] 文件内容包含 `conversation_id`、`timestamp`、`messages` 数组

**测试 2：Thumbs Down 按钮**

- 打开 http://localhost:3000
- 发一条消息，等回答出现
- 回答气泡底部应有 👎 按钮（灰色半透明）
- 点击 → 按钮变红框，变为不可点击
- [ ] 后端日志出现 `thumbs_down conversation_id=xxx message_index=1`

**测试 3：feedback 接口回归**

```bash
curl -s -X POST http://localhost:8000/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{"conversation_id":"test-123","message_index":1}'
```

预期：返回 `{"ok": true}`

**测试 4：Blob 不配时主链路不受影响**

- 注释掉 `.env` 里的 `AZURE_BLOB_CONNECTION_STRING`，重启
- 发消息 → 正常返回，无报错

#### 生产验证（部署后）

- [ ] 访问 Frontend Container App 的公网 URL → 页面加载正常
- [ ] 浏览器 Network 面板 → API 请求打到 backend Container App URL（不是 localhost）
- [ ] 发消息 → 正常回答，Langfuse 出现 Trace
- [ ] Blob Storage 出现对话存档文件
- [ ] 点 👎 → Application Insights 日志里出现 `thumbs_down` 关键词

### 常见问题

- **前端请求还是打到 localhost** → 检查 `frontend/.env.production` 里 `VITE_API_BASE` 是否填了生产 URL，且 build 时用的是 production 模式
- **Container App 启动失败** → Azure Portal → Container App → Log stream 查看启动报错；最常见原因：环境变量缺失
- **Blob 容器不存在报错** → 代码会自动创建容器，但需要 Connection String 有写权限（用 Account Key 的 Connection String 即可）

---

## Day 10 — LangChain + LangGraph 编排 + LLM/Embedding 升级

### 需要配置

#### A. 升级 LLM 和 Embedding 模型

1. 在 Azure Portal → Azure OpenAI → 确保已部署以下模型：
   - **gpt-4o**（如果之前只有 gpt-4o-mini，需要新建 deployment）
   - **text-embedding-3-large**（如果之前只有 text-embedding-ada-002，需要新建 deployment）

2. 修改 `.env` 中的模型配置：

```
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-large
# 可选：设置 embedding 维度缩减（默认 3072，可设为 1024 或 512 以节省存储）
# EMBEDDING_DIMENSIONS=3072
```

#### B. 重建索引（Embedding 维度变了，旧索引不兼容）

**重要：** 因为 text-embedding-3-large 的向量维度（3072）与 ada-002（1536）不同，必须重新 ingest 所有文档。

如果用 Azure AI Search：
- 在 Azure Portal → Azure AI Search → 删除旧的 `hr-documents` 索引
- 代码会在 ingest 时自动用新维度重建索引

如果用本地 ChromaDB：
- 删除 `chroma_db/` 目录下所有文件，让代码重建 collection

#### C. 重新构建镜像并 ingest

```powershell
# Day 10 新增了 langchain/langgraph 依赖，必须重新 build
docker compose build backend
docker compose up -d

# 重新入库（用新的 embedding 模型）
docker exec rag-backend python scripts/prepdocs.py --input-dir data --pattern "test*.pdf" --parser local
```

---

### E2E 测试

**测试 1：非流式接口基本功能（LangGraph 链路验证）**

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/answer" -ContentType "application/json" -Body '{"message":"What dental benefits are covered?","history":[]}'
```

预期：
- [ ] 正常返回回答，包含引用（来源：xxx.pdf，第 X 页）
- [ ] 回答质量不低于 Day 9（GPT-4o 预期更好）

**测试 2：流式接口 SSE 顺序回归**

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/stream" -ContentType "application/json" -Body '{"message":"What is the vision coverage?","history":[]}'
```

预期：
- [ ] SSE 事件顺序不变：`citation_data` → `response_text` → `done`
- [ ] citation 内容正常（文件名 + 页码 + snippet）

**测试 3：幻觉压制回归**

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/chat/answer" -ContentType "application/json" -Body '{"message":"Does the company provide shuttle bus service?","history":[]}'
```

预期：
- [ ] 回答包含"根据现有资料无法确认"，不编造答案

**测试 4：Langfuse Trace 验证 LangGraph 节点**

发送请求后，打开 Langfuse Dashboard → Traces：

- [ ] 出现 `rag-chat-answer` 或 `rag-chat-stream` Trace
- [ ] 点开后下挂 3 个 Span：`retrieve` / `build_prompt` / `llm_generate`
- [ ] `llm_generate` Span 的 model 显示为 `gpt-4o`（不是 gpt-4o-mini）

**测试 5：确认 LLM 使用 GPT-4o**

查看后端日志或 Langfuse trace，确认 model name：

```powershell
docker compose logs backend --tail 30
```

- [ ] 日志或 Langfuse 中 model 为 gpt-4o

**测试 6：确认 Embedding 使用 text-embedding-3-large**

在 ingest 日志中确认：

```powershell
docker exec rag-backend python -c "from scripts.chroma_embed import get_embedding_deployment; print(get_embedding_deployment())"
```

- [ ] 输出 `text-embedding-3-large`

**测试 7：RAGAS 评估跑通**

```powershell
docker exec rag-backend python scripts/evaluate.py --dataset data/eval_dataset.json --top-k 5
```

- [ ] 三个维度分数正常（0~1 之间，不是 NaN）
- [ ] 与 Day 8 基线对比，Faithfulness 和 Answer Relevancy 预期因 GPT-4o 有所提升

**测试 8：已有功能回归**

- [ ] 打开 http://localhost:3000 → 前端正常加载
- [ ] 发消息 → 正常回答
- [ ] 👎 按钮正常工作
- [ ] feedback 接口正常：

```powershell
Invoke-RestMethod -Method POST -Uri "http://localhost:8000/v1/feedback" -ContentType "application/json" -Body '{"conversation_id":"test-123","message_index":1}'
```

预期：返回 `{"ok": true}`

---

### 常见问题

- **`No module named 'langgraph'`** → 镜像没有新依赖，运行 `docker compose build backend` 再 `docker compose up -d`
- **检索返回空或报错** → 确认已用 text-embedding-3-large 重新 ingest；旧索引向量维度不兼容
- **Azure AI Search 报维度不匹配** → 需要在 Azure Portal 删除旧索引后重新 ingest
- **GPT-4o deployment 不存在** → 检查 Azure Portal 是否已部署 gpt-4o，确认 `.env` 中 `AZURE_OPENAI_CHAT_DEPLOYMENT` 名称正确
- **SSE 输出为空** → 检查 LangChain streaming 的 `chunk.content` 提取，确认 `AzureChatOpenAI` 的 `streaming=True` 已设置
