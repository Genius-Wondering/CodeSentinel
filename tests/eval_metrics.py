"""
CodeSentinel — 量化指标评测脚本
=================================
用法：
  # 仅跑无需标注的指标（索引吞吐量、延迟、Chunk 覆盖率）
  python tests/eval_metrics.py --no-labels --repo-path /path/to/repo

  # 跑全部指标（含 Recall@K、MRR，需要 eval_labels.json）
  python tests/eval_metrics.py --all --repo-path /path/to/repo

  # 仅跑延迟测试（仓库已索引）
  python tests/eval_metrics.py --latency-only

环境要求：
  - 后端已启动：uvicorn app.main:app --reload
  - OPENAI_API_KEY 已配置
  - 可选：tests/eval_labels.json 已填写（见 METRICS.md）
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")
LABELS_FILE = Path(__file__).parent / "eval_labels.json"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def api(method: str, path: str, **kwargs):
    url = f"{API_BASE}{path}"
    resp = requests.request(method, url, timeout=300, **kwargs)
    resp.raise_for_status()
    return resp.json()


def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(name: str, value, unit: str = ""):
    print(f"  {name:<40} {value}{unit}")


# ─────────────────────────────────────────────────────────────────────────────
# Metric 7: Chunk Coverage (AST vs Fallback)
# ─────────────────────────────────────────────────────────────────────────────

def measure_chunk_coverage(repo_path: str) -> dict:
    """
    Index the repo and measure what fraction of chunks came from AST parsing
    vs the line-based fallback.
    AST chunks have kind='function' or 'class'; fallback chunks have kind='block'.
    """
    print_section("指标 7: Chunk 覆盖率（AST vs Fallback）")

    t0 = time.perf_counter()
    result = api("POST", "/index/local", json={"repo_path": repo_path})
    elapsed = time.perf_counter() - t0

    total_chunks = result["indexed_chunks"]
    print_result("总 chunk 数", total_chunks)
    print_result("索引耗时", f"{elapsed:.1f}", "s")

    # Query the vector store directly to count kinds
    # We do this by asking a broad query and inspecting metadata
    # Better: directly import and count if running in-process
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from app.rag.vectordb import get_vector_store
        store = get_vector_store()
        corpus = store._corpus
        if corpus:
            ast_kinds = {"function", "class"}
            ast_count = sum(1 for d in corpus if d.metadata.get("kind") in ast_kinds)
            fallback_count = sum(1 for d in corpus if d.metadata.get("kind") == "block")
            ast_pct = ast_count / total_chunks * 100 if total_chunks else 0
            print_result("AST chunks (function/class)", ast_count)
            print_result("Fallback chunks (block)", fallback_count)
            print_result("AST 覆盖率", f"{ast_pct:.1f}", "%")
            return {
                "total_chunks": total_chunks,
                "ast_chunks": ast_count,
                "fallback_chunks": fallback_count,
                "ast_coverage_pct": round(ast_pct, 1),
                "index_time_s": round(elapsed, 1),
            }
    except Exception as e:
        print(f"  (无法直接读取 corpus，跳过分类统计: {e})")

    return {"total_chunks": total_chunks, "index_time_s": round(elapsed, 1)}


# ─────────────────────────────────────────────────────────────────────────────
# Metric 4: Indexing Throughput
# ─────────────────────────────────────────────────────────────────────────────

def measure_indexing_throughput(repo_path: str) -> dict:
    """
    Index the repo and measure chunks/second throughput.
    Already covered in chunk_coverage if called together; standalone for --latency-only bypass.
    """
    print_section("指标 4: 索引吞吐量")
    t0 = time.perf_counter()
    result = api("POST", "/index/local", json={"repo_path": repo_path})
    elapsed = time.perf_counter() - t0
    chunks = result["indexed_chunks"]
    throughput = chunks / elapsed if elapsed > 0 else 0
    print_result("总 chunk 数", chunks)
    print_result("耗时", f"{elapsed:.1f}", "s")
    print_result("吞吐量", f"{throughput:.1f}", " chunks/s")
    return {"chunks": chunks, "time_s": round(elapsed, 1), "chunks_per_sec": round(throughput, 1)}


# ─────────────────────────────────────────────────────────────────────────────
# Metric 5 & 6: Latency (E2E + Retrieval)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_QUERIES = [
    "认证逻辑在哪里实现的",
    "向量数据库是如何初始化的",
    "BM25 检索器是如何工作的",
    "代码是如何被分块的",
    "Cross-Encoder 重排序是如何工作的",
    "配置项有哪些",
    "错误处理是如何实现的",
    "API 路由是如何注册的",
    "嵌入模型用的是什么",
    "文档分块和代码分块有什么区别",
]


def measure_latency(queries: list = None, n: int = 10) -> dict:
    """
    Measure E2E latency over N queries.
    Returns p50, p95, min, max.
    """
    print_section("指标 5: 端到端查询延迟 (E2E Latency)")
    queries = (queries or DEFAULT_QUERIES)[:n]
    latencies = []

    for i, q in enumerate(queries, 1):
        t0 = time.perf_counter()
        try:
            api("POST", "/ask", json={"query": q})
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            print(f"  [{i:2d}/{len(queries)}] {elapsed_ms:6.0f}ms  {q[:50]}")
        except Exception as e:
            print(f"  [{i:2d}/{len(queries)}] ERROR: {e}")

    if not latencies:
        print("  (no successful queries)")
        return {}

    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95)]

    print()
    print_result("p50 (median)", f"{p50/1000:.2f}", "s")
    print_result("p95", f"{p95/1000:.2f}", "s")
    print_result("min", f"{min(latencies)/1000:.2f}", "s")
    print_result("max", f"{max(latencies)/1000:.2f}", "s")

    return {
        "n": len(latencies),
        "p50_s": round(p50 / 1000, 2),
        "p95_s": round(p95 / 1000, 2),
        "min_s": round(min(latencies) / 1000, 2),
        "max_s": round(max(latencies) / 1000, 2),
    }


def measure_retrieval_latency(queries: list = None, n: int = 10) -> dict:
    """
    Measure retrieval-only latency by calling VectorStore.search() directly (in-process).
    Bypasses LLM calls entirely.
    """
    print_section("指标 6: 检索阶段延迟（不含 LLM）")
    queries = (queries or DEFAULT_QUERIES)[:n]

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from app.rag.vectordb import get_vector_store
        store = get_vector_store()
        if store.count() == 0:
            print("  (索引为空，跳过检索延迟测试)")
            return {}
    except Exception as e:
        print(f"  (无法直接访问 VectorStore: {e})")
        return {}

    latencies_hybrid = []
    latencies_vector = []

    for q in queries:
        # Hybrid search
        t0 = time.perf_counter()
        store.search(q, k=5)
        latencies_hybrid.append((time.perf_counter() - t0) * 1000)

        # Pure vector (bypass BM25)
        t0 = time.perf_counter()
        store.db.similarity_search(q, k=5)
        latencies_vector.append((time.perf_counter() - t0) * 1000)

    def stats(lats):
        lats = sorted(lats)
        return {
            "p50_ms": round(lats[len(lats) // 2], 1),
            "p95_ms": round(lats[int(len(lats) * 0.95)], 1),
        }

    h = stats(latencies_hybrid)
    v = stats(latencies_vector)

    print_result("混合检索 (BM25 + Vector) p50", f"{h['p50_ms']}", "ms")
    print_result("混合检索 (BM25 + Vector) p95", f"{h['p95_ms']}", "ms")
    print_result("纯向量搜索 p50", f"{v['p50_ms']}", "ms")
    print_result("纯向量搜索 p95", f"{v['p95_ms']}", "ms")
    print_result("BM25 额外开销（中位数）", f"{h['p50_ms'] - v['p50_ms']:.1f}", "ms")

    return {"hybrid": h, "vector": v}


# ─────────────────────────────────────────────────────────────────────────────
# Metric 1, 2, 3: Recall@K, MRR, Hybrid vs Vector Delta
# ─────────────────────────────────────────────────────────────────────────────

def _chunk_matches(retrieved_docs: list, correct_chunks: list) -> bool:
    """
    Return True if any retrieved doc matches any correct chunk.
    Match criterion: same file AND start_line within ±10 lines (AST boundaries vary).
    """
    for doc in retrieved_docs:
        meta = doc.metadata
        for c in correct_chunks:
            if meta.get("file", "").endswith(c["file"]):
                if abs(meta.get("start_line", -999) - c["start_line"]) <= 10:
                    return True
    return False


def _reciprocal_rank(retrieved_docs: list, correct_chunks: list) -> float:
    """Return 1/rank where rank is the position of the first correct chunk (1-indexed)."""
    for i, doc in enumerate(retrieved_docs, 1):
        meta = doc.metadata
        for c in correct_chunks:
            if meta.get("file", "").endswith(c["file"]):
                if abs(meta.get("start_line", -999) - c["start_line"]) <= 10:
                    return 1.0 / i
    return 0.0


def measure_retrieval_quality(labels_path: Path, k: int = 5) -> dict:
    """
    Measure Recall@K and MRR using a labelled query set.
    Also compares hybrid vs pure-vector to compute Δ Recall@K.
    """
    print_section(f"指标 1–3: Recall@{k}, MRR, 混合 vs 纯向量")

    if not labels_path.exists():
        print(f"  标注文件未找到: {labels_path}")
        print("  请参考 METRICS.md 创建 tests/eval_labels.json")
        return {}

    with open(labels_path, encoding="utf-8") as f:
        labels = json.load(f)

    print(f"  标注集大小: {len(labels)} 条")

    try:
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from app.rag.vectordb import get_vector_store
        from app.rag.chunker import CodeChunker
        store = get_vector_store()
        if store.count() == 0:
            print("  (索引为空，跳过)")
            return {}
    except Exception as e:
        print(f"  (无法访问 VectorStore: {e})")
        return {}

    # --- Hybrid search results ---
    hybrid_hits = 0
    hybrid_rr_sum = 0.0
    vector_hits = 0
    vector_rr_sum = 0.0

    by_type = {"natural": {"hybrid": 0, "vector": 0, "total": 0},
               "exact_identifier": {"hybrid": 0, "vector": 0, "total": 0}}

    for item in labels:
        q = item["query"]
        correct = item["correct_chunks"]
        qtype = item.get("type", "natural")

        # Hybrid
        h_docs = store.search(q, k=k)
        hit_h = _chunk_matches(h_docs, correct)
        rr_h = _reciprocal_rank(h_docs, correct)
        if hit_h:
            hybrid_hits += 1
        hybrid_rr_sum += rr_h

        # Pure vector
        v_docs = store.db.similarity_search(q, k=k)
        hit_v = _chunk_matches(v_docs, correct)
        rr_v = _reciprocal_rank(v_docs, correct)
        if hit_v:
            vector_hits += 1
        vector_rr_sum += rr_v

        # By type
        if qtype in by_type:
            by_type[qtype]["total"] += 1
            if hit_h:
                by_type[qtype]["hybrid"] += 1
            if hit_v:
                by_type[qtype]["vector"] += 1

    n = len(labels)
    hybrid_recall = hybrid_hits / n * 100
    vector_recall = vector_hits / n * 100
    hybrid_mrr = hybrid_rr_sum / n
    vector_mrr = vector_rr_sum / n
    delta = hybrid_recall - vector_recall

    print()
    print(f"  {'指标':<35} {'混合检索':>12} {'纯向量':>12} {'提升Δ':>10}")
    print(f"  {'-'*70}")
    print(f"  {'Recall@' + str(k) + ' (overall)':<35} {hybrid_recall:>11.1f}% {vector_recall:>11.1f}% {delta:>+9.1f}pp")
    print(f"  {'MRR':<35} {hybrid_mrr:>12.3f} {vector_mrr:>12.3f} {hybrid_mrr-vector_mrr:>+10.3f}")

    for qtype, counts in by_type.items():
        t = counts["total"]
        if t == 0:
            continue
        hr = counts["hybrid"] / t * 100
        vr = counts["vector"] / t * 100
        label = f"Recall@{k} ({qtype})"
        print(f"  {label:<35} {hr:>11.1f}% {vr:>11.1f}% {hr-vr:>+9.1f}pp")

    return {
        "n": n, "k": k,
        "hybrid_recall_pct": round(hybrid_recall, 1),
        "vector_recall_pct": round(vector_recall, 1),
        "delta_pp": round(delta, 1),
        "hybrid_mrr": round(hybrid_mrr, 3),
        "vector_mrr": round(vector_mrr, 3),
        "by_type": by_type,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CodeSentinel 量化指标评测")
    parser.add_argument("--repo-path", default=None, help="要索引的本地仓库路径")
    parser.add_argument("--no-labels", action="store_true", help="只跑无需标注的指标（4–7）")
    parser.add_argument("--all", action="store_true", help="跑全部指标（含 Recall/MRR）")
    parser.add_argument("--latency-only", action="store_true", help="只跑延迟指标（假设已索引）")
    parser.add_argument("--k", type=int, default=5, help="Recall@K 的 K 值（默认 5）")
    args = parser.parse_args()

    print("\n🔍 CodeSentinel 量化指标评测")
    print(f"   API: {API_BASE}")

    # Check API reachable
    try:
        health = api("GET", "/health")
        print(f"   后端状态: {health['status']} ({health['service']})")
    except Exception as e:
        print(f"\n❌ 无法连接后端 ({API_BASE}): {e}")
        print("   请先启动: uvicorn app.main:app --reload")
        sys.exit(1)

    results = {}

    if args.latency_only:
        results["latency"] = measure_latency()
        results["retrieval_latency"] = measure_retrieval_latency()
    elif args.no_labels:
        if not args.repo_path:
            print("❌ --no-labels 需要 --repo-path 参数")
            sys.exit(1)
        results["chunk_coverage"] = measure_chunk_coverage(args.repo_path)
        results["latency"] = measure_latency()
        results["retrieval_latency"] = measure_retrieval_latency()
    else:  # --all or default
        if args.repo_path:
            results["chunk_coverage"] = measure_chunk_coverage(args.repo_path)
        results["latency"] = measure_latency()
        results["retrieval_latency"] = measure_retrieval_latency()
        results["retrieval_quality"] = measure_retrieval_quality(LABELS_FILE, k=args.k)

    # Save results
    out = Path(__file__).parent / "eval_results.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print_section("评测完成")
    print(f"  结果已保存至: {out}")
    print()
    print("  📋 简历模板（填入实测数字）：")

    q = results.get("retrieval_quality", {})
    perf = results.get("chunk_coverage", {})
    lat = results.get("latency", {})

    recall = q.get("hybrid_recall_pct", "XX")
    delta = q.get("delta_pp", "YY")
    chunks = perf.get("total_chunks", "NNN")
    idx_t = perf.get("index_time_s", "T")
    p50 = lat.get("p50_s", "X.X")
    ast_cov = perf.get("ast_coverage_pct", "XX")

    print(f"""
  • Built hybrid RAG pipeline (BM25 + OpenAI embeddings, RRF fusion);
    Recall@{args.k} = {recall}%, +{delta}pp over pure vector baseline.

  • 3-node LangGraph agent (Planner→Retriever→Reviewer);
    indexed {chunks} chunks in {idx_t}s, median E2E latency {p50}s.

  • AST-based chunking (tree-sitter) achieved {ast_cov}% coverage
    vs character-count fallback.
""")


if __name__ == "__main__":
    main()
