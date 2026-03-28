## 保险公司规章制度 RAG：选哪个chunking？

**选结构感知分块（Recursive）。**

原因很直接：保险公司的规章制度 PDF 本质上是人写的正式文件，天然有层级结构——章、节、条款、子条款。固定大小分块会把"第三条 理赔流程"切成两半，一半在 chunk 5，一半在 chunk 6，检索时两个都检索不完整。

但有一个前提需要确认 👇

---

### 关键问题：PDF 质量

```
PDF 是怎么来的？
├─ Word/排版软件直接导出 → 文字可提取 → Recursive 效果好 ✅
└─ 扫描件/拍照转 PDF    → 需要先 OCR   → OCR 质量决定上限 ⚠️
```

如果是扫描件，要先过一层 OCR（如 AWS Textract、Azure Document Intelligence），之后再做分块。

---

## Recursive Chunking 实操例子

假设有这样一段保险条款文本（从 PDF 提取后）：

```
第二章 理赔管理

第五条 理赔申请条件
被保险人发生保险事故后，应在事故发生之日起三十日内向本公司提出理赔申请。

第六条 理赔所需材料
6.1 基本材料
申请人需提交以下基本材料：
身份证明文件、保险合同原件、事故证明材料。

6.2 医疗类理赔补充材料
医疗机构出具的诊断证明、住院记录、费用清单。
```

### Recursive 的分块逻辑

分隔符优先级从高到低依次尝试：

```python
separators = [
    "\n第.章",   # 章级别（最优先）
    "\n第.条",   # 条级别
    "\n\d+\.\d+", # 子条款级别
    "\n\n",      # 段落
    "\n",        # 换行
    "。",        # 句子（最后兜底）
]
```

**分块过程：**

```
原文 2000 字，chunk_size = 500
              ↓
先尝试按"第X章"切 → 切出"第二章"整体 = 800字，超限
              ↓
退而按"第X条"切 → 切出"第五条" = 120字 ✅，"第六条" = 280字 ✅
              ↓
"第六条"内按"\n\d+\.\d+"切 → "6.1基本材料" / "6.2医疗类"分别成chunk
```

**最终 chunks：**

```
Chunk A: "第五条 理赔申请条件\n被保险人发生保险事故后..."
Chunk B: "第六条 理赔所需材料\n6.1 基本材料\n申请人需提交..."
Chunk C: "第六条 理赔所需材料\n6.2 医疗类理赔补充材料\n医疗机构出具..."
```

注意 Chunk C 保留了"第六条"的标题作为上下文，这是 overlap 的作用。

---

### 用 LangChain 实现

```python
from langchain.text_splitter import RecursiveCharacterTextSplitter

splitter = RecursiveCharacterTextSplitter(
    # 保险条款句子较长，500-800 是合理范围
    chunk_size=600,
    chunk_overlap=80,
    separators=[
        "\n第", # 匹配"第X章"/"第X条"
        "\n\n",
        "\n",
        "。",
        ""      # 最后兜底：强制按字符切
    ]
)

chunks = splitter.split_text(extracted_pdf_text)
```

---

### 保险场景额外建议

每个 chunk 存入向量库时，**把元数据一起存**：

```python
{
  "content": "第五条 理赔申请条件...",
  "metadata": {
    "source": "理赔管理办法2024版.pdf",
    "chapter": "第二章 理赔管理",
    "page": 8
  }
}
```

这样用户问"理赔需要什么材料"时，回答可以附上"来源：理赔管理办法第六条"，大幅提升可信度——保险场景里这点很重要，出处可追溯。

### 关于chunking的选择：

## RAG 中的 Chunking 方案全景

### 最常见：固定大小分块（Fixed-size Chunking）

最主流的方案是**按 token 数固定分块**，通常配合 overlap（重叠）。

```
chunk_size = 512 tokens
overlap = 50-100 tokens
```

**为什么最常见？**

1. **实现极简** — 一行代码搞定，无需理解文本语义
2. **行为可预测** — chunk 数量、大小完全可控，便于调试和成本估算
3. **普适性强** — 不依赖文档格式、语言、领域
4. **工程成熟** — LangChain/LlamaIndex 默认就是这个，生态直接支持
5. **够用** — 在大多数场景下效果已经足够好，符合工程上的"奥卡姆剃刀"

overlap 的作用是避免一个语义完整的句子被切断后两半分别落在不同 chunk，导致检索时上下文断裂。

---

### 其他方案及为什么较少选

#### 1. 语义分块（Semantic Chunking）

按句子 embedding 的相似度动态切分，相似度骤降处就是边界。

- ✅ chunk 边界更自然，语义更完整
- ❌ **每次分块要跑 embedding 模型**，构建索引成本高出数倍
- ❌ chunk 大小不可控，后续处理复杂
- ❌ 对短文本/结构化文档收益不明显

#### 2. 结构感知分块（Structure-aware / Recursive）

按段落 → 句子 → 词逐级回退切分（LangChain 的 `RecursiveCharacterTextSplitter`）。

- ✅ 比纯固定大小更尊重文档结构
- ✅ 实现成本低，是固定分块的小改进
- ❌ 依赖文档有明确分隔符（`\n\n`、`#` 等），PDF/扫描件效果差
- 实际上这个方案已经很接近"最常见"，很多人把它和固定分块混用

#### 3. 文档结构分块（Document-structure Chunking）

解析 Markdown heading、HTML tag、PDF 书签，按章节切分。

- ✅ chunk 的语义边界最准确
- ❌ **强依赖文档质量**，野生 PDF、Word 转换后结构往往一团糟
- ❌ 需要针对每种格式写解析逻辑，维护成本高
- 适合内部知识库（文档规范）、技术文档场景

#### 4. 命题级分块（Propositional Chunking）

用 LLM 把文档分解为原子化陈述句（"X 是 Y"），每条命题作为一个 chunk。

- ✅ 检索精度极高，每个 chunk 语义密度最大
- ❌ **构建成本极高**，每个 chunk 都要调用 LLM
- ❌ 大规模文档库几乎不可行
- 学术研究（如 RAPTOR、Dense X）验证有效，工程落地少

#### 5. 父子分块（Parent-Child / Small-to-Big）

小 chunk 用于检索（精），大 chunk 作为上下文返回给 LLM（全）。

- ✅ 兼顾检索精度和上下文完整性
- ❌ 索引结构复杂，存储翻倍
- ❌ 实现和调试成本高于简单方案
- 算是目前**最值得投入的进阶方案**之一

---

### 选型决策树

```
文档格式规范且有清晰结构？
├─ Yes → 结构感知分块（按标题/段落）
└─ No ↓

对检索精度要求极高？预算充足？
├─ Yes → 父子分块 or 语义分块
└─ No → 固定大小 + overlap（默认选择）
```

---

**总结**：固定大小分块赢在"足够好 + 成本极低 + 可控"，其他方案都是在某个维度上更优，但都有额外代价。工程上的选择永远是 tradeoff，而不是追求最优解。

===

---

核心问题：RAG 的检索阶段在做什么？

用户问一个问题 → 你需要从几百页 PDF 里找出最相关的几段文字 → 把这几段喂给 LLM

"找出最相关的几段"这件事，需要一个专门的存储+搜索系统。这个系统就是检索后端。

---

什么是"索引"（Index）

类比：MySQL 的表。

你有很多文档碎片（chunks），每个 chunk 有：

- 文本内容
- 向量（embedding，一串数字，表示语义）
- 元数据（来自哪个文件、第几页）

索引就是存这些数据的地方，同时它知道怎么快速搜索。

索引 = 结构化的存储 + 搜索能力

本地 ChromaDB 有索引，Azure AI Search 也有索引，只是实现不同。

---

ChromaDB（本地）vs Azure AI Search（生产）

┌──────────┬─────────────────────────────┬──────────────────────────────────────────────────┐
│ │ ChromaDB │ Azure AI Search │
├──────────┼─────────────────────────────┼──────────────────────────────────────────────────┤
│ 跑在哪 │ 你电脑上的文件夹 chroma_db/ │ Azure 云端服务 │
├──────────┼─────────────────────────────┼──────────────────────────────────────────────────┤
│ 搜索方式 │ 纯向量检索（语义相似度） │ Hybrid：关键词 + 向量，再用 Semantic Ranker 精排 │
├──────────┼─────────────────────────────┼──────────────────────────────────────────────────┤
│ 数据在哪 │ chroma_db/ 目录里的文件 │ Azure 服务器上 │
├──────────┼─────────────────────────────┼──────────────────────────────────────────────────┤
│ 适合场景 │ 本地开发调试 │ 生产，效果更好 │
└──────────┴─────────────────────────────┴──────────────────────────────────────────────────┘

两者数据完全独立，没有同步关系。 你往 ChromaDB 写的数据，Azure 不知道；往 Azure 写的数据，ChromaDB 也不知道。

---

为什么要先跑 prepdocs？

流程分两个阶段，缺一不可：

阶段1：Ingestion（入库）
PDF 文件 → 分块 → 生成 embedding → 写入检索后端（Chroma 或 Azure）

阶段2：检索（查询时）
用户问题 → 生成 embedding → 去检索后端搜索 → 返回相关 chunks

类比：先建图书馆书架，再去找书。

如果你没有跑 ingestion，检索后端是空的，用户问什么都找不到，LLM 会说"没有资料支持"。

你之前在本地模式下跑过 prepdocs，所以 chroma_db/ 里有数据。但 Azure 索引是刚创建的空的，所以切换到 azure 后需要重新跑一次 prepdocs，这次数据会写进 Azure。

---

完整数据流（你的项目）

【一次性准备】

data/\*.pdf
↓ prepdocs.py
├─ PyMuPDF 按页提取文本
├─ 分块（400字符，80重叠）
├─ 调用 Azure OpenAI Embedding API → 每个chunk变成1536维向量
└─ 写入检索后端
├─ SEARCH_BACKEND=local → 写 chroma_db/ 文件夹
└─ SEARCH_BACKEND=azure → 写 Azure AI Search 索引

【每次用户问问题】

用户输入
↓ app.py
├─ 调用 Embedding API → 问题变成向量
├─ 去检索后端搜索
│ ├─ local: ChromaDB 向量相似度，返回 top-5
│ └─ azure: BM25关键词 + 向量 → 召回top-20 → Semantic Ranker精排 → top-5
├─ 把 top-5 的文本拼进 prompt
└─ 调用 Azure OpenAI Chat API → LLM 生成回答 → SSE 流式返回

---

为什么切换后端只改一行环境变量就够了？

这就是今天实现的 Adapter 模式的作用。

app.py 只调用 get_search_client().search()，不关心后端是谁。工厂函数读 SEARCH_BACKEND 决定给你一个 ChromaSearchClient 还是
AzureSearchClient，两者对外接口完全一样。

SEARCH_BACKEND=local → ChromaSearchClient → chroma_db/
SEARCH_BACKEND=azure → AzureSearchClient → Azure 云端
↑
上层代码完全不感知差异
