"""
AST-based code parser using tree-sitter.
Extracts top-level functions and classes as meaningful semantic units.
Falls back to raw text if parsing fails.
"""
from dataclasses import dataclass
from typing import List

from app.config import config

try:
    from tree_sitter import Language, Parser as TSParser
    import tree_sitter_python as tspython

    PY_LANGUAGE = Language(tspython.language())
    _parser = TSParser(PY_LANGUAGE)  # FIX: pass language to constructor, not via attribute assignment
    TREE_SITTER_AVAILABLE = True
except Exception:
    TREE_SITTER_AVAILABLE = False


@dataclass
class CodeUnit:
    """A semantic unit of code: a function, class, or raw block."""
    name: str
    kind: str
    source: str
    start_line: int
    end_line: int


class CodeParser:
    """
    Parses Python source into semantic CodeUnits.

    Strategy:
    1. Try tree-sitter AST parsing → extract functions & classes by node boundaries
    2. If tree-sitter is unavailable or returns no nodes → fall back to line-based splitting
    """

    def parse(self, code: str, file_path: str = "") -> List[CodeUnit]:
        if TREE_SITTER_AVAILABLE:
            units = self._parse_with_ast(code)
            if units:
                return units
        return self._parse_fallback(code)

    def _parse_with_ast(self, code: str) -> List[CodeUnit]:
        """Use tree-sitter to extract top-level function and class definitions."""
        tree = _parser.parse(bytes(code, "utf8"))
        root = tree.root_node
        lines = code.splitlines()
        units: List[CodeUnit] = []

        for node in root.children:
            if node.type in ("function_definition", "class_definition"):
                kind = "function" if node.type == "function_definition" else "class"
                name_node = node.child_by_field_name("name")
                name = name_node.text.decode("utf8") if name_node else "unknown"
                start = node.start_point[0]
                end = node.end_point[0]
                source = "\n".join(lines[start:end + 1])
                units.append(CodeUnit(name=name, kind=kind, source=source,
                                      start_line=start + 1, end_line=end + 1))  # FIX: 1-based line numbers

        return units

    def _parse_fallback(self, code: str, chunk_lines: int | None = None) -> List[CodeUnit]:
        """Fallback: split code into fixed-size line blocks when AST parsing is unavailable."""
        chunk_lines = chunk_lines or config.CHUNK_SIZE_LINES
        lines = code.splitlines()
        units = []
        for i in range(0, len(lines), chunk_lines):
            block = lines[i:i + chunk_lines]
            units.append(CodeUnit(
                name=f"block_{i}",
                kind="block",
                source="\n".join(block),
                start_line=i + 1,      # FIX: 1-based line numbers
                end_line=min(i + chunk_lines, len(lines))  # FIX: 1-based, consistent end
            ))
        return units
