"""
CodeSentinel — BEIR Retriever Generalization Benchmark
=========================================================
tests/swe_qa_ragas_eval.py tests the full agent on a code-domain benchmark.
This script tests one specific component in isolation — the hybrid BM25 +
vector retriever in app/rag/vectordb.py — against BEIR (Thakur et al.,
"BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation of Information
Retrieval Models", https://github.com/beir-cellar/beir), a standard
academic IR benchmark.

Why this is a DIFFERENT kind of test than SWE-QA, not a redundant one:
BEIR has no code/software-domain dataset among its ~15 tasks (they're
biomedical, Wikipedia, news, scientific-claim, and web-search domains).
So this isn't "does CodeSentinel answer code questions well" — it's
"does the hybrid retrieval mechanism itself (BM25Retriever + Chroma vector
search fused via EnsembleRetriever/RRF, exactly as implemented in
app/rag/vectordb.py) behave sensibly outside the narrow domain it was built
for", scored with the standard IR metrics (nDCG@10, Recall@100, MAP) that
the BEIR leaderboard reports — so the numbers are comparable to published
baselines (e.g. plain BM25, plain dense retrieval) instead of floating in
isolation.

This script builds its OWN throwaway Chroma collection in --workdir. It
never touches the project's real ./data/chroma index.

Usage:
    pip install -r requirements-eval.txt
    python tests/beir_retriever_eval.py --dataset scifact

Datasets (pick a small one — full BEIR corpora can be 100k+ docs, and
embedding every doc through the OpenAI API has real $ cost):
    nfcorpus    ~3.6k docs,  323 queries   (smallest, good first run)
    scifact     ~5.2k docs,  300 queries   (default — BEIR's own quickstart example)
    arguana     ~8.7k docs, 1406 queries

Requires OPENAI_API_KEY (used for embeddings — same key as the main app).
Requires network access to public.ukp.informatik.tu-darmstadt.de to
download the dataset zip (this sandbox's network policy may block that
host even though it can run this script; if `download_and_unzip` fails
with a connection error, run this on a machine without that restriction).
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

BEIR_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"


def print_section(title: str):
    """Print a formatted section header to improve console readability."""
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def parse_arguments():
    """Parse command-line arguments for dataset selection, weights, and evaluation settings."""
    from app.config import config

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dataset", default="scifact", choices=["scifact", "nfcorpus", "arguana"])
    parser.add_argument("--workdir", default="./eval_workdir/beir")
    parser.add_argument("--k-values", type=int, nargs="+", default=[1, 5, 10, 100])
    parser.add_argument("--bm25-weight", type=float, default=None, help="Override config.BM25_WEIGHT for this run")
    parser.add_argument("--vector-weight", type=float, default=None, help="Override config.VECTOR_WEIGHT for this run")
    parser.add_argument("--max-queries", type=int, default=None, help="Smoke-test on a subset of queries")
    parser.add_argument("--keep-collection", action="store_true", help="Don't wipe the throwaway Chroma collection first")
    parser.add_argument("--report", default=None)
    return parser.parse_args()


def load_beir_dataset(dataset_name: str, workdir: Path, max_queries: int | None):
    """Download and load the BEIR dataset, optionally truncating the query set for quick testing."""
    from beir import util
    from beir.datasets.data_loader import GenericDataLoader

    print(f"Downloading {args.dataset} (skipped if already cached in {workdir}) ...")
    data_path = util.download_and_unzip(BEIR_URL.format(name=args.dataset), str(workdir))
    corpus, queries, qrels = GenericDataLoader(data_folder=data_path).load(split="test")
    print(f"Loaded {len(corpus)} docs, {len(queries)} queries, "
          f"{sum(len(v) for v in qrels.values())} relevance judgements")

    # Truncate queries if --max-queries is specified (useful for smoke testing)
    if args.max_queries:
        queries = dict(list(queries.items())[:args.max_queries])
        qrels = {qid: qrels[qid] for qid in queries if qid in qrels}
        print(f"--max-queries set: evaluating on {len(queries)} queries")

    return corpus, queries, qrels


def build_vector_index(corpus: dict, workdir: Path, dataset_name: str, keep_collection: bool):
    """Build an isolated Chroma vector index in the specified working directory."""
    from langchain_chroma import Chroma
    from langchain_core.documents import Document
    from app.rag.embedding import get_embedding_model

    collection_dir = workdir / "chroma"
    # Clean up existing isolated collection unless explicitly told to keep it
    if collection_dir.exists() and not args.keep_collection:
        shutil.rmtree(collection_dir)

    # Convert raw corpus into LangChain Document objects
    docs = [
        Document(
            page_content=f"{meta.get('title', '')} {meta['text']}".strip(),
            metadata={"doc_id": doc_id},
        )
        for doc_id, meta in corpus.items()
    ]

    # Initialize the Chroma database with the project's embedding model
    db = Chroma(
        collection_name=f"beir_{args.dataset}",
        embedding_function=get_embedding_model(),
        persist_directory=str(collection_dir),
    )

    # Embed and add documents in batches to avoid API rate limits
    if not args.keep_collection or db._collection.count() == 0:
        print(f"Embedding {len(docs)} documents via OpenAI (this has real $ cost) ...")
        batch_size = 500
        for i in range(0, len(docs), batch_size):
            db.add_documents(docs[i:i + batch_size])
    else:
        print(f"Reusing existing collection ({db._collection.count()} docs) — pass without --keep-collection to rebuild.")

    return db, docs


def run_hybrid_retrieval(queries: dict, db, docs:list, bm25_weight: float, vector_weight: float, k_values: list[int]):
    """Execute hybrid retrieval (BM25 + Vector) and return the formatted results dictionary."""
    from langchain_community.retrievers import BM25Retriever
    from langchain_classic.retrievers import EnsembleRetriever

    max_k = max(args.k_values)

    bm25 = BM25Retriever.from_documents(docs, k=max_k)
    vector = db.as_retriever(search_kwargs={"k": max_k})
    ensemble = EnsembleRetriever(retrievers=[bm25, vector], weights=[bm25_weight, vector_weight])

    print_section(f"Running {len(queries)} queries  (bm25={bm25_weight}, vector={vector_weight})")
    results: dict[str, dict[str, float]] = {}
    for i, (qid, qtext) in enumerate(queries.items(), 1):
        ranked = ensemble.invoke(qtext)
        # BEIR's evaluator wants {doc_id: score}; ensemble.invoke() already
        # returns RRF-fused rank order, so a synthetic descending score
        # (1/(rank+1)) just encodes that same order for pytrec_eval — the
        # absolute values aren't otherwise used.
        results[qid] = {d.metadata["doc_id"]: 1.0 / (rank + 1) for rank, d in enumerate(ranked)}
        if i % 25 == 0 or i == len(queries):
            print(f"  {i}/{len(queries)} queries done")

    return results


def evaluate_and_report(results: dict, qrels: dict, k_values: list[int], 
                        dataset_name: str, num_docs: int, num_queries: int,
                        bm25_weight: float, vector_weight: float, report_path: Path):
    """Compute BEIR evaluation metrics and save the final JSON report."""
    from beir.retrieval.evaluation import EvaluateRetrieval
    
    print_section("BEIR Metrics")
    ndcg, _map, recall, precision = EvaluateRetrieval.evaluate(qrels, results, args.k_values)
    for name, metric in [("nDCG", ndcg), ("MAP", _map), ("Recall", recall), ("Precision", precision)]:
        for k, v in metric.items():
            print(f"  {name}@{k.split('@')[-1]:<5} {v:.4f}")

    # Compile evaluation data and write to a JSON file
    report_path = Path(args.report) if args.report else workdir / f"{args.dataset}_report.json"
    report_path.write_text(json.dumps({
        "dataset": args.dataset,
        "num_docs": len(corpus),
        "num_queries": len(queries),
        "bm25_weight": bm25_weight,
        "vector_weight": vector_weight,
        "ndcg": ndcg, "map": _map, "recall": recall, "precision": precision,
    }, indent=2))
    print(f"\nFull report written to {report_path}")    

def main():
    # Parse command-line arguments
    args = parse_arguments()

    #  Check core dependencies
    try:
        from beir import util
        from beir.datasets.data_loader import GenericDataLoader
        from beir.retrieval.evaluation import EvaluateRetrieval
    except ImportError:
        sys.exit("beir not installed. Run: pip install -r requirements-eval.txt")
    
    # Prepare the working directory
    print_section(f"BEIR Retriever Benchmark — {args.dataset}")
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # Load the BEIR dataset
    corpus, queries, qrels = load_beir_dataset(args.dataset, workdir, args.max_queries)
    
    # Build the isolated vector index
    db, docs = build_vector_index(corpus, workdir, args.dataset, args.keep_collection)

    # Determine retrieval weights (CLI overrides take precedence over config)
    bm25_weight = args.bm25_weight if args.bm25_weight is not None else config.BM25_WEIGHT
    vector_weight = args.vector_weight if args.vector_weight is not None else config.VECTOR_WEIGHT
    
    # Execute hybrid retrieval
    results = run_hybrid_retrieval(queries, db, docs, bm25_weight, vector_weight, args.k_values)

    # Evaluate metrics and generate the final report
    report_path = Path(args.report) if args.report else workdir / f"{args.dataset}_report.json"
    evaluate_and_report(
        results, qrels, args.k_values, args.dataset, len(corpus), len(queries),
        bm25_weight, vector_weight, report_path
    )


if __name__ == "__main__":
    main()
