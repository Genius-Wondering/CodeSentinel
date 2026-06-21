"""
This module defines the `RepoLoader` class, which acts as a data pipeline that transforms Python source files 
from a given repository into standardized Document objects suitable for AI models (e.g., LLMs).

Key behaviors:
- Recursively walks the repository directory.
- Skips directories that are irrelevant for code Q&A (e.g., `.git`, `__pycache__`, `venv`, `node_modules`, etc.).
- Only processes files with the `.py` extension.
- Skips files larger than `MAX_FILE_BYTES` (500 KB) to avoid oversized inputs.
- Uses `CodeChunker` to split the code into semantic chunks.
- Uses the file's relative path (from the repo root) as the document identifier for portability.
- Returns a list of LangChain `Document` objects.

The loader is intended to be used for populating a vector database with code snippets from a Python repository.
"""

import logging
import os
from typing import List

from langchain_core.documents import Document

from app.rag.chunker import CodeChunker

logger = logging.getLogger(__name__)

# Directories skipped during indexing
SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    "node_modules",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
    "site-packages",
}

# FIX: skip files that are too large to be meaningful for code Q&A
MAX_FILE_BYTES = 500_000


class RepoLoader:
    """Walk a repo, read .py files, and chunk into LangChain Documents."""

    def __init__(self):
        self.chunker = CodeChunker()

    def load_repo(self, repo_path: str) -> List[Document]:
        repo_path = os.path.abspath(repo_path)
        all_docs: List[Document] = []

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIR_NAMES]

            for file in files:
                if not file.endswith(".py"):
                    continue

                full_path = os.path.join(root, file)

                # FIX: skip oversized files before reading into memory
                try:
                    if os.path.getsize(full_path) > MAX_FILE_BYTES:
                        logger.debug("Skipping oversized file: %s", full_path)
                        continue
                except OSError as e:
                    logger.warning("Cannot stat file, skipping: %s — %s", full_path, e)
                    continue

                try:
                    with open(full_path, "r", encoding="utf-8") as f:
                        code = f.read()
                except UnicodeDecodeError as e:
                    logger.warning("Skipping non-UTF-8 file: %s — %s", full_path, e)
                    continue

                if not code.strip():
                    continue

                # FIX: use relative path as file identifier for portability
                rel_path = os.path.relpath(full_path, repo_path)
                docs = self.chunker.chunk_code(code, rel_path)
                all_docs.extend(docs)

        return all_docs
