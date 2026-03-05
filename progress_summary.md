# 项目功能总结

这是一个 **超小 RAG (Retrieval-Augmented Generation) Chatbot MVP**，实现了以下功能：

### 核心功能

1. **RAG 问答系统** - 基于检索增强生成的聊天机器人
2. **本地知识库** - 启动时将 `policy.txt` 文本切分成 chunk 存入 SQLite 数据库
3. **文档检索** - 使用简单的 LIKE 模式匹配从数据库中检索相关文档
4. **对话管理** - 支持创建会话、存储对话历史
5. **引用返回** - 返回答案时附带相关文档引用

### 技术栈

- **FastAPI** - Web 框架
- **SQLite** - 本地数据库存储
- **OpenAI GPT-4o-mini** - LLM 生成答案

### API 接口

- `POST /v1/chat/answer` - 发送问题获取 RAG 增强的答案
- `GET /` - 健康检查

### 数据流

1. 用户发送问题 → 检索相关文档 → 构建 prompt → 调用 OpenAI 生成答案 → 返回答案+引用
