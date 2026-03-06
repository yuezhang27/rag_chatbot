### Day 1: 搞清楚 "RAG 到底在干什么"

**目标：** 在你现有 MVP 上，亲手体验 RAG 的核心流程，理解每一步在做什么。

**需求引导**

- 在Day 0的极简MVP之后，我们已经有了

1. 准备好的 policy.txt文档作为知识库
2. 函数`load_policy_into_docs`:读取policy.txt文档 → 按固定长度（400）切分成 chunks → 存入 SQLite
3. 函数`retrieve_docs`：从 SQLite 中用 `LIKE` 关键词搜索最相关的 chunks

```python
pattern = f"%{question}%"
    cursor.execute(
        """
        SELECT id, title, chunk
        FROM docs
        WHERE chunk LIKE ? OR title LIKE ?
        LIMIT ?
        """,
        (pattern, pattern, top_k),
    )
    rows = cursor.fetchall()
```

4. `chat_answer`（接收POST请求的API`"/v1/chat/answer"`） 会调用上面的函数`retrieve_docs`，找到和question最相关（用LIKE）的top_k个结果→ 拼进 prompt → 调 OpenAI 生成回答

5. 在`chat_answer`函数里，在回答中返回 citations（引用了哪些 chunk）

```python
citations.append(
            Citation(
                doc_id=row["id"],
                title=row["title"],
                snippet=snippet,
            )
        )
```

**你要做的事：**

0. 显然，业务上，是用户（例如公司的需求部门）自行上传pdf，docx等文件，而不是所有东西写入policy.txt
1. 准备 3-5 个 PDF 文档作为知识库（比如一家虚构公司的员工手册、福利政策、岗位描述——和原项目一致）
2. 写一个 `scripts/prepdocs/pdfparser.py` 脚本：读取 PDF → 提取文本
3. 写一个`scripts/prepdocs/textsplitter.py`，：把前面提取到的文本，切分成 chunks → 存入 SQLite
4. 根据需求清单，需求部门上传的全部是PDF，且强结构（结构需要提前确认好），因此不应该按照token去chunking，而是应该结构感知分块（Structure-aware / Recursive）去进行chunking

```text
目前，在app.py里，我在函数`load_policy_into_docs`:固定读取policy.txt文档 → 按固定长度（400）切分成 chunks → 存入 SQLite

我希望更新功能：
1. 允许管理员上传PDF文档
2. 修正我的 `scripts/prepdocs/pdfparser.py` 脚本：读取 PDF → 提取文本。支持local parser和azure的parser
3. 写一个`scripts/prepdocs/textsplitter.py`，：把前面提取到的文本，切分成 chunks → 存入 SQLite
4. 切分chunks的方式，请采用Structure-aware / Recursive去进行chunking。也可以结合token数+overlap。总之符合保险公司内部pdf文档RAG的需求。

最终实现，项目跑起来之后，管理员可以上传一些pdf，这些内容会被自动读取+chunking+写入SQLite。考虑到上传pdf是每周五才进行，这里可以不加worker

你完成之后，请告诉我，我该怎么跑起来，并且如何测试这个新增功能

```

**Day 1 结束时你应该能：**

- `python scripts/prepdocs.py` 把 PDF 处理成 chunks 存入数据库
- `curl POST /v1/chat/answer` 能基于文档内容回答问题（虽然检索效果差）
- 理解 RAG = Retrieval + Augmented Generation，你亲手实现了最朴素的版本

**AI 概念：** RAG 基本流程、文档 Chunking、Prompt Stuffing

**引导思考：**

> 试着问你的系统："What is the dental coverage?" 然后再问 "Tell me about vision benefits"。  
> 你会发现，用 `LIKE` 关键词搜索的效果很差——"dental coverage" 搜不到 "teeth cleaning reimbursement" 相关的内容。  
> **问题**：关键词搜索无法理解语义，同义词、换一种说法就搜不到了。  
> **这就是为什么我们需要 Vector Search——这是明天要解决的。**

## Day 1。

---

## 1. 已实现内容

- **管理员上传 PDF**
  - 接口：`POST /admin/documents/upload`，表单字段 `file`（PDF 文件）。
  - 流程：接收 PDF → 解析正文 → 结构感知分块 → 写入 SQLite `docs` 表（同步，无 worker）。

- **`scripts/prepdocs/pdfparser.py`（新）**
  - `parse_pdf(content, filename, backend="local"|"azure", **kwargs) -> str`
  - **local**：用 pypdf 抽文本。
  - **azure**：用 Azure Document Intelligence 抽文本，需环境变量 `DOCINTELLIGENCE_ENDPOINT`、`DOCINTELLIGENCE_KEY`。

- **`scripts/prepdocs/textsplitter.py`**
  - 结构感知、递归分块：先按 `\n\n` → `\n` → `。` / `. ` → 空格 切，再按 `chunk_size`/`chunk_overlap` 合并。
  - `split_text(text, chunk_size=600, chunk_overlap=80, separators=...) -> List[str]`，适合保险/内部 PDF 的 RAG。

- **依赖**
  - `requirements.txt` 已增加：`pypdf`、`python-multipart`、`azure-ai-documentintelligence`、`azure-core`。

说明：原来的 `parsepdf.py` 依赖不存在的 `.page`/`.parser`，没有去改它，而是新建了独立的 `pdfparser.py` 供 app 使用。

---

## 2. 如何跑起来

在项目根目录（有 `app.py` 的目录）：

```bash
pip install -r requirements.txt
$env:OPENAI_API_KEY = "你的key"   # PowerShell
python app.py
```

服务会在 `http://localhost:8000` 启动。

---

## 3. 如何测试“上传 PDF → 解析 → 分块 → 入库”

**（1）用 Swagger 上传（推荐）**

- 打开：`http://localhost:8000/docs`
- 找到 `POST /admin/documents/upload`
- 点击 Try it out → 选一个本机 PDF → Execute
- 看返回里的 `chunks_inserted`，表示写入的 chunk 数量。

**（2）用 curl（PowerShell）**

```powershell
curl.exe -X POST "http://localhost:8000/admin/documents/upload" -F "file=@C:\path\to\your.pdf"
```

默认使用 **local** 解析。若要用 Azure，先设好 `DOCINTELLIGENCE_ENDPOINT` 和 `DOCINTELLIGENCE_KEY`，然后：

```powershell
curl.exe -X POST "http://localhost:8000/admin/documents/upload?parser=azure" -F "file=@C:\path\to\your.pdf"
```

**（3）验证是否进库、能否被检索到**

- 再发一条聊天请求，问题里包含 PDF 里的内容，并开启检索，例如：

```powershell
curl.exe -X POST "http://localhost:8000/v1/chat/answer" -H "Content-Type: application/json" -d "{\"question\": \"你上传的 PDF 里某段关键内容是什么？\", \"use_retrieval\": true, \"top_k\": 3}"
```

- 或直接查库（若本机有 sqlite3）：

```bash
sqlite3 chatbot.db "SELECT id, title, length(chunk) FROM docs;"
```

能看到新 PDF 的 `title` 和对应 chunk 即表示“上传 → 解析 → 分块 → 写入 SQLite”全流程已跑通。
