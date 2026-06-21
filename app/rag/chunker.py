"""
CodeChunker: converts CodeUnits into LangChain Documents with rich metadata.
Each Document represents one semantic code unit (function / class / block).
"""
from typing import List

from langchain_core.documents import Document

from app.rag.parser import CodeParser


class CodeChunker:
    def __init__(self):
        self.parser = CodeParser()

    def chunk_code(self, code: str, file_path: str) -> List[Document]:
        units = self.parser.parse(code, file_path)
        docs = []
        for unit in units:
            docs.append(Document(
                page_content=f"# {file_path}  [{unit.kind}: {unit.name}]\n\n{unit.source}",
                metadata={
                    "file": file_path,
                    "name": unit.name,
                    "kind": unit.kind,
                    "start_line": unit.start_line,
                    "end_line": unit.end_line,
                }
            ))
        return docs
