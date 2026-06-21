# CodeSentinel — 量化指标说明与评测指南

> 本文档说明可量化的性能指标、每项指标的含义、预期效果、以及对应的评测方法。  
> 目标读者：跑完评测后将数字写入简历/项目报告。

---

## 一、指标总览

| # | 指标 | 类型 | 无需标注？ | 简历价值 |
|---|---|---|---|---|
| 1 | **Recall@K** | 检索质量 | 需要标注集 | ⭐⭐⭐⭐⭐ |
| 2 | **MRR（Mean Reciprocal Rank）** | 检索质量 | 需要标注集 | ⭐⭐⭐⭐ |
| 3 | **混合检索 vs 纯向量检索提升** | 对比实验 | 需要标注集 | ⭐⭐⭐⭐⭐ |
| 4 | **索引吞吐量** | 系统性能 | 无需标注 ✅ | ⭐⭐⭐ |
| 5 | **端到端查询延迟** | 系统性能 | 无需标注 ✅ | ⭐⭐⭐ |
| 6 | **检索阶段延迟** | 系统性能 | 无需标注 ✅ | ⭐⭐ |
| 7 | **Chunk 覆盖率** | 索引质量 | 无需标注 ✅ | ⭐⭐ |
| 8 | **RAGAS（Faithfulness / Context Precision/Recall / Answer Relevancy）** | 端到端答案质量 | 用 SWE-QA 公开标注集，无需自标 ✅ | ⭐⭐⭐⭐⭐ |
| 9 | **BEIR（nDCG@10 / Recall@100 / MAP）** | 检索器泛化能力 | 用 BEIR 公开标注集，无需自标 ✅ | ⭐⭐⭐ |

---

## 二、指标详细说明

### 指标 1：Recall@K（最重要）

**是什么：** 给定一个问题，检索出的 top-K 个结果里，包含正确答案的比例。

```
Recall@5 = (包含正确 chunk 的查询数) / (总查询数)
```

**为什么重要：** RAG 系统的天花板由检索质量决定。如果正确的代码 chunk 没进入 top-K，后面的 LLM 无论多强都无法给出正确答案。Recall@K 直接衡量这个瓶颈。

**预期效果（基于项目设计）：**
- 纯向量搜索（baseline）：Recall@5 约 65–75%（自然语言问题表现好，精确标识符差）
- 混合搜索（BM25 + Vector）：Recall@5 约 80–90%，精确标识符查询提升 **+15~25 pp**
- 学术 baseline 参考：CodeSearchNet Python 子集，顶级系统 Recall@5 约 80–85%

**简历写法示例：**
> "Improved Recall@5 from 68% to 86% by replacing pure vector search with hybrid BM25+vector retrieval (RRF fusion), a +18pp improvement on exact identifier queries."

---

### 指标 2：MRR（Mean Reciprocal Rank）

**是什么：** 正确答案在排名列表中位置的倒数，取所有查询的平均值。

```
MRR = (1/N) × Σ (1 / rank_i)
```

例：正确 chunk 排在第 1 位 → 1.0；第 2 位 → 0.5；第 5 位 → 0.2。

**为什么重要：** 比 Recall@K 更细致，反映正确结果是排在第 1 还是第 5（对用户体验差异巨大）。

**预期效果：**
- 纯向量：MRR ≈ 0.55–0.65
- 混合检索：MRR ≈ 0.70–0.80

---

### 指标 3：混合检索 vs 纯向量提升（Δ Recall@5）

**是什么：** 同一评测集上，`BM25_WEIGHT=0.4/VECTOR_WEIGHT=0.6` 对比 `BM25_WEIGHT=0/VECTOR_WEIGHT=1.0` 的 Recall@5 差值。

**为什么重要：** 直接论证混合检索设计的价值，是项目核心技术贡献之一，面试中最容易被追问。

**要分类测量：**
- 自然语言问题（"认证逻辑在哪里？"）→ 向量搜索本来就好，提升有限
- 精确标识符问题（"RETRIEVER_TOP_K 在哪里用到？"）→ BM25 优势明显，提升显著

---

### 指标 4：索引吞吐量

**是什么：** 单位时间内能处理的代码文件数或 chunk 数。

```
吞吐量 = 总 chunk 数 / 总耗时（秒）
```

**为什么重要：** 展示系统工程能力，说明能处理真实规模的仓库。

**无需标注，直接跑：** 对 `fastapi/fastapi`（约 150 个 .py 文件）或 `requests/requests` 计时。

**预期效果：**
- 主要瓶颈在 OpenAI Embedding API（每次请求约 0.5–2s）
- 批量调用后约 30–120 秒索引完中等规模仓库（100–300 文件）

**简历写法示例：**
> "Indexed 2,800+ AST-parsed chunks from a 150-file Python codebase in ~45 seconds via batched OpenAI embedding calls."

---

### 指标 5：端到端查询延迟（E2E Latency）

**是什么：** 从发出 `/ask` POST 请求到收到完整响应的时间。包含：Planner LLM 调用 + 检索 + Reviewer LLM 调用。

**分解：**
```
E2E = T_planner + T_retrieval + T_reviewer
    ≈ 0.5–2s   + 0.1–0.5s   + 1–5s      (GPT-4o-mini)
```

**无需标注，直接跑：** 准备 20 个查询，用脚本批量发请求记时，取 p50/p95。

**预期效果（GPT-4o-mini）：**
- p50 约 3–6s
- p95 约 8–15s（受 OpenAI 服务端波动影响大）

**简历写法示例：**
> "Achieved median end-to-end query latency of 4.2s (p95: 9.8s) using GPT-4o-mini with a 3-node LangGraph pipeline (plan → retrieve → review)."

---

### 指标 6：检索阶段延迟

**是什么：** 只计算 `RetrieverAgent.retrieve_multi()` 的耗时，不含 LLM 调用。

**为什么单独测：** 检索是可以纯本地优化的部分（不依赖 OpenAI）。BM25 重建是主要开销。

**预期效果：**
- 纯向量搜索：10–50ms（Chroma HNSW）
- 混合搜索（含 BM25 重建）：50–200ms（与 corpus 大小线性相关）
- corpus 3000 chunks 时 BM25 重建约 30–80ms

---

### 指标 7：Chunk 覆盖率（AST vs Fallback 比例）

**是什么：** 成功用 tree-sitter AST 解析的 chunk 占总 chunk 的比例（vs fallback 的行分割块）。

```
AST 覆盖率 = AST chunks / (AST chunks + fallback blocks) × 100%
```

**为什么重要：** AST chunks 带有语义边界（函数/类），召回质量更高。Fallback blocks 是任意行切割，语义破碎。这个比例反映索引质量。

**预期效果：** 纯 Python 仓库应达到 85–95%（少数文件因语法错误或编码问题走 fallback）。

---

### 指标 8：RAGAS 端到端答案质量（用公开 benchmark，无需自己标注）

**是什么：** 不再只看"检索到的 chunk 对不对"，而是看"最终答案本身好不好"。用 [SWE-QA](https://arxiv.org/abs/2509.14635)（ACL 2026 Findings）这个公开的仓库级代码问答 benchmark 提供的真实问题 + 人工参考答案，跑完整个 Plan→Retrieve→Rerank→Review 链路后，用 [RAGAS](https://docs.ragas.io) 打分：

- **Faithfulness**：答案里的每个论断是否都能在检索到的 chunk 里找到支撑（抓幻觉）
- **Answer Relevancy**：答案是否真的回答了问题本身
- **Context Precision**：检索到的 chunk 里有多少是真正相关的（噪声占比）
- **Context Recall**：参考答案需要的信息，有多大比例出现在检索到的 chunk 里

**为什么这个指标比自己标注的 Recall@K 更有说服力：** 标注集是自己写的，面试官可以合理怀疑"问题是不是刻意凑的"；SWE-QA 是已发表论文的公开 benchmark，问题和参考答案都不是你写的，分数更有第三方可信度。

**预期效果：** 没有发表前的真实跑分对比，不编造具体数字——这四个分数都是 0~1 之间的 LLM 评分，实测后把真实数字填进去即可，不要用占位符充数。

**怎么跑：**
```bash
pip install -r requirements-eval.txt
uvicorn app.main:app --reload &
python tests/swe_qa_ragas_eval.py --repo flask
```

---

### 指标 9：BEIR 检索器泛化能力（用公开 benchmark，无需自己标注）

**是什么：** 单独把 `app/rag/vectordb.py` 里的混合检索机制（BM25 + 向量 + RRF）拿出来，跑在 [BEIR](https://github.com/beir-cellar/beir) 这个学术界标准信息检索 benchmark 上（跨生物医学/百科/科学事实等多个领域，不含代码领域），用 nDCG@10 / Recall@100 / MAP 这些 BEIR 排行榜通用指标打分。

**为什么要做这个，跟指标 1-3 不重复吗：** 不重复。Recall@K（指标1-3）证明的是"这套检索机制能不能在*代码*领域工作"；BEIR 证明的是"这套检索机制本身的实现是不是对的、是不是只是在代码这个特定场景里凑巧好用"——能在一个跟代码完全无关的学术 benchmark 上跑出合理分数，是更强的工程正确性证据。

**预期效果：** 同样不编造数字。BEIR 官方排行榜上纯 BM25 在 SciFact 上 nDCG@10 约 0.665，纯向量（OpenAI embedding）量级相近或略高；混合检索通常不会比两者中更好的那个差太多。实测后把真实数字和这两个公开 baseline 放在一起对比，比单独一个数字有说服力。

**怎么跑：**
```bash
pip install -r requirements-eval.txt
python tests/beir_retriever_eval.py --dataset scifact
```

---

## 三、评测代码

评测脚本位于 `tests/eval_metrics.py`，涵盖指标 1–3（需要标注集）和指标 4–7（无需标注）。

### 快速运行方法

```bash
# 先索引目标仓库（替换为你的路径）
curl -X POST http://localhost:8000/api/v1/index/local \
     -H "Content-Type: application/json" \
     -d '{"repo_path": "/path/to/your/repo"}'

# 运行全部无需标注的指标（指标 4-7）
python tests/eval_metrics.py --no-labels

# 运行全部指标（需要先编辑 tests/eval_labels.json 填写标注集）
python tests/eval_metrics.py --all
```

### 标注集格式（`tests/eval_labels.json`）

```json
[
  {
    "query": "JWT token 验证逻辑在哪里实现的？",
    "type": "natural",
    "correct_chunks": [
      {"file": "app/auth.py", "start_line": 45}
    ]
  },
  {
    "query": "RETRIEVER_TOP_K 这个配置在哪里被用到？",
    "type": "exact_identifier",
    "correct_chunks": [
      {"file": "app/agent/retriever.py", "start_line": 1}
    ]
  }
]
```

建议准备 20–30 条，其中约 40% 为精确标识符类型（`exact_identifier`），以便体现混合检索的优势。

### 另外两个脚本（指标 8、9）

`tests/eval_metrics.py` 的标注集是手写的；下面两个用的是公开发表的 benchmark，不需要自己标注，详见上面指标 8、9 的说明，以及各脚本文件头部的 docstring：

- `tests/swe_qa_ragas_eval.py` — 需要 `uvicorn app.main:app --reload` 跑着，会先 clone 目标仓库的 pinned commit 再索引
- `tests/beir_retriever_eval.py` — 不需要起后端，直接对 `app/rag/vectordb.py` 的检索逻辑跑分；需要联网下载 BEIR 数据集（下载地址见脚本内 `BEIR_URL`，若所在网络环境屏蔽该域名需更换网络）

两者都需要 `pip install -r requirements-eval.txt`（ragas + beir，刻意没有放进核心 `requirements.txt`，避免给只是想跑 app 本身的人装一堆评测专用依赖）。

---

## 四、Baseline 参考

| 来源 | 指标 | 数值 | 说明 |
|---|---|---|---|
| CodeSearchNet (Python) | Recall@5 | ~80–85% | 学术 NL→code 检索，不完全对等 |
| Pure OpenAI Ada-002 embedding | Recall@5 | ~65–72% | 纯向量，无 BM25，可作为你的 baseline |
| BM25 only (Okapi BM25) | Recall@5 | ~55–65% | 纯关键词，无语义 |
| 本项目目标（混合） | Recall@5 | **≥ 80%** | 混合检索预期上限 |

> 注：CodeSearchNet 是"给自然语言 docstring 找对应函数"任务，而本项目是"给自由问题找相关代码片段"，两者场景有差异，数字不能直接比较，但可作为量级参考。

---

## 五、简历数字填写模板

跑完评测后，按以下模板填写（括号内替换为实测数字）：

```
• Built a hybrid RAG pipeline (BM25 + OpenAI embeddings, RRF fusion) over AST-parsed Python codebases;
  Recall@5 = [XX]% overall, +[YY]pp over pure vector baseline on exact identifier queries.

• Designed a 4-node LangGraph agent (Plan → Retrieve → Rerank → Review) with a confidence-gated
  feedback loop; indexed [NNN] chunks from a [M]-file codebase in [T]s, median E2E query latency [X.X]s.

• Implemented structure-aware chunking (tree-sitter AST + Markdown header splitting);
  AST parse coverage [XX]% vs character-count fallback.

• Built intent-adaptive retrieval routing: Planner's classified query intent sets per-query
  BM25/vector weighting and fetch-pool size, instead of a single fixed hybrid-search config.

• Persisted document/repo metadata and query logs to a relational store (SQLAlchemy, SQLite→MySQL/
  Postgres-portable) with content-hash deduplication, replacing in-memory state lost on restart.

• Evaluated end-to-end answer quality with RAGAS (Faithfulness/Context Precision/Recall/Answer
  Relevancy) against the published SWE-QA benchmark; Faithfulness = [X.XX], Context Recall = [X.XX]
  on [N] real-repo questions ([repo name]).

• Validated the hybrid retriever's generalization on the BEIR IR benchmark ([dataset name]):
  nDCG@10 = [X.XXX] vs published BM25-only baseline [X.XXX].
```

**关于第 6/7 条：跑完之后把真实数字填进去，跑不出来或数字不好看也不要编。** 面试官大概率会顺着这两条往下问"具体怎么测的""baseline 是什么""为什么选这个 benchmark"——这恰好是 SWE-QA/BEIR 这种公开 benchmark 的价值所在：你答得出来，因为方法论是公开论文/标准排行榜定义的，不是自己拍的。
