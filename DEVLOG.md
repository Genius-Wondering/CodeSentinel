# DevAssistant — 开发日志

记录开发过程中遇到的实际问题、排查思路、以及每个设计决策背后的原因。
按时间顺序，粗糙但真实。

---

## 阶段一：CodeSentinel 代码审查与修复

### [BUG] tree-sitter Parser 初始化方式错误

**现象：** 导入 `app.rag.parser` 时偶发 `AttributeError`，在某些 tree-sitter 版本下完全无法解析，静默回退到 fallback（行分割）导致 AST chunking 形同虚设。

**根因：** 原代码写的是：
```python
_parser = TSParser()
_parser.language = PY_LANGUAGE
```
`.language` 是旧版 tree-sitter（< 0.20）的 API，新版本要求通过构造函数传入：
```python
_parser = TSParser(PY_LANGUAGE)
```
属性赋值在新版本不会报错，只是不生效，导致解析器没有绑定语言，`parse()` 返回空树，`root.children` 为空，`units` 为空，然后 `if units` 为 False，静默进入 `_parse_fallback`。

**排查过程：** 发现线上 chunk 全部是 `block_0, block_50, block_100` 这种 fallback 产物，没有任何 `function` 或 `class` 类型，才意识到 AST 解析从来没有真正跑起来过。

**修复：** 构造函数传参。同时在 `except Exception` 里加日志，避免下次静默失败。

---

### [BUG] 行号是 0-based，但引用是 1-based

**现象：** Reviewer 输出的引用 `auth.py lines 0–23` 在编辑器里对不上，实际内容在第 1–24 行。

**根因：** tree-sitter 的 `node.start_point[0]` 返回 0-based 行号（C 惯例）。原代码直接存入 metadata 没有转换，reviewer prompt 里写的是"cite as lines X–Y"，用户在编辑器按这个行号跳过去差了一行。

```python
# 原代码
start = node.start_point[0]   # 0-based
end = node.end_point[0]
units.append(CodeUnit(..., start_line=start, end_line=end))
```

fallback 的 `_parse_fallback` 也有同样问题，并且 end_line 的边界也算错了（`min(i + chunk_lines - 1, len(lines) - 1)` 在 0-based 下是对的，但与其他地方混用后语义不一致）。

**修复：** 统一在存入 `CodeUnit` 时 +1，所有下游代码消费的都是 1-based。文档注释里明确标注。

---

### [BUG] `_parse_fallback` 的 docstring 放在了赋值语句前面

**现象：** `chunk_lines` 参数有默认值逻辑，但实际上走了一遍才发现赋值顺序有问题。

**原代码：**
```python
def _parse_fallback(self, code: str, chunk_lines: int | None = None) -> List[CodeUnit]:
    chunk_lines = chunk_lines or config.CHUNK_SIZE_LINES
    """Fallback parser: ..."""   # ← docstring 在赋值之后，Python 把它当普通字符串表达式，不影响运行
```

实际运行不会崩溃（Python 的 docstring 必须是函数体第一个语句才生效，放在后面只是一个无用的字符串字面量），但意图非常容易误读，review 时花了额外时间确认。调整顺序，docstring 移到最前。

---

### [BUG] Chroma `add_documents` 无批量分片，大仓库会超限

**现象：** 索引一个 200+ Python 文件的仓库时，`add_documents` 抛出 `chromadb.errors.InvalidArgumentError: Batch size ... exceeds maximum`。

**根因：** Chroma 默认单次 `add` 上限是 5461 条（由 SQLite 的参数绑定数量限制推导而来，每条记录占多个字段）。原代码直接 `self.db.add_documents(docs)` 一次传全部，仓库稍大就炸。

**修复：** 每批 500 条循环插入：
```python
for i in range(0, len(docs), 500):
    self.db.add_documents(docs[i:i + 500])
```
500 是经验值，留了足够的余量，也足够让进度条有意义。

---

### [BUG] 空集合上执行 `similarity_search` 报内部错误

**现象：** 刚启动还没索引任何仓库时，直接调用 `/ask` 接口（绕过前端检查），Chroma 抛 `IndexError` 而不是返回空列表，导致 500 而不是友好的 400。

**根因：** Chroma 的 HNSW 实现在集合为空时调用 `search` 会尝试访问不存在的索引节点。API 层虽然有 `store.count() == 0` 的检查，但 `VectorStore.search()` 本身没有防护，被直接调用时不安全。

**修复：** 在 `search()` 内部加守卫：
```python
def search(self, query: str, k: int = None) -> List[Document]:
    if self.count() == 0:
        return []
    ...
```
防御性编程，不依赖调用方一定做了检查。

---

### [安全] GitHub Token 注入用字符串替换，存在边缘情况

**现象：** 功能上没问题，但 code review 时发现这行有潜在风险：
```python
return url.replace("https://", f"https://{token}@")
```

**问题：** 如果 URL 里包含多个 `https://`（比如 redirect 参数 `?next=https://...`），`replace` 会替换所有出现的位置，导致 URL 损坏。虽然 GitHub repo URL 实际上不会出现这种情况，但作为库函数这是一个隐患，面试被追问"有没有考虑边界情况"时说不清楚。

**修复：** 用 `urlparse` 只修改 `netloc` 部分：
```python
parsed = urlparse(url)
authenticated = parsed._replace(netloc=f"{token}@{parsed.netloc}")
return urlunparse(authenticated)
```

---

### [设计] 文件路径存绝对路径导致索引不可迁移

**现象：** 在 A 机器上索引的仓库，把 Chroma 数据目录复制到 B 机器上，所有引用都是 A 机器的绝对路径（`/Users/alice/projects/myrepo/auth.py`），在 B 机器上完全无意义。

**根因：** `repo_loader.py` 传给 chunker 的是 `full_path`（`os.path.join(root, file)`），是绝对路径。

**修复：** 改成相对于仓库根目录的相对路径：
```python
rel_path = os.path.relpath(full_path, repo_path)
docs = self.chunker.chunk_code(code, rel_path)
```
这样存进 Chroma 的 `file` 字段是 `app/agent/graph.py` 而不是 `/Users/alice/...`，更可读，也更可迁移。

---

### [设计] 模块导入时实例化 Agent，影响测试和冷启动

**原代码（`graph.py` 顶层）：**
```python
planner = PlannerAgent()    # 导入时就建立 OpenAI 客户端连接
retriever = RetrieverAgent() # 导入时就连接 ChromaDB
reviewer = ReviewAgent()
```

**问题：** 任何 `import app.agent.graph` 的地方都会触发这三个初始化，包括写测试时的 `from app.agent.graph import AgentState`。没有 `OPENAI_API_KEY` 的环境（CI、单元测试）会立刻报错，即使那个测试根本不调用 LLM。

**修复：** Agent 实例移到各个 node 函数内部，每次调用时按需创建。对于无状态的 Agent 来说开销可以忽略（主要是 Python 对象创建，不是网络连接），连接复用由底层 HTTP 客户端的连接池处理。

---

### [次要] `app/main.py` 版本号不一致

```python
# FastAPI 初始化
app = FastAPI(version="0.2.1")

# root 端点
return {"version": "0.2.0"}  # ← 忘记同步了
```

没有功能影响，但 `/docs` 页面显示 `0.2.1`，`/` 返回 `0.2.0`，容易在 debug 时造成困惑。统一到 `config.py` 里管一个 `VERSION` 常量是更好的做法（后续改）。

---

## 阶段二：架构重新审视

### [架构] `document_lifecycle_manager.py` 为什么不属于这个项目

审查代码时发现根目录有一个 700 行的独立脚本，顶部注释写了 "Not wired into FastAPI — for reference"。

具体问题：
- 用 FAISS 做存储层，项目主体用 ChromaDB，两套不兼容
- 用 HuggingFace embeddings（`BAAI/bge-small-zh`），项目主体用 OpenAI embeddings，向量空间不同，无法合并检索
- `metadata_db` 是内存字典，重启即丢失
- 依赖（`faiss-cpu`, `sentence-transformers`, `unstructured`）没有出现在 `requirements.txt` 里

结论：这是作者早期做 RAG 实验时写的草稿，后来换了技术栈，文件忘记清理了。

**处理方式：** 把有价值的逻辑（文档去重、版本管理、PDF/MD 加载）重写后合并进 `app/services/doc_service.py` 和 `app/rag/doc_chunker.py`，统一用 ChromaDB + OpenAI embeddings，原文件删除。

---

### [架构决策] ChromaDB vs FAISS 的本质区别，以及为什么最初的对比是错的

第一版 README 里把 ChromaDB 和 FAISS 列成两个可以互换的"向量库后端"，收到反馈说这个对比不准确。

**实际关系：**

FAISS 是 Facebook 开源的 **ANN（Approximate Nearest Neighbor）索引算法库**，核心功能是：给定一堆向量，建一个索引结构，然后对查询向量快速找 top-K 近邻。它不管向量从哪来、存到哪去、metadata 怎么处理、持久化怎么做。

ChromaDB 是一个**向量数据库**，它的职责是：存储向量 + 元数据，提供增删改查，处理持久化，并且内部用 `hnswlib`（HNSW 算法的 C++ 实现）做 ANN 搜索。

所以正确的对比维度是：

- **存储层：** ChromaDB vs Pinecone vs pgvector vs Weaviate（这才是"换后端"）
- **索引算法：** Flat（暴力精确）vs IVF（倒排分区）vs HNSW（图结构近邻）（这才是"换策略"）

**实际设计调整：**

不再提供"切换到 FAISS 存储后端"的选项（这需要自己实现 metadata 存储、持久化、删除，工作量大且没有必要），而是：

1. ChromaDB 作为唯一存储层，保持稳定
2. 暴露 `INDEX_TYPE=flat|ivf|hnsw` 配置，让用户感受三种搜索策略的延迟/召回差异
3. 在文档里解释 FAISS 在哪个场景下会真正用到（大规模时的两阶段检索：FAISS IVF 快速缩小候选集 → ChromaDB 按 ID 补全 metadata）

这个架构更诚实，也更容易在面试里说清楚。

---

### [架构决策] 为什么保留 Streamlit 而不是只做 MCP

考虑过把 Streamlit 去掉，只做 MCP Server（Claude Desktop 接入），但最终保留了，原因：

1. **展示场景不同。** MCP 演示需要对方装了 Claude Desktop，Streamlit 打开浏览器就能看。给面试官 demo 时不能假设对方的环境。

2. **功能侧重不同。** Streamlit 更适合展示"索引 + 查询"的完整流程，有进度条、有 agent plan 展开、有历史记录。MCP 更适合展示"工具调用集成"这个特性本身。

3. **简历上两者都算加分项。** MCP 是 2024 年底的新协议，会的人少；Streamlit 是 AI 应用标配，不写反而奇怪。

**结论：** 三层入口（Streamlit / FastAPI / MCP）共享同一个 agent pipeline，代码不重复，展示时灵活选择。

---

## 待解决的问题

**[TODO] `VectorStore` 是模块级单例，多线程不安全**

`get_vector_store()` 用全局 `_store` 变量做单例，FastAPI 在多 worker 下每个进程有自己的单例，问题不大。但如果未来切换到单进程多线程模式（`uvicorn --workers 1 --threads N`），并发 `reset()` 操作会有竞态条件。需要加锁或改成 per-request 模式。

**[TODO] `INDEX_TYPE=ivf` 需要训练步骤**

IVF 索引在建立时需要先用 k-means 对向量做聚类（训练），需要一定数量的样本才能跑（通常建议 > 39 × nlist）。目前如果 chunk 数量太少直接用 IVF 会报错或质量很差。需要在 `vectordb.py` 里加数量检查，chunk 不足时自动降级到 Flat。

**[TODO] cross_search 的结果合并没有 re-ranking**

目前 `cross_search` 是把代码 chunks 和文档 chunks 分别取 top-K 然后拼在一起，传给 Reviewer。没有统一的相关性排序，可能导致不相关的结果排在前面。正确做法是用 cross-encoder 对合并后的候选集做重排序，但这需要额外的模型调用，暂时接受这个局限。

**[TODO] 文档版本元数据没有持久化**

`doc_service.py` 的 `metadata_db` 是内存字典（从 `document_lifecycle_manager.py` 继承来的设计），重启后文档版本历史丢失，重新上传同一文件会被判断为新文档而不是更新。生产环境需要换成 SQLite 或 PostgreSQL。当前规模下（本地 demo）可以接受。

---

## 阶段三：多文档结构处理 + 混合索引

### [问题发现] 按字符数切块会破坏文档结构

**现象：** 给系统上传一份 API 设计文档（Markdown），问"限流策略是什么"，返回的 chunk 内容是"...超过限制后返回 429。\n\n## 错误码\n\n| 码 | 含义 |..."——限流的描述被截断，后半段和错误码表混在了同一个 chunk 里。

**根因：** 原来的 `document_lifecycle_manager.py` 用 `RecursiveCharacterTextSplitter(chunk_size=1000)` 无差别切块。这个分割器不理解文档结构，只看字符数。当"2.3 限流"这一节恰好在 1000 字符边界附近，就会被切断，上下文丢失。

更具体的问题：切出来的 chunk 的 metadata 只有 `page: 0`，没有任何标题信息。Reviewer 拿到这段孤立的文字，不知道它属于哪个章节，引用只能写 `docs/design.md page 0`，完全没有定位价值。

**修复思路：**

Markdown 有天然的结构：`#` 标题就是语义边界。正确的做法是先按标题切，再在每个 section 内部按字符数切（处理超长章节）。LangChain 提供了 `MarkdownHeaderTextSplitter` 做第一步，切出来的每个 Document 的 metadata 里会自动带上 `h1`, `h2`, `h3` 字段。

```python
# 切完之后每个 chunk 的 metadata 长这样：
{
  "h1": "API 设计",
  "h2": "流量控制",
  "h3": "限流策略",
  "section": "API 设计 > 流量控制 > 限流策略",  # 我们额外拼接的面包屑
  "page": 0,
  "source_type": "doc",
}
```

这样 Reviewer 的引用可以写成 `docs/design.md § API 设计 > 流量控制 > 限流策略`，定位精确。

**PDF 的处理：**

PDF 没有标题标记，结构信息在排版里（字体大小、缩进），普通文本提取器看不到。原来用的 `PyPDFLoader` 会把整页文字拼成一个字符串，段落边界全靠空行猜。

换成 `PDFMinerLoader`：pdfminer.six 做了布局分析，能识别段落分组，不会把两栏排版的文字错误拼接，也能保留列表项的换行结构。实测同一份文档，PyPDF 提取后段落之间经常缺换行，PDFMiner 保留得更完整。

代码在 `app/rag/doc_chunker.py`，两个加载器的选择在模块导入时检测，`pdfminer.six` 未安装时 fallback 到 PyPDF 并打印警告。

---

### [问题发现] 向量搜索找不到精确的变量名和标识符

**现象：** 查询 `"RETRIEVER_TOP_K 这个配置在哪里用到"` 时，向量搜索返回的是语义上"像配置项"的 chunks（各种 `os.getenv` 调用），但真正包含 `RETRIEVER_TOP_K` 这个字符串的 `retriever.py` 排到了第 4 位，差点出现在 top-K 之外。

再试了几个类似的案例：
- `"JWT_SECRET_KEY"` → 向量搜索排第 3，BM25 排第 1
- `"chromadb.errors.InvalidArgumentError"` → 向量搜索完全没找到，BM25 第 1
- `"def _inject_token"` → 向量搜索第 2，BM25 第 1

**根因分析：**

OpenAI 的 `text-embedding-3-small`（也包括更早的 `ada-002`）是在大量自然语言语料上训练的。对于"正常"的技术词汇（`authentication`, `rate limit`, `dependency injection`），embedding 效果很好——语义相近的词在向量空间里也相近。

但对于**项目专有标识符**（`RETRIEVER_TOP_K`, `_inject_token`, `chromadb.errors.InvalidArgumentError`），embedding 模型会把它们映射到通用的"编程/配置"区域，而不是精确定位到这个特定的字符串。这是 embedding 模型的固有局限，不是 bug。

BM25（Best Match 25）是经典的 TF-IDF 变体，基于词频统计。它不理解语义，但对精确的词语命中非常灵敏——`RETRIEVER_TOP_K` 只出现在少数几个文件里，BM25 能直接定位。

**解决方案：混合检索 + RRF 融合**

同时跑两个检索器，用 Reciprocal Rank Fusion（RRF）合并排名：

```
RRF score(doc) = Σ weight_i / (rank_i(doc) + 60)
```

常数 60 是 RRF 的经典默认值（来自原始论文），作用是平滑排名差距，避免第 1 名的优势过于压倒性。

权重默认 `BM25_WEIGHT=0.4, VECTOR_WEIGHT=0.6`，偏向向量搜索，因为大多数自然语言问题语义搜索效果更好，BM25 作为补充兜底精确词语。这两个值暴露在 `.env` 里，可以根据实际 query 分布调整。

实现用 LangChain 的 `EnsembleRetriever`，它内置了 RRF 逻辑，不需要手动实现排名融合。

---

### [设计决策] BM25 的 corpus 放内存，重启后 fallback 到纯向量

**问题：** `BM25Retriever.from_documents(corpus)` 需要在内存里有全部文档才能建索引。但 ChromaDB 是持久化的，进程重启后 Chroma 的数据还在，而内存 corpus 清空了。

**三种方案的取舍：**

方案 A：每次查询时从 Chroma 把所有文档捞出来重建 BM25 索引。
→ 简单，但 10k chunks 的情况下每次查询要捞几十 MB 数据，延迟不可接受。

方案 B：把 corpus 序列化持久化（pickle 到磁盘）。
→ 可行，但增加了状态管理复杂度，pickle 有安全风险，版本兼容性也麻烦。

方案 C：内存有 corpus 则用混合搜索，没有则 fallback 到纯向量搜索。
→ 选这个。fallback 有轻微的召回质量损失（精确标识符查询退化），但系统不会因为重启而崩溃，用户感知是"重启后需要重新索引才能用混合搜索"，可接受。

在 `vectordb.py` 的 `search()` 里记录了这个行为：

```python
if not self._corpus:
    # BM25 needs raw documents in memory.
    # Fall back to pure vector search after process restart.
    return self.db.similarity_search(query, k=k)
```

---

### [设计决策] `source_type` 元数据贯穿整个 pipeline

把 `source_type="code"` 或 `source_type="doc"` 打在每个 chunk 的 metadata 里，有几个好处：

1. **检索层可以过滤。** `filter_search(query, source_type="code")` 只搜代码，BM25 的 corpus 也同步过滤，不会把文档 chunk 混进代码查询的结果里。

2. **展示层可以区分。** Streamlit 和 MCP 返回结果时，header 格式不同：代码 chunk 显示 `file | kind | lines`，文档 chunk 显示 `filename | page | section`。

3. **stats 端点可以报告分布。** `/stats` 现在能返回 `code_chunks: 342, doc_chunks: 87`，方便判断两个索引的相对规模。

---

### [待解决] BM25 在 cross-search 场景下权重可能需要动态调整

当前 `cross_search`（不过滤 source_type）的 BM25 corpus 包含了代码 chunk 和文档 chunk 的混合。代码 chunk 天然词汇密度高（标识符、关键字密集），文档 chunk 是自然语言，TF-IDF 分布不同。

结果是 BM25 对代码 chunk 的命中分数会系统性地偏高，跨源查询里代码结果会排得比文档结果靠前，即使语义上文档结果更相关。

正确的解决方法是分别对代码 corpus 和文档 corpus 建 BM25 索引，然后在 RRF 里用四路融合（代码BM25 + 代码向量 + 文档BM25 + 文档向量），权重独立配置。当前先记录这个局限，暂不实现。

---

## 阶段四：依赖整理、环境搭建与新增问题修复

### [环境] Windows GBK 编码导致启动失败

**现象：** 在 Windows 上运行 `uvicorn app.main:app --reload` 时，立刻抛出：
```
UnicodeDecodeError: 'gbk' codec can't decode byte 0x93 in position 202
```

**根因：** Windows 默认用系统 GBK 编码读取文件。`planner.py` 和 `reviewer.py` 在模块级用 `Path.read_text()` 加载 prompt 模板文件，而这两个 `.txt` 文件是 UTF-8 编码保存的，GBK 无法解码其中的中文字符和 Unicode 符号。

**修复：** 两处 `read_text()` 均加上 `encoding='utf-8'` 参数，现已体现在代码中。这是 Python 跨平台开发的经典陷阱——Linux/macOS 默认 UTF-8 所以不报错，Windows 才暴露。

---

### [环境] LangChain 0.x → 1.x 迁移：导入路径全面失效

**现象：** 安装依赖后运行报 `ModuleNotFoundError: No module named 'langchain.retrievers'`、`No module named 'langchain.chat_models'` 等一系列错误。

**根因：** LangChain 在 0.2.x 到 1.x 的升级中进行了彻底的包拆分：
- `langchain.chat_models.ChatOpenAI` → `langchain_openai.ChatOpenAI`
- `langchain.embeddings.OpenAIEmbeddings` → `langchain_openai.OpenAIEmbeddings`
- `langchain.vectorstores.Chroma` → `langchain_chroma.Chroma`
- `langchain.retrievers.BM25Retriever` → `langchain_community.retrievers.BM25Retriever`
- `langchain.retrievers.EnsembleRetriever` → `langchain_classic.retrievers.EnsembleRetriever`（在 1.x 生态中）

原代码全部使用旧路径，安装了 1.x 版本后全部失效。

**修复策略：** 统一迁移到新生态路径，并在 `pyproject.toml` 锁定版本组合：
```
langchain==1.3.2 + langchain-core>=1.4.0 + langchain-community==0.4.1
+ langchain-openai==1.0.3 + langchain-chroma==1.0.0 + langchain-classic>=0.1.0
+ langgraph>=1.2.2,<1.3.0 + chromadb==1.0.21
```

**关键发现：** `EnsembleRetriever` 在 langchain 1.x 下既不在 `langchain_community.retrievers` 也不在 `langchain.retrievers`，而是在 `langchain_classic.retrievers`。这是 notes.md 中调试链的终点，也是最终解决方案。`langchain-classic` 必须显式加入依赖，否则环境可复现性破坏。

---

### [BUG] `requirements.txt` 与 `pyproject.toml` 严重不一致

**发现：** `requirements.txt` 使用 `>=` 宽松版本约束（`langchain>=0.2.0`），而 `pyproject.toml` 已锁定精确版本（`langchain==1.3.2`）。更严重的是：
- `requirements.txt` 仍然包含 `faiss-cpu>=1.8.0`，但项目主体已切换到 ChromaDB，FAISS 从未在 `pyproject.toml` 中出现
- `requirements.txt` 缺少 `langchain-classic`，这是运行时实际需要的包

这意味着 `pip install -r requirements.txt` 和 `pip install -e .` 会安装出完全不同的环境，给他人复现项目制造障碍。

**修复：** `requirements.txt` 重写为与 `pyproject.toml` 完全镜像，版本一致，补充 `langchain-classic`，保留可选的 `pdfminer.six`，移除游离的 `faiss-cpu`。

---

### [BUG] `/stats` 端点重启后 `code_chunks`/`doc_chunks` 始终为 0

**现象：** 重启服务后访问 `/api/v1/stats`，`total_chunks` 正常（从 Chroma 读取），但 `code_chunks` 和 `doc_chunks` 均为 0。

**根因：** 原代码从 `store._corpus`（内存列表）统计分类数量。`_corpus` 仅在进程生命周期内存在，进程重启后清空。而 `total_chunks` 来自 `store.count()`（查询 Chroma），Chroma 是持久化的，所以两个来源在重启后产生矛盾的数据。

**修复：** 改为通过 Chroma 的 `where` 过滤器直接从持久化存储读取分类计数：
```python
code_count = store.db._collection.count(where={"source_type": "code"})
doc_count  = store.db._collection.count(where={"source_type": "doc"})
```
同时在返回值中增加 `hybrid_search_active` 字段，明确告知调用方当前是否有 BM25 corpus（即混合检索是否生效），避免用户误以为重启后搜索质量与索引时一致。

---

### [次要] `repo_loader.py` 异常静默丢失，排查困难

**现象：** 对某个仓库执行索引后，`indexed_chunks` 数量比预期少，但没有任何错误提示。

**根因：** 文件读取的两处异常捕获（`OSError`、`UnicodeDecodeError`）直接 `continue`，不记录日志。权限问题、编码问题导致的文件跳过完全不可见。

**修复：** 引入 `logging.getLogger(__name__)`，跳过文件时在 `WARNING` 级别记录原因和路径。生产环境配置日志后即可看到哪些文件被跳过及原因。

---

## 待解决的问题（延续）

**[TODO] `VectorStore` 单例在多线程下的竞态条件**（见阶段二，未解决）

**[TODO] IVF 索引的自动降级**（见阶段二，未解决）

**[TODO] cross_search 缺少 re-ranking**（见阶段二，未解决）

**[TODO] 文档版本元数据持久化**（见阶段二，未解决）

**[TODO] `index/local` 和 `index/github` 硬编码 `reset=True`，无法增量追加多个代码仓库**

当前设计是每次索引代码仓库都清空整个 store（`reset=True`），这意味着同时索引两个仓库是不可能的——第二次会清掉第一次的数据。文档 chunk（`index/document`）不受影响，因为它不 reset。这是一个已知局限，当前 demo 场景（单仓库 + 若干文档）可以接受，但需要在 README 里明确说明。

**[TODO] `RepoLoader` 只支持 `.py` 文件，docstring 也只说了 Python**

notes.md 中记录了\"优化点：除了 Python 文件，还可以接入 C/C++/Golang 等语言\"。tree-sitter 对这些语言都有 grammar，扩展的架构是清晰的（`CodeParser` 按 `file_path` 后缀选 grammar），但当前未实现。简历 demo 场景下 Python-only 是合理的，但这是一个明显的扩展点。

---

## 阶段五：运行时 Bug 修复（跑通验证）

> 本阶段对所有模块执行 smoke test 和 API 集成测试，发现并修复了 2 个影响正常运行的 Bug。

### [BUG-1] 前端 Sidebar 索引数量始终显示 0

**影响：** 前端 Sidebar 的「Indexed chunks」数量始终显示 0，即使已成功完成索引，导致用户误以为索引失败。

**根因：** `frontend/app.py` 第 25 行读取 `/stats` 响应时使用了键名 `indexed_chunks`：
```python
# 原代码（BUG）
stats.get('indexed_chunks', 0)
```
但 `/api/v1/stats` 端点实际返回的键名是 `total_chunks`（已在阶段四修复时统一）。两端键名不一致，`get()` 取不到值，默认返回 0。

注意：`/index/local` 和 `/index/github` 的响应体里确实使用 `indexed_chunks` 键，这两处是正确的（行 45、61）。只有 sidebar 的 stats 展示这一处有问题。

**修复：**
```python
# 修复后：同时展示总数和分类数，键名与 /stats 对齐
st.caption(f"Indexed: **{stats.get('total_chunks', 0)}** chunks "
           f"(code: {stats.get('code_chunks',0)}, doc: {stats.get('doc_chunks',0)})")
```
顺带将展示内容升级为分类统计（code/doc），比单一总数更有用。

---

### [BUG-2] `/health` 接口返回错误的服务名

**影响：** 轻微，但会在接入监控系统或健康检查时产生混淆。

**根因：** `/health` 端点的响应硬编码了旧名称：
```python
# 原代码（BUG）
return {"status": "ok", "service": "DevAssistant"}
```
项目在某个版本时从 DevAssistant 改名为 CodeSentinel，但这行漏改了。`app/main.py` 的 `FastAPI(title=...)` 和 `root()` 端点均已是 CodeSentinel。

**修复：** 改为 `"service": "CodeSentinel"`，与全局一致。

---

### 本阶段测试覆盖

本轮修复后，以下测试全部通过：

| 测试项 | 结果 |
|---|---|
| 全模块 import（15 个模块） | ✅ |
| `CodeParser` 单元测试（AST + fallback）| ✅ |
| `CodeChunker` 单元测试 | ✅ |
| `RepoLoader` 单元测试 | ✅ |
| `DocChunker` Markdown 单元测试 | ✅ |
| `VectorStore` 空索引守卫 | ✅ |
| `build_graph()` 图结构验证 | ✅ |
| `GET /` 版本一致性 | ✅ |
| `GET /api/v1/health` 服务名 | ✅ |
| `GET /api/v1/stats` 字段完整性 + `hybrid_search_active` | ✅ |
| `POST /api/v1/ask` 空索引 → 400 而非 500 | ✅ |

---

## 阶段六：文档上传功能（v0.3.0）

### 功能概述

本阶段在后端新增 `POST /api/v1/index/upload` 接口，将 `DocChunker` 扩展支持 `.txt`、`.docx`、`.xlsx`，并对前端进行全面重构：新增文档上传区，升级整体 UI 风格，增加检索范围选择器。

---

### [后端] DocChunker 扩展：支持 TXT / DOCX / XLSX

**变更文件：** `app/rag/doc_chunker.py`

新增三种格式的分块策略，并导出 `SUPPORTED_EXTENSIONS` 常量供路由层校验使用：

```python
SUPPORTED_EXTENSIONS = {".pdf", ".md", ".txt", ".docx", ".xlsx", ".xls"}
```

**TXT：**
直接 `open(encoding='utf-8', errors='replace')` 读取全文，交给 `RecursiveCharacterTextSplitter` 按字符数分块。无结构可利用，处理最简单。

**DOCX（Word）：**
首选 `langchain_community.document_loaders.Docx2txtLoader`（依赖 `docx2txt` 包），提取段落文本后按字符分块。若 `docx2txt` 不可用，降级到 `python-docx` 直接拼接段落文本。metadata 中 `section=""`, `page=0`。

**XLSX / XLS（Excel）：**
使用 `openpyxl.load_workbook(read_only=True, data_only=True)` 遍历所有 sheet，将每个 sheet 的数据转为 TSV 格式文本块：

```
[Sheet: Q1 Sales]
Product	Region	Revenue	Units
Widget A	North	12000	500
```

每个 sheet 是独立的 `Document`，metadata 中 `section=<sheet名>`, `page=<sheet索引>`。Sheet 过大时进一步字符分块。空 sheet 自动跳过。

**设计决策：** Excel 不做语义理解，转为 TSV 文本后由向量模型处理。这保留了表格结构（列名对齐），同时不依赖任何额外的表格解析库。实测对"第一季度Widget A的营收是多少"类查询有效。

---

### [后端] 新增 `POST /api/v1/index/upload` 接口

**变更文件：** `app/api/routes.py`

使用 FastAPI 的 `UploadFile` + `File(...)` 接收 multipart/form-data 上传，流程：

```
UploadFile → 读取全部字节到内存 → 检查大小 → 清理文件名 → 写入临时文件
  → DocChunker.chunk() → VectorStore.add_documents() → 删除临时文件
```

**关键设计点：**

1. **文件名清理**：`re.sub(r'[^\w.\-]', '_', original_name)`，防止路径遍历攻击（`../etc/passwd` → `.._.._etc_passwd`）

2. **大小限制**：读取后检查 `len(data) > config.MAX_UPLOAD_BYTES`（默认 50 MB），超限返回 HTTP 413

3. **临时文件策略**：文件落盘到 `config.UPLOAD_DIR`（默认 `./data/uploads`），索引完成或失败后在 `finally` 块中立即删除。不保留上传文件，存储只在 ChromaDB

4. **扩展名前置校验**：从 `SUPPORTED_EXTENSIONS` 动态读取，新增格式只需改 `doc_chunker.py` 一处

5. **新增依赖** `python-multipart`：FastAPI 的 `UploadFile` 依赖此包，不安装会在路由注册时抛 `RuntimeError`（已加入 `pyproject.toml`）

---

### [后端] `app/services/indexing.py` 扩展

`index_document()` 的文件类型白名单从硬编码 `(".pdf", ".md")` 改为动态引用 `SUPPORTED_EXTENSIONS`，保持单一来源原则（SSOT）。

---

### [后端] `app/config.py` 新增配置项

| 参数 | 默认值 | 说明 |
|---|---|---|
| `UPLOAD_DIR` | `./data/uploads` | 上传文件的临时落盘目录 |
| `MAX_UPLOAD_BYTES` | `52428800`（50 MB） | 单文件最大上传字节数 |

---

### [前端] 全面重构（v0.3.0）

**变更文件：** `frontend/app.py`

#### 新增：文档上传区（Document Upload）

位于侧边栏代码索引区下方，独立的 `section-label` 区块。

**多文件上传 + 批量索引流程：**
1. `st.file_uploader(accept_multiple_files=True, type=[...])` 接收多文件
2. 文件选择后立即展示文件徽章（含格式色块、文件名、大小）
3. 点击「Upload & Index All」按钮后：
   - 逐文件调用 `POST /api/v1/index/upload`（multipart）
   - 实时进度条 `st.progress()` + 动态状态文字显示当前处理的文件
   - 全部完成后汇总：成功 N 个文件 / M 个 chunks / K 个失败
4. 上传完成后 `st.cache_data.clear()` 刷新 stats 面板

**文件徽章设计：**
每种格式有独立的色块标识（PDF=红色、MD=绿色、DOCX=蓝色、XLSX=亮绿、TXT=灰色），防止用户在多文件上传时混淆格式。

#### 新增：检索范围选择器（Scope）

主区域新增 `st.selectbox`，选项：
- **All sources**（默认）：`source_type=None`，跨代码和文档混合检索
- **Code only**：`source_type="code"`，只检索代码 chunks
- **Docs only**：`source_type="doc"`，只检索文档 chunks

选择结果通过 `POST /api/v1/ask` 的 `source_type` 字段传入 Agent。

#### 新增：Stats 实时面板

侧边栏顶部的 stats 从纯文字改为四枚胶囊形标签：`total` / `code` / `docs` / `hybrid`，使用 `@st.cache_data(ttl=5)` 缓存，上传/索引后主动 `st.cache_data.clear()` 触发刷新。

#### 主题与样式

- **字体**：JetBrains Mono（代码/标签）+ DM Sans（正文）
- **主色**：`#58a6ff`（GitHub 蓝），背景 `#0d1117`（GitHub 暗色）
- **查询框**：改为 `st.text_area`（多行），支持粘贴长问题
- **答案区**：带左侧蓝色边框的 card 样式，视觉上区分于页面背景

---

### 本阶段测试覆盖

| 测试项 | 结果 |
|---|---|
| `DocChunker` TXT 格式 | ✅ |
| `DocChunker` DOCX 格式 | ✅ |
| `DocChunker` XLSX 格式（多 sheet）| ✅ |
| `DocChunker` CSV 通用 fallback | ✅ |
| `POST /index/upload` 拒绝不支持扩展名（→ 400）| ✅ |
| `POST /index/upload` 拒绝超大文件（→ 413）| ✅ |
| `POST /index/upload` TXT 上传路由（端到端）| ✅ |
| 前端 Python 语法检查 | ✅ |
| 全模块 import 检查 | ✅ |

---

### 新增依赖总结

| 包 | 版本 | 用途 |
|---|---|---|
| `python-multipart` | `>=0.0.9` | FastAPI `UploadFile` multipart 解析（必须） |
| `docx2txt` | `>=0.8` | Word `.docx` 文本提取（`Docx2txtLoader` 依赖） |
| `openpyxl` | `>=3.1.0` | Excel 读取（项目原有，现在显式声明） |
| `python-docx` | `>=1.1.0` | DOCX fallback loader |

升级命令：
```bash
pip install -e . --extra-index-url https://pypi.org/simple
```

---

## 阶段七：Advanced RAG 全链路升级（v0.4.0）

> 本阶段将 RAG 管道从"基础检索-生成"升级为五阶段高级 RAG。每个阶段解决基础 RAG 的一个具体缺陷，并有独立的模块实现。

---

### 背景：基础 RAG 的四个缺陷

在升级前，管道是：`用户问题 → 向量搜索(top-5) → 直接送 LLM → 输出`

这个流程有四个已知的系统性缺陷：

| 缺陷 | 表现 | 根因 |
|---|---|---|
| 问题语义不足 | "它为什么慢" 搜不到性能代码 | 短问题的 embedding 向量和长代码 chunk 的向量距离天然偏大 |
| 单角度检索 | "auth 在哪" 搜不到 `verify_token` | 自然语言和精确符号名各自偏向向量/关键词检索 |
| 初召回质量低 | top-5 里第一名不是最好答案 | 向量相似度 ≠ 对具体问题的精确相关度 |
| 一次性生成 | 上下文不足也不会补充 | 没有重试机制 |

---

### Stage 1 — 检索前优化（Pre-Retrieval Optimization）

**变更文件：** `app/agent/planner.py`, `app/prompts/planner.txt`

#### 1a. 查询改写（Query Rewriting）

Planner 现在首先对用户问题做"深加工"：

- **代词消解**："它" → 根据上下文展开为具体实体
- **缩写扩展**："auth" → "authentication"，"cfg" → "configuration"
- **隐式意图显化**："为什么慢" → "request handler 热路径中的性能瓶颈"

改写后的 `rewritten_query` 会作为首条子查询加入检索池。

#### 1b. 多角度子查询分解（Multi-Angle Decomposition）

将改写后的问题分解成 2–4 条子查询，每条覆盖不同的检索角度：

```
原始问题: "JWT 认证是怎么实现的"
→ 角度1 (语义): "authentication logic using JSON Web Token"
→ 角度2 (关键词): "verify_token jwt decode secret_key Bearer"
→ 角度3 (上下文): "middleware decorator that calls verify_token"
→ 角度4 (对比): "authentication failure error handling"
```

这解决了"单角度检索"缺陷：向量检索偏语义、BM25 偏关键词，多角度可以覆盖两者的盲区。

#### 1c. HyDE — 假设文档嵌入（Hypothetical Document Embedding）

**核心思想（来自 Gao et al. 2022）：** 与其直接嵌入一个短问题，不如先让 LLM 生成一段"假如答案存在，它应该长什么样"的文字，再用这段文字去做检索。

```
问题：  "JWT 认证在哪里实现" (20 tokens)
↓
HyDE 段落："The JWT authentication is implemented in the auth middleware.
             The verify_token function decodes the Bearer token from the
             Authorization header using the SECRET_KEY from config."  (约60 tokens)
↓
用 HyDE 段落的 embedding 去检索 → 向量空间上与实际代码 chunk 更近
```

HyDE 段落和所有子查询一起进入检索池，作为额外的一条"假设"查询。

---

### Stage 2 — 多路召回（Multi-Route Retrieval）

**变更文件：** `app/agent/retriever.py`

#### 扩大检索池（Fetch-K vs Top-K 分离）

基础 RAG 直接 fetch top-5，但这给重排序模块留不下选择空间。

现在引入了两个独立参数：
- `RETRIEVER_FETCH_K = 20`：从向量库召回 20 个候选（给重排序用）
- `RERANKER_TOP_K = 5`：重排序后保留 5 个（送入 LLM）

**类比：** 面试先海选 20 人（recall 优先），再精挑 5 人（precision 优先）。

#### 多跳检索（Multi-Hop）

每条子查询独立检索，结果按"首次出现"顺序合并去重。第一轮检索召回的 chunk 保持高优先级，后续子查询补充新增的 chunk，保留来自高排名子查询的信号。

---

### Stage 3 — 重排序（Re-ranking）

**新增文件：** `app/agent/reranker.py`

#### 为什么需要重排序

向量检索（cosine 相似度）衡量的是"两段文字的整体语义有多像"，而重排序模型衡量的是"这段文字是否直接回答了这个具体问题"。两者目标不同，准确度差异显著。

#### 双路径设计

| 路径 | 模型 | 延迟 | 依赖 | 精度 |
|---|---|---|---|---|
| Path A | `cross-encoder/ms-marco-MiniLM-L-6-v2` | ~20–50ms/20条(CPU) | `sentence-transformers` | 最高 |
| Path B | GPT-4o-mini LLM Reranker | ~1–3s | 仅 OpenAI Key | 较高 |

模块在 import 时自动检测 `sentence_transformers` 是否可用：

```python
try:
    from sentence_transformers import CrossEncoder
    _CROSS_ENCODER_AVAILABLE = True
except ImportError:
    _CROSS_ENCODER_AVAILABLE = False
```

**Path B（LLM Reranker）的 prompt 设计：**

将所有候选 chunk 一次性打包进单个 prompt，要求 LLM 输出 JSON 分数数组（而非逐条评分），控制 token 消耗：

```
Query: "JWT 认证实现"
Passages:
[1] def verify_token(tok): ...
[2] class UserModel(Base): ...
[n] ...

→ [9, 2, 7, 1, ...]
```

**安装 sentence-transformers 后自动切换到 Path A：**
```bash
pip install sentence-transformers
```
无需修改代码，模块启动时会自动加载。

---

### Stage 4 — 上下文融合（Context Fusion）

**新增文件：** `app/agent/context_builder.py`

基础 RAG 将 chunk 直接拼接成字符串，没有任何结构信息。LLM 可能误认为相邻两个 chunk 是连续文本。

新的 `build_context()` 为每个 chunk 生成语义丰富的 header，并用视觉分隔符隔开：

```
[1] 💻 app/auth.py | function: verify_token | lines 42–65
def verify_token(token: str) -> dict:
    ...

═══════════════════════════════════════

[2] 📄 architecture.pdf | page 3 | §"Authentication Flow"
JWT tokens are validated against the secret key stored in...
```

**设计细节：**
- 编号顺序 = 重排序后的相关度顺序（`[1]` 是最相关的）
- 每个 chunk 限制 `MAX_CHARS_PER_CHUNK=1200` 字符，防止 context window 溢出
- 超长 chunk 追加 `… [truncated]` 标记，让 LLM 知道内容被截断

**Reviewer prompt 同步升级：**

要求 LLM 对多个 chunk 做"合成"而非逐条列举，并在 prompt 中明确要求输出置信度和后续查询。

---

### Stage 5 — 生成后反馈循环（Post-Generation Feedback Loop）

**变更文件：** `app/agent/graph.py`, `app/agent/reviewer.py`

#### 思路

当第一轮检索的上下文不足以给出完整答案时（Reviewer 评估为 medium/low 置信度），与其直接输出一个不确定的答案，不如让系统自动发起第二轮检索。

#### 实现

Reviewer 现在输出三个字段（而非原来只有 answer）：
1. `answer`：当前轮次的答案
2. `confidence`："high" | "medium" | "low"
3. `followup_queries`：如果有知识缺口，输出 1–2 条追加查询

图的路由逻辑：

```
review_node 执行完毕
    ↓
_should_loop():
    if confidence != "high" 
       AND followup_queries 非空
       AND iteration < MAX_RAG_ITERATIONS (默认 2):
        → 回到 retrieve_node（带上 followup_queries 追加检索）
    else:
        → END
```

**防无限循环：** `MAX_RAG_ITERATIONS=2` 保证最多执行 2 轮检索，即使每轮都 medium confidence。

**第二轮检索的增量性：** follow-up queries 会 append 到原有 sub_queries 列表（不替换），第二轮的 RetrieverAgent 在已有结果基础上追加新发现的 chunk，再次重排序。

#### 图拓扑对比

基础 RAG（线性）：
```
plan → retrieve → review → END
```

高级 RAG（可循环）：
```
plan → retrieve → rerank → review
                     ↑         |  (medium/low + followups + iter<max)
                     └─────────┘
                               ↓
                              END
```

---

### 数据流对比

**基础 RAG（改造前）：**
```
用户问题
  → PlannerAgent.plan()         # 1次 LLM 调用，输出 sub_queries
  → RetrieverAgent.retrieve_multi(sub_queries, k=5)   # 每个子查询 top-5
  → 直接拼接成 context 字符串
  → ReviewAgent.review(context) # 1次 LLM 调用，输出 answer
总计: 2次 LLM，1次向量检索
```

**高级 RAG（改造后，一轮无循环）：**
```
用户问题
  → PlannerAgent.plan()         # 1次 LLM（输出 rewritten + sub_queries + HyDE）
  → RetrieverAgent.retrieve_multi(4-6条查询, k=20) # 每个子查询 top-20，合并去重
  → rerank(top-20候选, k=5)     # LLM 批量评分（或 CrossEncoder 0ms）
  → build_context(top-5)        # 结构化 context 字符串
  → ReviewAgent.review(context) # 1次 LLM（输出 answer + confidence + followups）
总计: 2-3次 LLM（含 reranker），4x 更大检索池，结构化 context
```

**高级 RAG（两轮，触发 feedback loop）：**
```
+ 第二轮 retrieve（follow-up queries）
+ 第二轮 rerank + build_context
+ 第二轮 ReviewAgent（最终 answer）
总计: 3-4次 LLM
```

---

### 新增/修改文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `app/agent/planner.py` | 重写 | 加入 HyDE、改写、多角度分解 |
| `app/agent/retriever.py` | 升级 | FETCH_K=20 扩大池；多跳合并 |
| `app/agent/reranker.py` | **新增** | CrossEncoder / LLM 双路径重排序 |
| `app/agent/context_builder.py` | **新增** | 结构化 context 格式化 |
| `app/agent/reviewer.py` | 升级 | 解析 confidence + follow-up queries |
| `app/agent/graph.py` | 重写 | 4节点图 + 条件反馈循环 |
| `app/prompts/planner.txt` | 重写 | 三任务 prompt（改写/分解/HyDE）|
| `app/prompts/reviewer.txt` | 升级 | 要求合成 + confidence + follow-ups |
| `app/config.py` | 升级 | 4个新参数 |
| `app/api/routes.py` | 升级 | `/ask` 返回 confidence/iterations/HyDE |
| `frontend/app.py` | 升级 | 显示 confidence 徽章、iterations、HyDE |

---

### 新增配置项

| 参数 | 默认值 | 说明 |
|---|---|---|
| `RETRIEVER_FETCH_K` | `20` | 给重排序的候选池大小 |
| `RERANKER_TOP_K` | `5` | 重排序后保留数量（原 RETRIEVER_TOP_K 的语义） |
| `MAX_CHARS_PER_CHUNK` | `1200` | context fusion 中每 chunk 的字符上限 |
| `MAX_RAG_ITERATIONS` | `2` | 反馈循环最大迭代次数 |

如需关闭反馈循环（退回单轮行为）：
```bash
MAX_RAG_ITERATIONS=1
```

如需安装 CrossEncoder 开启 Path A 重排序：
```bash
pip install sentence-transformers
```

---

### 本阶段测试覆盖

| 测试项 | 结果 |
|---|---|
| 全模块 import（17 个模块）| ✅ |
| `config` 新字段默认值 | ✅ |
| `build_graph()` 4节点结构 | ✅ |
| `build_context()` header 格式（code + doc）| ✅ |
| `_parse_confidence()` medium/high 解析 | ✅ |
| `_parse_followup_queries()` 2条/空列表 | ✅ |
| `GET /health` `GET /stats` | ✅ |

---

## 阶段八：主线功能精简（移除非核心模块，2026-06）

**动机：** 项目积累了几条和"代码/文档 RAG 问答"这条主线无关、或者从未真正接入主流程的旁支代码（GitHub 一键克隆索引、一个独立的文档生命周期管理脚本、个人调试笔记）。这些内容增加了阅读和维护成本，也让简历/面试时容易被问到"这部分到底用了没有"这类不该出现的问题。本阶段目标：**删掉主线之外的东西，但不动 Plan→Retrieve→Rerank→Review 这条核心 Agent 链路、混合检索、AST 分块、文档分块、FastAPI/Streamlit 任何一个已经在用的功能。**

### [移除] GitHub 一键克隆索引 (`/index/github`)

**移除内容：**
- `app/services/github_service.py` 整个文件删除（`GitPython` clone + token 注入逻辑）
- `app/api/routes.py`：删除 `IndexGitHubRequest` 模型、`/index/github` 端点、`GitHubService` 的导入与实例化；同时清掉了因此变成死代码的 `tempfile`/`shutil` 两个未使用 import
- `app/config.py` / `.env.example`：删除 `GITHUB_TOKEN` 配置项
- `requirements.txt` / `pyproject.toml`：删除 `gitpython` 依赖
- `frontend/app.py`：侧边栏 "Local Path / GitHub URL" 单选去掉，直接展示本地路径输入框（少一层分支，体验更直接）
- `README.md`、`docs/USER_MANUAL.md`、`docs/TECH_SPEC.md`：同步删除 GitHub 索引相关的功能介绍、Quick Start 示例、接口表格行
- `tests/eval_metrics.py` 的延迟测试默认问题列表、`tests/eval_labels.json` 的标注集中各有一条指向 `github_service.py` 的测试样本，替换为指向仍然存在的 `reranker.py`（保证评测脚本继续可跑，不会因为引用了已删除文件而报错或产生误导性的"标注答案"）

**为什么可以删：** 索引一个仓库本质只需要"一个本地目录"，GitHub 远程仓库只是这个目录的一种获取方式，跟 RAG/Agent 这条核心逻辑完全无关，是纯粹的"便利性外壳"。用户自己 `git clone` 到本地再用 `/index/local` 索引，效果完全一样，少了一条 `git clone --depth 1` 子进程失败、私有仓库 token 泄露风险等需要额外维护的边界情况。

**保留：** `/index/local`、`/index/document`、`/index/upload`（含 PDF/MD/TXT/DOCX/XLSX 跨源文档支持）、混合检索、reranker、反馈循环全部不受影响。

### [移除] `document_lifecycle_manager.py`

这是一个独立的、从未被 `app/main.py` 引用、`README.md` 里也明确写了"Not wired into CodeSentinel FastAPI"的参考脚本（用 FAISS + HuggingFace embedding 实现的另一套文档生命周期管理 demo）。真正在用的文档处理逻辑是 `app/rag/doc_chunker.py` + `app/services/indexing.py`，两者功能有重叠但接口、依赖（FAISS vs Chroma，HuggingFace vs OpenAI embedding）完全不同，留着容易让人误以为它是文档处理的"正式实现"而误读架构。直接删除，未对任何现有功能产生影响（`grep` 全项目确认无任何文件 import 它）。

### [移除] `notes.md`、`scripts/`

- `notes.md`：个人调试过程记录（LangChain 0.2→1.x 迁移踩坑、Windows 环境变量编码问题等），属于开发者笔记而非项目交付物，且其中部分内容已经在 DEVLOG 的"阶段四"中以结构化形式记录过，保留原始笔记是信息重复。
- `scripts/`：空目录，仅在 `docs/TECH_SPEC.md` 里被提及为"未来 MCP server 入口预留位置"，没有任何实际文件。删除空目录，并把 `TECH_SPEC.md` 里的说法改为"可在 `app/` 下新增入口文件"，避免文档指向一个不存在的目录。

### 改动后的项目结构

```
codesentinel/
├── app/
│   ├── agent/        # graph / planner / retriever / reranker / reviewer / context_builder（未改动）
│   ├── rag/          # parser / chunker / doc_chunker / embedding / vectordb（未改动）
│   ├── services/
│   │   ├── repo_loader.py     # 保留：本地仓库加载
│   │   └── indexing.py        # 保留：索引编排
│   ├── api/routes.py           # 改：移除 /index/github
│   ├── prompts/                # 未改动
│   ├── config.py                # 改：移除 GITHUB_TOKEN
│   └── main.py                  # 未改动
├── frontend/app.py               # 改：移除 GitHub URL 入口
├── tests/                        # 改：评测样本替换
├── docs/                         # 改：同步删除 GitHub 相关说明
├── .env.example / requirements.txt / pyproject.toml   # 改：移除 GITHUB_TOKEN / gitpython
└── （已删除）github_service.py, document_lifecycle_manager.py, notes.md, scripts/
```

### 本阶段验证

| 验证项 | 方法 | 结果 |
|---|---|---|
| 全项目 `grep -i github` 无残留引用（除本 DEVLOG 历史记录） | 静态检索 | ✅ |
| `routes.py` / `config.py` / `frontend/app.py` / `eval_metrics.py` 语法检查 | `py_compile` | ✅ |
| `eval_labels.json` 仍为合法 JSON | `json.load` | ✅ |
| 核心 Agent 链路（`graph.py`/`planner.py`/`retriever.py`/`reranker.py`/`reviewer.py`）未改动一行 | diff 对比 | ✅ |
| 混合检索（BM25+向量）、文档上传跨源问答功能未受影响 | 代码审查 | ✅ |

**已处理的后续项：** `README.md` 顶部架构图原先写的是 "Planner → Retrieve → Reviewer" 三节点，与阶段七实际实现的 "Plan→Retrieve→Rerank→Review 四节点 + 反馈循环" 不一致。已同步更新：Features 表补充了 Rerank 节点、反馈循环、HyDE/Query Rewriting、Hybrid Retrieval、Cross-Source Search 几项此前完全没写进文档的能力；架构图改为四节点 + 条件回边；Project Structure 补全了 `reranker.py`、`context_builder.py`、`doc_chunker.py`、`indexing.py`、`tests/`；Roadmap 里删掉已经做完的"评测 harness"一项，换成更真实的下一步（持久化元数据存储）。

---

## 阶段九：持久化元数据层 + 意图路由 + 公开 Benchmark 评测（v0.5.0，2026-06）

**动机：** 阶段八清理了"主线之外"的代码，但顺带又确认了两个真实存在的功能缺口：① 文档/仓库元数据只存在内存字典里，进程重启全丢；② Planner 早就在做 intent 分类，但分类结果只在 Streamlit 调试面板里看个热闹，没接进任何实际检索逻辑。这两点本来就写在 `TECH_SPEC.md` 的已知局限里。本阶段把它们做成真东西，而不是为了"加数据库显得牛逼"而加数据库——具体取舍见对话记录：明确放弃了 MongoDB（这批数据全是结构化、有外键关系的，关系型数据库才是诚实的选择），只用了一个 SQLAlchemy + SQLite（一行配置可切 MySQL/Postgres）。

同时补充了两个基于**已发表公开 benchmark**的评测脚本（SWE-QA + RAGAS、BEIR），区别于 `tests/eval_metrics.py` 用的手写标注集——公开 benchmark 的问题和参考答案不是自己写的，跑分对面试官更有说服力。

### [新增] 持久化元数据层

**新文件：**
- `app/db.py` — SQLAlchemy engine/session，`DATABASE_URL` 驱动，默认 `sqlite:///./data/codesentinel.db`
- `app/db_models.py` — 两张表：
  - `IndexedSource`：每次索引一个版本，含 `content_hash`（去重用）、`version`、`chunk_count`、`indexed_at`
  - `QueryLog`：每次 `/ask` 一条记录，含 rewritten_query、intent、confidence、iterations、retrieved_candidates、latency_ms
- `app/services/metadata.py` — `hash_file()`（文档内容 SHA-256）、`hash_repo_fingerprint()`（仓库级轻量指纹：(相对路径,大小,mtime) 排序后 hash，不是全文件内容 hash——大仓库没必要每次重新读全部字节）、`find_existing_doc_by_hash()`、`record_indexed_source()`（自动算 version+1）、`log_query()`

**接入点：**
- `app/services/indexing.py`：`index_document()` 先查 hash，命中则直接返回已有 `doc_id`/`chunk_count`，不重新 chunk、不重新调 OpenAI embedding（真实省钱省时间，不是摆设）；`index_code_repository()` 索引完后记一条版本
- `app/api/routes.py`：`/ask` 计时后写 `QueryLog`，失败不影响主流程（`log_query()` 内部 try/except 吞掉异常，只 log，不向上抛）；新增 `GET /sources`（版本历史）、`GET /queries/recent`（最近查询）两个只读端点把这些数据实际暴露出来
- `app/main.py`：`@app.on_event("startup")` 调 `init_db()`，建表是 `CREATE TABLE IF NOT EXISTS` 语义，重复调用安全

**验证：** 实际起了 TestClient，触发 startup 建表，用 `sqlite3` 直接查表结构确认两张表字段；直接调 `app/services/metadata.py` 的函数跑了一遍"首次索引→内容变更产生新版本→hash 命中去重→hash 未命中返回 None→写查询日志"全流程，结果符合预期；再通过 `/api/v1/sources`、`/api/v1/queries/recent` 验证写入的数据能正确读出。

### [新增] 意图路由（`app/agent/intent_routing.py`）

Planner 分类出的 7 种 intent（`find_definition`/`find_config`/`trace_logic`/`compare_implementations`/`summarize_module`/`explain_usage`/`identify_bug`）现在真正影响检索：

- **窄范围精确查找**（`find_definition`/`find_config`）→ BM25 权重调高（0.65~0.70），fetch pool 不放大——这类问题通常是找一个具体的标识符/配置项，关键词匹配比语义匹配更可靠，候选池放大只会引入噪声
- **宽范围跨文件推理**（`trace_logic`/`compare_implementations`/`summarize_module`）→ 向量权重调高（0.65~0.70），fetch pool 放大到 1.5×——这类问题需要语义理解，且往往要从多个文件/函数里收集上下文，给 reranker 更大的候选池才有意义
- 其余 intent 用接近默认配置的折中值；未识别的 intent 或 `INTENT_ROUTING_ENABLED=false` 时完全退化为原来的全局固定权重，行为可关闭、可回退

**接入方式：** `app/rag/vectordb.py` 的 `search()`/`filter_search()` 新增可选的 `bm25_weight`/`vector_weight` 覆盖参数（默认仍读全局 config，不传参数时行为和之前完全一样）；`app/agent/retriever.py` 的 `retrieve()`/`retrieve_multi()` 新增 `intent` 参数，查表得到权重和 fetch_k 倍数后传给 vectordb；`app/agent/graph.py` 的 `retrieve_node` 把 `state["plan"]["intent"]` 真正传下去（这一行之前完全没有，intent 算出来之后就被忽略了）。

**验证：** 实际打印了全部 7 种 intent + 未知 intent + `None` 对应的 `(bm25_weight, vector_weight, fetch_k)`，数值符合设计表格；`build_graph()` 重新构建无报错。

### [新增] SWE-QA + RAGAS 端到端答案质量评测（`tests/swe_qa_ragas_eval.py`）

[SWE-QA](https://arxiv.org/abs/2509.14635)（ACL 2026 Findings）是一个仓库级代码问答 benchmark：人工写的问题 + 人工参考答案，绑定到具体项目的某个 pinned commit。挑了其中体量最小、最知名的两个项目（Flask、Requests，各 48 题）打包进 `tests/data/swe_qa/`（Apache-2.0 协议，附带原始 LICENSE 与署名说明，详见该目录下 `README.md`）。

脚本流程：按 benchmark 指定的 pinned commit clone 目标仓库 → 走真实的 `/index/local` 索引 → 把 48 个问题逐一丢给真实的 `/ask` → 用 [RAGAS](https://docs.ragas.io) 打四个分：Faithfulness（抓幻觉）、Answer Relevancy（答得对不对题）、Context Precision（检索噪声占比）、Context Recall（参考答案需要的信息有多少被检索到）。

为了让 RAGAS 拿到逐条 chunk 文本（`context_precision`/`context_recall` 需要，不能只给一个拼接好的字符串），给 `app/agent/graph.py` 的 `rerank_node` 加了 `retrieved_chunks_text`（重排后每个 chunk 的原始 `page_content` 列表），`/ask` 响应里也带上这个字段（不在 Streamlit UI 里展示，纯粹给这类外部评测脚本用）。

**验证（沙箱网络范围内能做到的）：**
- 实际跑了 `load_swe_qa()`，确认能正确解析 48 条 `{"question","answer"}` 记录
- 实际跑了 `clone_pinned_repo("flask", ...)`，确认能 clone 并 checkout 到 benchmark 指定的精确 commit（`85c5d93`），`git rev-parse --short HEAD` 验证一致
- 实际构造了 `ragas.SingleTurnSample`/`EvaluationDataset`，确认字段名（`user_input`/`response`/`retrieved_contexts`/`reference`）和脚本里的用法完全匹配
- **没有跑通的部分：** 完整调用 `evaluate()` 需要真实 `OPENAI_API_KEY` 和能访问 `api.openai.com`，这个沙箱环境两者都没有，所以没有跑出真实分数。脚本逻辑已验证到"卡在需要真实 API key/网络"这个边界为止，真实跑分需要在有 OpenAI key 的机器上执行。

### [新增] BEIR 检索器泛化能力评测（`tests/beir_retriever_eval.py`）

[BEIR](https://github.com/beir-cellar/beir) 是信息检索领域的标准学术 benchmark，覆盖生物医学/百科/科学事实等多个领域，**不含代码领域**——这条故意选了一个跟代码无关的 benchmark，测的是 `app/rag/vectordb.py` 里 BM25+向量 RRF 融合这套机制本身是否实现正确、是否只是凑巧在代码场景里好用。脚本会在独立的临时 Chroma collection（`--workdir` 下）里跑，不会碰项目真实的 `./data/chroma`。

**验证：**
- 用最小合成 `qrels`/`results` 数据实测了 `EvaluateRetrieval.evaluate()` 的调用方式（`(qrels, results, k_values)`，`staticmethod`，不需要实例化），返回的 `ndcg`/`map`/`recall`/`precision` 四个字典格式与脚本里的解析逻辑一致
- 实际确认了 `GenericDataLoader.load(split="test")` 返回 `(corpus, queries, qrels)` 三元组的字段结构
- **没有跑通的部分：** 真实下载 BEIR 数据集需要访问 `public.ukp.informatik.tu-darmstadt.de`，这个沙箱的出网策略不在白名单内（实测返回 `403 host_not_allowed`）。这是沙箱限制，不是脚本本身的问题——在用户自己的机器上跑没有这个限制。

### 依赖管理

新增 `requirements-eval.txt`（`ragas==0.4.3`、`beir==2.2.0`、`langchain-openai==1.0.3`），故意不并入核心 `requirements.txt`：这两个包只有跑评测脚本才需要，没必要让只想跑 app 本身的人多装一堆依赖。`sqlalchemy==2.0.51` 是持久化层的直接依赖，加进了核心 `requirements.txt`/`pyproject.toml`（之前是 langchain 生态间接带入的，没有显式声明）。

### 本阶段验证汇总

| 验证项 | 方法 | 结果 |
|---|---|---|
| `app/db.py`/`db_models.py`/`metadata.py`/`intent_routing.py` 等新文件语法检查 | `py_compile` | ✅ |
| FastAPI app 实际启动，新路由 `/sources`、`/queries/recent` 注册成功 | `TestClient` | ✅ |
| SQLite 建表（`indexed_sources`、`query_logs`）字段与设计一致 | 直连 sqlite3 查 schema | ✅ |
| 去重/版本号递增/查询日志写入端到端逻辑 | 直接调用 `metadata.py` 函数 | ✅ |
| 7 种 intent 对应的检索参数表 | 直接打印 `get_retrieval_params()` | ✅ |
| `graph.py` 四节点结构在新增字段后仍正确构建 | `build_graph()` | ✅ |
| SWE-QA pinned commit clone+checkout | 实际 clone flask 仓库验证 commit hash | ✅ |
| RAGAS `SingleTurnSample`/`EvaluationDataset` 字段匹配 | 实际构造对象 | ✅ |
| BEIR `EvaluateRetrieval.evaluate()` 调用方式与返回结构 | 合成数据实测 | ✅ |
| RAGAS 真实打分 / BEIR 真实下载数据集 | 需要 OpenAI key / 需要访问被沙箱拦截的域名 | ⚠️ 未在本环境跑通，逻辑已验证到该边界 |


