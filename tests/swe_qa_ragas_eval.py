"""
CodeSentinel — SWE-QA + RAGAS End-to-End Answer Quality Evaluation
====================================================================
tests/eval_metrics.py answers "did the retriever find the right chunk"
(Recall@K / MRR against hand-labelled file:line pairs in eval_labels.json).
This script answers a different question: "is the final ANSWER any good",
using a real published benchmark instead of hand-written questions.

SWE-QA (Peng et al., ACL 2026 Findings — "SWE-QA: Can Language Models
Answer Repository-level Code Questions?", https://arxiv.org/abs/2509.14635)
provides (question, reference_answer) pairs written by humans against pinned
commits of real popular Python projects. tests/data/swe_qa/ bundles two of
the smallest ones (Flask, Requests) under their original Apache-2.0 license
— see tests/data/swe_qa/README.md for attribution and how to add the other
12 projects from the original benchmark.

RAGAS (https://docs.ragas.io) turns (question, generated_answer,
retrieved_contexts, reference_answer) into four LLM-judged scores:

  faithfulness        Is every claim in the answer actually supported by
                       the retrieved chunks? (catches hallucination)
  answer_relevancy     Does the answer actually address the question asked?
  context_precision    Of the chunks retrieved, how many were relevant?
                       (signal pollution in the rerank pool)
  context_recall       Of the information needed for the reference answer,
                       how much was present in the retrieved chunks?
                       (this is the metric that's actually testable against
                       SWE-QA's reference answers — faithfulness/relevancy
                       only need the question+answer+contexts, not the
                       reference, so they work even on benchmarks without
                       structured ground truth)

Usage:
    # 1. Start the backend in another terminal:
    uvicorn app.main:app --reload

    # 2. Run the eval (clones+checks out the pinned Flask commit, indexes
    #    it through the real /index/local endpoint, asks all 48 questions,
    #    scores with RAGAS):
    python tests/swe_qa_ragas_eval.py --repo flask

    # Smaller smoke test (first 5 questions only):
    python tests/swe_qa_ragas_eval.py --repo flask --limit 5

    # Second bundled project:
    python tests/swe_qa_ragas_eval.py --repo requests

Requires (not in core requirements.txt — see requirements-eval.txt):
    pip install -r requirements-eval.txt
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

API_BASE = os.getenv("API_BASE", "http://localhost:8000/api/v1")
DATA_DIR = Path(__file__).parent / "data" / "swe_qa"

# (github_url, pinned_commit) — from the original SWE-QA repo_commit.txt.
# Questions were written against these exact commits; indexing a different
# commit risks file/line drift between the reference answer and what's
# actually in the repo.
REPO_COMMITS = {
    "flask":    ("https://github.com/pallets/flask", "85c5d93"),
    "requests": ("https://github.com/psf/requests", "46e939b"),
}


def api(method: str, path: str, **kwargs):
    resp = requests.request(method, f"{API_BASE}{path}", timeout=600, **kwargs)
    resp.raise_for_status()
    return resp.json()


def print_section(title: str):
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def clone_pinned_repo(name: str, workdir: Path) -> Path:
    url, commit = REPO_COMMITS[name]
    dest = workdir / name
    if not dest.exists():
        print(f"Cloning {url} ...")
        subprocess.run(["git", "clone", "--quiet", url, str(dest)], check=True)
    print(f"Checking out pinned commit {commit} ...")
    subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", commit], check=True)
    return dest


def load_swe_qa(name: str, limit: int | None) -> list[dict]:
    path = DATA_DIR / f"{name}.jsonl"
    if not path.exists():
        sys.exit(
            f"No bundled benchmark file at {path}. Bundled projects: "
            f"{', '.join(sorted(p.stem for p in DATA_DIR.glob('*.jsonl')))}. "
            "Download additional projects from "
            "https://github.com/peng-weihan/SWE-QA-Bench/tree/main/Benchmark "
            "and drop the .jsonl into tests/data/swe_qa/ (see that repo's "
            "Apache-2.0 LICENSE for redistribution terms)."
        )
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return records[:limit] if limit else records


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", choices=sorted(REPO_COMMITS), default="flask")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N questions (smoke test)")
    parser.add_argument("--workdir", default="./eval_workdir", help="Where to clone the target repo")
    parser.add_argument("--skip-index", action="store_true", help="Repo is already indexed — skip cloning/indexing")
    parser.add_argument("--report", default=None, help="Path to write a JSON report (default: tests/data/swe_qa/<repo>_report.json)")
    args = parser.parse_args()

    print_section(f"SWE-QA + RAGAS Evaluation — {args.repo}")

    records = load_swe_qa(args.repo, args.limit)
    print(f"Loaded {len(records)} question/reference-answer pairs")

    if not args.skip_index:
        repo_dir = clone_pinned_repo(args.repo, Path(args.workdir))
        print(f"Indexing {repo_dir} via {API_BASE}/index/local ...")
        idx = api("POST", "/index/local", json={"repo_path": str(repo_dir.resolve())})
        print(f"Indexed {idx['indexed_chunks']} chunks")
    else:
        print("Skipping clone/index (--skip-index) — assuming repo is already indexed.")

    print_section("Running questions through the agent")
    rows = []
    for i, rec in enumerate(records, 1):
        question, reference = rec["question"], rec["answer"]
        t0 = time.monotonic()
        try:
            resp = api("POST", "/ask", json={"query": question, "source_type": "code"})
        except requests.HTTPError as e:
            print(f"  [{i}/{len(records)}] FAILED: {e}")
            continue
        elapsed = time.monotonic() - t0
        contexts = resp.get("retrieved_chunks_text") or [resp.get("answer", "")]
        rows.append({
            "question": question,
            "reference": reference,
            "answer": resp["answer"],
            "contexts": contexts,
            "confidence": resp.get("confidence"),
            "iterations": resp.get("iterations"),
            "latency_s": round(elapsed, 1),
        })
        print(f"  [{i}/{len(records)}] confidence={resp.get('confidence'):<6} "
              f"iter={resp.get('iterations')}  {elapsed:.1f}s  {question[:60]}")

    if not rows:
        sys.exit("No questions answered successfully — nothing to score.")

    print_section("Scoring with RAGAS")
    try:
        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import answer_relevancy, context_precision, context_recall, faithfulness
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    except ImportError:
        sys.exit(
            "ragas / langchain-openai not installed. "
            "Run: pip install -r requirements-eval.txt"
        )

    samples = [
        SingleTurnSample(
            user_input=r["question"],
            response=r["answer"],
            retrieved_contexts=r["contexts"],
            reference=r["reference"],
        )
        for r in rows
    ]
    dataset = EvaluationDataset(samples)

    llm = LangchainLLMWrapper(ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"), temperature=0))
    embeddings = LangchainEmbeddingsWrapper(OpenAIEmbeddings())

    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
        show_progress=True,
    )

    scores_df = result.to_pandas()
    metric_cols = ["faithfulness", "answer_relevancy", "context_precision", "context_recall"]
    means = {m: round(float(scores_df[m].mean()), 3) for m in metric_cols if m in scores_df.columns}

    print_section("Results (mean across all questions)")
    for m, v in means.items():
        print(f"  {m:<20} {v}")

    report_path = Path(args.report) if args.report else DATA_DIR / f"{args.repo}_report.json"
    report_path.write_text(json.dumps({
        "repo": args.repo,
        "pinned_commit": REPO_COMMITS[args.repo][1],
        "num_questions": len(rows),
        "mean_scores": means,
        "per_question": scores_df[["user_input"] + [c for c in metric_cols if c in scores_df.columns]].to_dict(orient="records"),
    }, indent=2, ensure_ascii=False))
    print(f"\nFull report written to {report_path}")


if __name__ == "__main__":
    main()
