# CodeSentinel — 技术实现文档

> **适用对象：** 负责部署、维护或二次开发的工程师  
> **版本：** v0.3.0  

---

## 一、系统架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                      客户端入口                                  │
│   Streamlit UI (8501)    FastAPI REST (8000)    MCP (扩展)      │
└──────────────────┬──────────────────┬───────────────────────────┘
                   │                  │
        ┌──────────▼──────────────────▼──────────┐
        │           FastAPI Application           │
        │         app/api/routes.py               │
        └──────────────────┬──────────────────────┘
                           │
        ┌──────────────────▼──────────────────────┐
        │         LangGraph Agent Pipeline         │
        │   plan_node → retrieve_node → review_node│
        │         app/agent/graph.py               │
        └────────┬──────────────┬─────────────────┘
                 │              │
    ┌────────────▼──┐    ┌──────▼──────────────────┐
    │  PlannerAgent │    │     RetrieverAgent        │
    │  (GPT-4o-mini)│    │  (VectorStore search)     │
    │  planner.py   │    │  retriever.py             │
    └───────────────┘    └──────────┬────────────────┘
                                    │
                   ┌────────────────▼──────────────────┐
                   │            VectorStore             │
                   │     ChromaDB + BM25 混合检索        │
                   │     vectordb.py                    │
                   └──────────┬──────────────┬──────────┘
                              │              │
              ┌───────────────▼──┐  ┌────────▼──────────┐
              │  Chroma HNSW     │  │  BM25Retriever     │
              │  (持久化)        │  │  (内存，进程生命期) │
              └──────────────────┘  └───────────────────┘
                   ↑ add_documents
        ┌──────────┴──────────────────────────────────┐
        │              索引管道                         │
        │  RepoLoader (代码)    DocChunker (文档)       │
        │  services/repo_loader.py  rag/doc_chunker.py │
        └──────────┬──────────────────────────────────┘
                   │
        ┌──────────▼──────────────────────────────────┐
        │              解析 / 分块                       │
        │  CodeParser (tree-sitter AST)                 │
        │  MarkdownHeaderTextSplitter / PDFMinerLoader  │
        └─────────────────────────────────────────────┘
```

---

## 二、核心模块说明

### 2.1 配置管理 — `app/config.py`

所有运行时参数通过环境变量注入，`.env` 文件自动加载。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | — | **必填**，OpenAI 密钥 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 使用的 LLM 模型 |
| `CHROMA_PERSIST_DIR` | `./data/chroma` | ChromaDB 持久化目录 |
| `CHUNK_SIZE_LINES` | `50` | AST fallback 时每块的行数 |
| `RETRIEVER_TOP_K` | `5` | 检索返回的最大 chunk 数 |
| `VECTOR_WEIGHT` | `0.6` | RRF 中向量检索的权重 |
| `BM25_WEIGHT` | `0.4` | RRF 中 BM25 的权重（与 VECTOR_WEIGHT 之和应为 1.0） |
| `DOC_CHUNK_SIZE` | `1000` | 文档字符级分块大小 |
| `DOC_CHUNK_OVERLAP` | `100` | 文档分块重叠字符数 |
| `PLANNER_MAX_TOKENS` | `2048` | Planner LLM 最大输出 token |
| `REVIEWER_MAX_TOKENS` | `4096` | Reviewer LLM 最大输出 token |

---

### 2.2 代码解析管道

#### `app/rag/parser.py` — AST 解析器

使用 **tree-sitter 0.25.1** 对 Python 源码做语法树解析，提取顶层函数和类定义作为语义单元（`CodeUnit`）。

**关键实现细节：**
- tree-sitter 新版 API：语言通过构造函数传入 `TSParser(PY_LANGUAGE)`，而非 `.language = ...`
- 行号统一转换为 **1-based**（tree-sitter 原始返回 0-based）
- tree-sitter 不可用时（导入失败）自动降级为行分割 fallback
- fallback 的 `kind` 字段值为 `"block"`，AST 解析的为 `"function"` 或 `"class"`

```python
# 正确的 tree-sitter 初始化（修复了原始 bug）
PY_LANGUAGE = Language(tspython.language())
_parser = TSParser(PY_LANGUAGE)  # 不是 _parser.language = PY_LANGUAGE
```

#### `app/rag/chunker.py` — Code → Document 转换

将 `CodeUnit` 转为 LangChain `Document`，page_content 格式：
```
# app/auth.py  [function: verify_token]

def verify_token(token: str) -> dict:
    ...
```

metadata 字段：`file`, `name`, `kind`, `start_line`, `end_line`

> ⚠️ `source_type` 字段由 `indexing.py` 在索引时统一打标，不在 chunker 里设置。

#### `app/rag/doc_chunker.py` — 文档分块器

两种文档类型的分块策略不同：

**Markdown：**
1. Phase 1：`MarkdownHeaderTextSplitter` 按 H1/H2/H3 切分，每 section 保留 metadata（`h1`, `h2`, `h3`）
2. Phase 2：每个 section 如果超过 `DOC_CHUNK_SIZE` 字符，再用 `RecursiveCharacterTextSplitter` 细切
3. 生成面包屑路径：`"h1 > h2 > h3"` 存入 `section` 字段

**PDF：**
1. 使用 `PDFMinerLoader`（布局感知，段落边界保留比 PyPDF 好）
2. 过滤 `_is_boilerplate()` 行（页码、URL、版权信息）
3. 标准化 metadata：`page_number` → `page`

**TXT：** `open(encoding='utf-8', errors='replace')` 读取全文，直接字符分块，metadata `section=""`, `page=0`。

**DOCX（Word）：** `Docx2txtLoader` 提取段落文本，字符分块。`docx2txt` 不可用时降级 `python-docx`。

**XLSX / XLS（Excel）：** `openpyxl` 遍历所有 sheet，每 sheet 转 TSV 文本块（`[Sheet: 名称]\n列1\t列2\t...`），metadata `section=<sheet名>`, `page=<sheet索引>`。空 sheet 跳过。

**所有文档 chunk 的 metadata 固定字段：**
`source_type="doc"`, `doc_id`, `filename`, `version`, `page`, `section`

---

### 2.3 向量数据库 — `app/rag/vectordb.py`

#### 存储层

单一 ChromaDB collection `"codebase"`，持久化到 `CHROMA_PERSIST_DIR`。

所有写操作分批（每批 500 条），避免触发 Chroma 的 SQLite 参数绑定上限（~5461）。

#### 双检索器设计

```
查询 q
  │
  ├─ BM25Retriever.from_documents(corpus, k=K)  ← 从内存 corpus 建索引
  │    基于词频统计，精确匹配标识符、变量名、错误码
  │
  └─ Chroma.as_retriever(k=K)                   ← HNSW 向量搜索
       基于语义相似度，自然语言问题效果好
       │
EnsembleRetriever (RRF fusion)
  score(d) = Σ  weight_i / (rank_i(d) + 60)
  weights = [BM25_WEIGHT, VECTOR_WEIGHT]
```

#### 重要边界情况处理

| 情况 | 处理方式 |
|---|---|
| 索引为空时搜索 | `count() == 0` 时直接返回 `[]`，不触发 Chroma 内部异常 |
| 进程重启后 corpus 为空 | fallback 到纯向量搜索（BM25 需要内存 corpus） |
| `filter_search` 过滤某 source_type | BM25 corpus 和 Chroma filter 同步应用，保证两腿一致 |

#### 单例模式

`get_vector_store()` 使用模块级 `_store` 单例，FastAPI 多 worker 下每个进程独立持有一个实例。

---

### 2.4 Agent Pipeline — `app/agent/`

#### 状态流

```python
AgentState = {
    "query": str,
    "source_type": Optional[str],   # "code" | "doc" | None
    "plan": dict,                    # Planner 输出
    "sub_queries": List[str],        # Planner 分解出的子查询
    "retrieved_context": str,        # 拼接后的检索内容
    "answer": str,                   # Reviewer 输出
}
```

#### PlannerAgent

- 调用 LLM，将用户查询分解为 2–3 个子查询 + 提取意图
- LLM 输出 JSON，`_parse_plan_json()` 支持带/不带 markdown fence 的解析
- JSON 解析失败时 fallback：返回原始查询，`intent="find_definition"`
- prompt 从 `app/prompts/planner.txt` 加载（`encoding='utf-8'` 防 Windows GBK 问题）

#### RetrieverAgent

- 对每个子查询独立调用 `VectorStore.search()`
- 根据 `source_type` 选择 `search()` 或 `filter_search()`
- 跨子查询去重：代码 chunk 按 `(file, start_line)` 去重；文档 chunk 按 `(doc_id, page, section)` 去重

#### ReviewAgent

- 将检索结果格式化为带 header 的上下文字符串传给 LLM
- 使用 `.replace()` 而非 `str.format()`，防止代码上下文中的 `{}` 被误解析
- prompt 要求输出固定结构：Answer / Relevant locations / Confidence

---

### 2.5 API 层 — `app/api/routes.py`

**关键设计决策：**

1. **统一错误处理**：所有端点用 `try/except` 包裹，非 `HTTPException` 的异常转为 500，给出 `Agent pipeline failed: ...` 描述
2. **OPENAI_API_KEY 前置检查**：所有需要 LLM 的端点在执行前检查 key，返回 503 而非 500
3. **index/local 使用 `reset=True`**：每次索引代码仓库会清空整个 store（已知局限，见 TODO）
4. **index/document 不 reset**：文档 chunk 累积，与代码 chunk 共存

**`POST /api/v1/index/upload` — 文件上传：**

接受 `multipart/form-data`，字段名 `file`。支持 `.pdf`, `.md`, `.txt`, `.docx`, `.xlsx`, `.xls`，大小限制 50 MB（`MAX_UPLOAD_BYTES`）。文件落盘到 `UPLOAD_DIR` 后索引，索引完成后立即删除原文件。

```json
{ "indexed_chunks": 42, "doc_id": "uuid...", "filename": "spec.pdf", "source_type": "doc" }
```

**`/stats` 端点字段说明：**
```json
{
  "total_chunks": 342,
  "code_chunks": 280,
  "doc_chunks": 62,
  "hybrid_search_active": true,    // false 表示进程重启后 corpus 丢失，混合检索降级为纯向量
  "chroma_dir": "./data/chroma",
  "vector_weight": 0.6,
  "bm25_weight": 0.4,
  "openai_configured": true
}
```

---

## 三、数据流

### 3.1 索引流程（`POST /index/local`）

```
repo_path
  └─ validate_path()                        # 验证路径存在且是目录
       └─ RepoLoader.load_repo()
            ├─ os.walk() 遍历所有 .py 文件
            ├─ 过滤: SKIP_DIR_NAMES, MAX_FILE_BYTES
            ├─ CodeParser.parse()           # tree-sitter AST → CodeUnit[]
            └─ CodeChunker.chunk_code()     # CodeUnit → Document[]
                  metadata: file, kind, name, start_line, end_line
  └─ 批量打标 source_type="code"
       └─ VectorStore.reset()              # 清空旧代码索引
            └─ VectorStore.add_documents() # 分批写入 Chroma
                  内存 corpus 同步追加
```

### 3.2 查询流程（`POST /ask`）

```
query + source_type
  └─ run_agent()
       └─ LangGraph graph.invoke()
            ├─ plan_node
            │    └─ PlannerAgent.plan()
            │         LLM → JSON → sub_queries[]
            ├─ retrieve_node
            │    └─ RetrieverAgent.retrieve_multi(sub_queries)
            │         └─ 每个 sub_query:
            │              VectorStore.search() 或 .filter_search()
            │                ├─ BM25Retriever (corpus)
            │                └─ Chroma.as_retriever (HNSW)
            │                     EnsembleRetriever (RRF)
            │         去重 → retrieved_context (格式化字符串)
            └─ review_node
                 └─ ReviewAgent.review()
                      LLM (query + context) → markdown answer
```

---

## 四、依赖版本锁定（关键）

```toml
langchain==1.3.2
langchain-core>=1.4.0,<2.0.0
langchain-community==0.4.1
langchain-openai==1.0.3
langchain-chroma==1.0.0
langchain-classic>=0.1.0      # EnsembleRetriever 所在包
langgraph>=1.2.2,<1.3.0
chromadb==1.0.21
```

**重要：** `EnsembleRetriever` 在 langchain 1.x 生态中位于 `langchain_classic.retrievers`，不在 `langchain_community` 或 `langchain.retrievers`。`langchain-classic` 必须显式声明在依赖中。

安装命令（需指定备用源以防 langchain 相关包在清华源同步延迟）：
```bash
pip install -e . --extra-index-url https://pypi.org/simple
```

---

## 五、已知局限与 TODO

| 问题 | 影响 | 优先级 |
|---|---|---|
| 进程重启后 BM25 corpus 丢失，降级为纯向量 | 检索质量轻微下降 | 中 |
| `index/local` 每次 reset，不支持多仓库增量索引 | 功能限制 | 中 |
| `VectorStore` 全局单例，多线程 `reset()` 有竞态条件 | 仅影响并发 reset 场景 | 低 |
| IVF 索引在 chunk 数量不足时会崩溃或质量差 | 仅影响使用 `INDEX_TYPE=ivf` 时 | 低 |
| `RepoLoader` 只支持 `.py` 文件 | 无法索引多语言仓库 | 中 |

**已解决（见 DEVLOG.md 阶段九）：**
- ~~文档版本 metadata 内存存储，重启丢失~~ → `app/db.py` + `app/db_models.py` 持久化到 SQLite/MySQL/Postgres，含内容 hash 去重
- ~~`cross_search` 无 cross-encoder re-ranking~~ → 这条描述本身就是过时的（文档滞后于代码）：`rerank_node` 对 `retrieve_node` 返回的 `_raw_docs` 统一重排，不区分 `source_type`，跨源查询同样经过 cross-encoder/LLM 精排

---

## 六、部署说明

### Docker 部署

```bash
docker-compose up --build
```

`docker-compose.yml` 会启动：
- `api` 服务（FastAPI，端口 8000）
- `frontend` 服务（Streamlit，端口 8501）
- 共享 volume 挂载 ChromaDB 数据目录

### 生产注意事项

1. **ChromaDB 并发**：当前使用 `PersistentClient`，单进程安全。多进程（`uvicorn --workers N`）下每个进程独立持有 Chroma 实例，写操作可能冲突。生产环境建议使用 Chroma 的 HTTP server 模式。

2. **OpenAI 费用估算**：
   - 索引：`text-embedding-3-small` 约 $0.02/1M tokens。1000 个 chunk，每 chunk 约 200 tokens，共约 200K tokens，费用约 $0.004
   - 查询：`gpt-4o-mini` 约 $0.15/1M input tokens。每次查询约 3000 tokens context，费用约 $0.0005/次

3. **数据目录备份**：`./data/chroma` 包含所有索引数据，定期备份此目录即可恢复完整索引。

---

## 七、扩展开发指南

### 添加新语言支持

1. 安装对应 tree-sitter grammar：`pip install tree-sitter-javascript`
2. 在 `app/rag/parser.py` 中按文件扩展名选择 grammar
3. `RepoLoader` 的文件过滤逻辑加入新扩展名

### 接入新 LLM

1. `app/config.py` 修改 `OPENAI_MODEL`，或扩展为支持其他 provider
2. `PlannerAgent` 和 `ReviewAgent` 的 `ChatOpenAI` 替换为对应的 LangChain provider

### 添加 MCP Server 支持

项目架构支持将 `run_agent()` 封装为 MCP tool，让 Claude Desktop 等客户端直接调用；可在 `app/` 下新增一个 `mcp_server.py` 入口来承载该封装（当前未实现）。

---

*文档更新：v0.2.1 | 与代码同步维护*
