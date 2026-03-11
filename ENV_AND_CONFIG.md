# 环境变量与配置说明

本文档汇总本项目所需的环境变量，以及「仅本地开发」与「接入 Azure 等云服务」时的配置方式。后续接入新服务时，请在此文档中补充对应变量与配置步骤。

---

## 一、环境变量一览

| 变量名 | 用途 | 本地开发是否必填 | 接入 Azure/云服务时 |
|--------|------|------------------|---------------------|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI 端点（对话 + Embedding） | **必填**（当前已切到 Azure） | 必填 |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API 密钥 | **必填** | 必填 |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | 对话模型部署名（如 gpt-4o-mini） | 可选，默认 `gpt-4o-mini` | 按实际部署名填写 |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | Embedding 模型部署名（如 text-embedding-3-small） | 可选，默认 `text-embedding-3-small` | 按实际部署名填写 |
| `AZURE_OPENAI_API_VERSION` | API 版本 | 可选，默认 `2024-02-01` | 按需 |
| `DOCINTELLIGENCE_ENDPOINT` | Azure Document Intelligence 接口地址（PDF 解析） | 不填 | 使用 `parser=azure` 时**必填** |
| `DOCINTELLIGENCE_KEY` | Azure Document Intelligence 密钥 | 不填 | 使用 `parser=azure` 时**必填** |

- **配置方式**：项目启动时会从**项目根目录的 `.env` 文件**加载上述变量（`python-dotenv`），无需再手动 export。
- **仅本地开发**：配置好 `AZURE_OPENAI_*` 即可；PDF 上传使用本地解析（pypdf）时无需 Document Intelligence。
- **向量检索**：已使用 ChromaDB（`./chroma_db`）+ Azure OpenAI Embedding，上传 PDF 时会批量生成向量并写入 Chroma。

---

## 二、本地开发（当前）

### 2.1 必须配置

- **`OPENAI_API_KEY`**  
  - 用于 `POST /v1/chat/answer` 等调用 OpenAI（如 gpt-4o-mini）。  
  - 获取方式：[OpenAI API Keys](https://platform.openai.com/api-keys) 创建并复制 Key。

### 2.2 配置方式示例

**PowerShell（当前终端有效）：**

```powershell
$env:OPENAI_API_KEY = "sk-xxxxxxxx"
```

**Windows 系统环境变量（长期）：**  
「此电脑」→ 属性 → 高级系统设置 → 环境变量 → 用户变量/系统变量 → 新建 `OPENAI_API_KEY`。

**`.env` 文件（若后续用 python-dotenv 等）：**  
在项目根目录创建 `.env`，内容例如：

```env
OPENAI_API_KEY=sk-xxxxxxxx
```

注意：当前项目未内置加载 `.env`，若使用需自行在启动前加载或使用 dotenv 库。

### 2.3 本地不配置 Azure

- 不设置 `DOCINTELLIGENCE_ENDPOINT`、`DOCINTELLIGENCE_KEY` 即可。  
- 上传 PDF 时不要带 `parser=azure`，使用默认的本地解析（pypdf）。

---

## 三、未来接入 Azure 时的配置步骤

以下按「先创建资源 → 再在项目中配置」的顺序说明，便于以后一步步完成。

### 3.1 Azure Document Intelligence（PDF 解析）

用于管理员上传 PDF 时选用「Azure 解析」而非本地 pypdf，适合复杂版式、多语言或需要更高识别率的场景。

#### 步骤 1：创建 Azure 账号与订阅

1. 打开 [Azure 门户](https://portal.azure.com/) 并登录。  
2. 若无订阅，先创建 [免费订阅](https://azure.microsoft.com/free/)。  
3. 在订阅中创建 **Document Intelligence** 资源（曾用名：Form Recognizer）：  
   - 门户顶栏搜索「Document Intelligence」→ 创建。  
   - 选择订阅、资源组、区域、定价层（如 F0 免费层）。  
   - 记下创建后的 **终结点（Endpoint）** 和 **密钥（Key）**。

#### 步骤 2：获取 Endpoint 与 Key

1. 在 Azure 门户中进入刚创建的 Document Intelligence 资源。  
2. 左侧「密钥和终结点」：  
   - **终结点**：形如 `https://<your-resource-name>.cognitiveservices.azure.com/` → 用作 `DOCINTELLIGENCE_ENDPOINT`。  
   - **密钥 1（或密钥 2）** → 用作 `DOCINTELLIGENCE_KEY`。

#### 步骤 3：在项目中配置环境变量

**本地：**

```powershell
$env:DOCINTELLIGENCE_ENDPOINT = "https://<your-resource-name>.cognitiveservices.azure.com/"
$env:DOCINTELLIGENCE_KEY = "你的密钥"
```

**Docker 运行示例：**

```powershell
docker run -e OPENAI_API_KEY=sk-xxx `
  -e DOCINTELLIGENCE_ENDPOINT="https://xxx.cognitiveservices.azure.com/" `
  -e DOCINTELLIGENCE_KEY=你的密钥 `
  -p 8000:8000 rag-chatbot-mvp
```

**docker-compose 示例（若后续使用）：**

```yaml
services:
  app:
    image: rag-chatbot-mvp
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DOCINTELLIGENCE_ENDPOINT=${DOCINTELLIGENCE_ENDPOINT}
      - DOCINTELLIGENCE_KEY=${DOCINTELLIGENCE_KEY}
    ports:
      - "8000:8000"
```

主机上导出同名环境变量，或使用 `.env` 文件配合 `docker-compose --env-file .env`。

#### 步骤 4：在接口中选用 Azure 解析

上传 PDF 时显式使用 Azure 解析：

```bash
curl -X POST "http://localhost:8000/admin/documents/upload?parser=azure" -F "file=@/path/to/file.pdf"
```

不传 `parser` 或传 `parser=local` 时仍使用本地 pypdf。

---

### 3.2 Azure OpenAI（对话 + Embedding，当前已使用）

用于 `POST /v1/chat/answer` 的 LLM 对话，以及 PDF 上传后的 chunk 向量化与检索。

#### 步骤 1：在 Azure 创建 OpenAI 资源

1. 打开 [Azure 门户](https://portal.azure.com/) → 搜索「Azure OpenAI」→ 创建资源。  
2. 选择订阅、资源组、区域、定价层。  
3. 创建完成后，进入该资源 → **密钥和终结点**：复制 **终结点** 和 **密钥 1**。

#### 步骤 2：部署模型

1. 进入同一 Azure OpenAI 资源 → **模型部署**（或通过 Azure OpenAI Studio）。  
2. 部署**聊天模型**：部署名例如 `gpt-4o-mini`，模型选 `gpt-4o-mini`。  
3. 部署 **Embedding 模型**：部署名例如 `text-embedding-3-small`，模型选 `text-embedding-3-small`（或 `text-embedding-ada-002`）。

#### 步骤 3：在项目中配置 .env

在项目根目录创建或编辑 `.env`：

```env
AZURE_OPENAI_ENDPOINT=https://<your-resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=你的密钥
AZURE_OPENAI_CHAT_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=text-embedding-3-small
AZURE_OPENAI_API_VERSION=2024-02-01
```

重启服务后，对话与 PDF 向量检索会使用上述配置。

---

### 3.3 其他可能接入的 Azure 服务（预留）

若后续增加以下能力，可在此补充对应环境变量与配置步骤，保持本文档为唯一入口：

- **Azure OpenAI**  
  已接入：通过 `AZURE_OPENAI_*` 配置对话与 Embedding，详见上文 3.2。

- **Azure 存储（Blob）**  
  若将上传的 PDF 或生成的文档存到 Blob，可能新增：  
  `AZURE_STORAGE_CONNECTION_STRING` 或 `AZURE_STORAGE_ACCOUNT_KEY` 等。

- **其他（如 Azure 认知搜索、Key Vault）**  
  每增加一项服务，建议在本文档「环境变量一览」中增加一行，并在下方新增一小节写清：创建何种资源、在何处拿到密钥/端点、在项目中如何配置（本地 / Docker / 生产）。

---

## 四、配置检查清单（可选）

- [ ] 本地开发：已设置 `OPENAI_API_KEY`，能正常调用 `POST /v1/chat/answer`。  
- [ ] 本地开发：未设置 Azure 相关变量，上传 PDF 使用默认本地解析。  
- [ ] 接入 Azure Document Intelligence：已在 Azure 创建资源并配置 `DOCINTELLIGENCE_ENDPOINT`、`DOCINTELLIGENCE_KEY`。  
- [ ] 接入 Azure Document Intelligence：上传时使用 `?parser=azure` 并验证解析与入库正常。  
- [ ] 若使用 Docker：所需环境变量已通过 `-e` 或 compose 的 `environment` 传入。

---

## 五、安全与注意事项

- 不要将 `OPENAI_API_KEY`、`DOCINTELLIGENCE_KEY` 等写入代码或提交到 Git。  
- 本项目 `.gitignore` 已包含 `.env`、`.env.*`，敏感配置请放在环境变量或本地 `.env`（且不要提交）。  
- 生产环境建议使用托管密钥服务（如 Azure Key Vault）注入密钥，而非明文环境变量。
