## 超小 RAG Chatbot MVP

### E2E

#### 准备资料

```text
<!-- 容器启动 -->
docker compose up -d

<!-- 用local parser解析，然后扫文件，存数据 -->
docker exec rag-backend python scripts/prepdocs.py --input-dir data --pattern "test*.pdf" --parser local

<!-- 自动根据.env里 SEARCH_BACKEND 决定检查哪个后端。 -->
docker exec rag-backend python scripts/check_index.py
```

（原本的数据库检查：如果.env里，储存在chromaDB，用本地模式
docker exec rag-backend python scripts/check_chroma.py
）

#### E2E

- http://localhost:3000/
- http://localhost:3000/ask

#### 关闭

```text
docker compose down
```

### 如何使用OpenCode进行vibe coding

- Step1. cmd，执行命令`wsl`
- Step2. 进入项目文件夹
- Step3. `opencode`

> 如果opencode不确定怎么弄：https://www.runoob.com/ai-agent/opencode-coding-agent.html

### 项目结构

- **app.py**: FastAPI 应用入口，包含：
  - SQLite 初始化与表结构创建
  - 启动时若 `docs` 为空则读取并切分本地 `policy.txt` 写入 `docs` 表
  - 单一接口 `POST /v1/chat/answer`、管理员 `POST /admin/documents/upload` 上传 PDF
  - 调用 OpenAI 的 `gpt-4o-mini` 同步生成答案
- **scripts/prepdocs/pdfparser.py**: PDF 文本抽取，支持 `local`（pypdf）与 `azure`（Document Intelligence）。
- **scripts/prepdocs/textsplitter.py**: 结构感知递归分块（段落→行→句→词），支持 chunk 长度与 overlap，适合保险/内部 PDF RAG。
- **policy.txt**: 本地示例 policy 文本，启动时若无文档则被切成若干 chunk 写入 `docs` 表。
- **requirements.txt**: Python 依赖列表。
- **Dockerfile**: 最简可运行 Docker 镜像配置。

### 本地运行（无需 Docker）

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 设置环境变量（以 PowerShell 为例）：

```bash
$env:OPENAI_API_KEY = "your_api_key_here"
```

3. 启动服务：

```bash
python app.py
```

服务默认监听 `http://0.0.0.0:8000`。

### 使用 Docker 启动

1. 构建镜像：

```bash
docker build -t rag-chatbot-mvp .
```

2. 运行容器（注意传入 OpenAI API Key 环境变量）：

```bash
docker run -e OPENAI_API_KEY=your_api_key_here -p 8000:8000 rag-chatbot-mvp
```

### 示例请求（curl）

新建会话并启用检索：

```bash
curl -X POST "http://localhost:8000/v1/chat/answer" ^
  -H "Content-Type: application/json" ^
  -d "{
    \"conversation_id\": null,
    \"user_id\": \"u1\",
    \"question\": \"请根据政策文本总结一下系统会如何处理用户隐私？\",
    \"use_retrieval\": true,
    \"top_k\": 3
  }"
```

典型返回示例（结构）：

```json
{
  "conversation_id": 1,
  "message_id": 2,
  "answer": "...",
  "citations": [{ "doc_id": 1, "title": "policy", "snippet": "..." }]
}
```

### 管理员上传 PDF（新增）

上传 PDF 后会自动：解析文本 → 结构感知分块 → 写入 SQLite `docs` 表。无需 worker，同步处理即可。

1. **仅本地解析（默认）**  
   使用 pypdf，无需额外配置：

```bash
curl -X POST "http://localhost:8000/admin/documents/upload" ^
  -F "file=@/path/to/your.pdf"
```

2. **使用 Azure Document Intelligence**  
   设置环境变量后，上传时指定 `parser=azure`：

```bash
$env:DOCINTELLIGENCE_ENDPOINT = "https://xxx.cognitiveservices.azure.com/"
$env:DOCINTELLIGENCE_KEY = "your_key"

curl -X POST "http://localhost:8000/admin/documents/upload?parser=azure" ^
  -F "file=@/path/to/your.pdf"
```

返回示例：`{"filename":"xxx.pdf","title":"xxx","chunks_inserted":12}`

3. **验证上传结果**
   - 再调用 `POST /v1/chat/answer`，`question` 里提一个 PDF 中的问题，`use_retrieval: true`，看是否能检索到并回答。
   - 或直接查 SQLite：`SELECT id, title, length(chunk) FROM docs;`

# rag_chatbot
