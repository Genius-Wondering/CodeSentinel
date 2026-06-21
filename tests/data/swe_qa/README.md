# SWE-QA Benchmark Data — Attribution

The two `.jsonl` files in this directory (`flask.jsonl`, `requests.jsonl`)
are unmodified copies of two project subsets from the **SWE-QA** benchmark.

- **Paper**: Peng et al., *"SWE-QA: Can Language Models Answer
  Repository-level Code Questions?"*, ACL 2026 Findings.
  https://arxiv.org/abs/2509.14635
- **Source repository**: https://github.com/peng-weihan/SWE-QA-Bench
- **Dataset (Hugging Face)**: https://huggingface.co/datasets/swe-qa/SWE-QA-Benchmark
- **License**: Apache License 2.0 — see `SWE-QA-Bench-LICENSE` in this
  directory for the full license text, copied unmodified from the source
  repository as required for redistribution.

Each line is `{"question": "...", "answer": "..."}` — a human-written
question about a real Python project and a human-written reference answer,
tied to a pinned commit of that project. `tests/swe_qa_ragas_eval.py`
clones the project at that exact commit before indexing, so the indexed
code matches what the reference answers were written against.

| Bundled file     | Project                          | Pinned commit |
|-------------------|-----------------------------------|----------------|
| `flask.jsonl`     | https://github.com/pallets/flask  | `85c5d93`      |
| `requests.jsonl`  | https://github.com/psf/requests   | `46e939b`      |

## Adding more projects

The original benchmark covers 12–15 Python projects (Django, SymPy,
scikit-learn, matplotlib, pytest, Sphinx, SQLFluff, astropy, pylint,
xarray, conan, reflex, streamlink). To evaluate against any of them:

1. Download the matching `.jsonl` from
   https://github.com/peng-weihan/SWE-QA-Bench/tree/main/Benchmark
2. Drop it into this directory
3. Add its `(github_url, pinned_commit)` to `REPO_COMMITS` in
   `tests/swe_qa_ragas_eval.py` — the pinned commits for every project are
   listed in `repo_commit.txt` in the source repository
4. Run `python tests/swe_qa_ragas_eval.py --repo <name>`

We only bundle Flask and Requests directly in this repo to keep its size
small; both are small, dependency-light projects that index in well under
a minute, making them good defaults for a first run.
