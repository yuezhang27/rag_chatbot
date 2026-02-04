## 超小 RAG Chatbot MVP

### 项目结构

- **app.py**: FastAPI 应用入口，包含：
  - SQLite 初始化与表结构创建
  - 启动时读取并切分本地 `policy.txt` 写入 `docs` 表
  - 单一接口 `POST /v1/chat/answer`
  - 调用 OpenAI 的 `gpt-4o-mini` 同步生成答案
- **policy.txt**: 本地示例 policy 文本，启动时会被切成若干 chunk 写入 `docs` 表。
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
  "citations": [
    { "doc_id": 1, "title": "policy", "snippet": "..." }
  ]
}
```

# rag_chatbot