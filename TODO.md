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

## Day 7 — Langfuse 可观测性

### 需要配置（先做这步）

**1. 注册 Langfuse Cloud（免费）**

- 去 https://cloud.langfuse.com 注册账号
- 创建一个 Project
- 进入 Project → Settings → API Keys → 复制 Public Key 和 Secret Key

**2. 在 `.env` 里添加（然后重启容器）**

```
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx
LANGFUSE_HOST=https://cloud.langfuse.com
```

**3. 重启 backend 让新 env 生效**

```bash
docker compose up -d
```

### E2E 测试

**测试 1：非流式 Trace 验证（最快）**

```bash
curl -s -X POST http://localhost:8000/v1/chat/answer \
  -H "Content-Type: application/json" \
  -d '{"message":"What dental benefits are covered?","history":[]}'
```

然后去 Langfuse Dashboard → Traces：

- [ ] 出现一条名为 `rag-chat-answer` 的 Trace
- [ ] 下挂 3 个 Span：`retrieve` / `build_prompt` / `llm_generate`
- [ ] `retrieve` Span 的 output 包含 sources（文件名 + 页码）
- [ ] `llm_generate` Span 的 output 是完整回答文本（不为空）
- [ ] `llm_generate` Span 有 token 用量（input / output tokens）

**测试 2：流式 Trace 验证**

```bash
curl -s -X POST http://localhost:8000/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"What is the vision coverage?","history":[]}' \
  --no-buffer
```

去 Langfuse → Traces：

- [ ] 出现 `rag-chat-stream` Trace，`llm_generate` Span 有完整输出（不是空的）

**测试 3：Langfuse 挂掉不影响主链路**

- 注释掉 `.env` 里的 `LANGFUSE_PUBLIC_KEY`，重启容器
- 再发请求 → 正常返回答案，后端日志无 Exception

### Application Insights（可选，生产再配）

```
APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=xxx;IngestionEndpoint=...
```

从 Azure Portal → Application Insights 资源 → Overview → 复制 Connection String。

---

## Day 8 — RAGAS 评估

### 需要配置（先做这步）

**填写 eval_dataset.json**

文件路径：`data/eval_dataset.json`

当前文件里有 5 道占位题目（`"TODO: fill in..."`），需要你替换成真实的问答对。
格式要求：

```json
[
  {
    "question": "问题（与用户实际会问的一致）",
    "ground_truth": "标准答案（参考 HR 文档的正确内容）"
  }
]
```

建议至少填 5 题跑通流程，正式评估填 30 题。

### E2E 测试

**测试 1：先跑通（用占位 dataset 也行，但 ground_truth 要是真实文本）**

```bash
docker exec rag-backend python scripts/evaluate.py --dataset data/eval_dataset.json --top-k 5
```

预期：

- [ ] 控制台输出三个维度分数（0~1 之间的数字，不是 N/A）
- [ ] `data/eval_results_YYYYMMDD_HHMMSS.json` 文件生成

**测试 2：调参对比**

```bash
# 第一次：默认 chunk_size=400
docker exec rag-backend python scripts/evaluate.py  # 记录 context_precision 分数

# 修改 prepdocs.py 默认 chunk_size 为 300，重新 ingest
docker exec rag-backend python scripts/prepdocs.py --input-dir data --pattern "test*.pdf" --chunk-size 300

# 第二次跑评估
docker exec rag-backend python scripts/evaluate.py  # 对比 context_precision 分数
```

预期：两次 `context_precision` 数值不同（说明参数确实影响了检索质量）。

### 常见问题

- **分数全为 NaN** → 检查 `contexts` 字段是否是字符串列表（不能是 dict 列表）
- **RAGAS 调用 LLM 失败** → 确认 `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` 在容器内可见；`docker exec rag-backend env | grep AZURE`
- **`No module named 'langchain_openai'`** → 需要先 `docker compose build backend`（新依赖还没装进镜像）

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
