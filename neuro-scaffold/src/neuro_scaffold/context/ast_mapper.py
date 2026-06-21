"""AST-based code skeleton mapper for targeted context retrieval."""

from __future__ import annotations

import ast as python_ast
import os
import re
import time
from pathlib import Path
from typing import Any

import structlog

try:
    import tree_sitter_python as tspython
    from tree_sitter import Language, Parser
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False

from neuro_scaffold.agent.models import ASTMap, Symbol, SymbolType

logger = structlog.get_logger(__name__)


class ASTMapper:
    """Parses a directory into an AST symbol map for targeted retrieval."""

    EXTENSION_LANGUAGES = {
        ".py": "python",
        ".js": "javascript",
        ".mjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".rs": "rust",
        ".go": "go",
        ".java": "java",
        ".rb": "ruby",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
    }

    def __init__(self, root_path: str) -> None:
        self._root = Path(root_path).resolve()
        self._cache: ASTMap | None = None

    def scan(self, force: bool = False) -> ASTMap:
        """Scan the directory and build the AST map."""
        if self._cache is not None and not force:
            return self._cache

        start = time.monotonic()
        ast_map = ASTMap(root_path=str(self._root))

        for dirpath, dirnames, filenames in os.walk(self._root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", "node_modules", ".tox", "venv", ".venv")]

            for filename in filenames:
                file_path = Path(dirpath) / filename
                ext = file_path.suffix.lower()
                language = self.EXTENSION_LANGUAGES.get(ext)

                if language is None:
                    continue

                rel_path = str(file_path.relative_to(self._root))
                ast_map.language_stats[language] = ast_map.language_stats.get(language, 0) + 1
                ast_map.total_files += 1

                symbols = self._parse_file(file_path, language)
                file_symbol_names: list[str] = []
                for sym in symbols:
                    key = f"{rel_path}::{sym.name}"
                    ast_map.symbols[key] = sym
                    file_symbol_names.append(sym.name)
                    ast_map.total_symbols += 1

                ast_map.file_index[rel_path] = file_symbol_names

        duration = time.monotonic() - start
        logger.info(
            "AST scan complete",
            files=ast_map.total_files,
            symbols=ast_map.total_symbols,
            duration_s=round(duration, 3),
        )
        self._cache = ast_map
        return ast_map

    def _parse_file(self, file_path: Path, language: str) -> list[Symbol]:
        """Parse a single file and extract symbols."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        rel_path = str(file_path.relative_to(self._root))

        if language == "python":
            return self._parse_python(content, rel_path)

        return self._parse_generic(content, rel_path, language)

    def _parse_python(self, content: str, file_path: str) -> list[Symbol]:
        """Parse Python code using the ast module."""
        symbols: list[Symbol] = []
        try:
            tree = python_ast.parse(content)
        except SyntaxError:
            return self._parse_generic(content, file_path, "python")

        for node in python_ast.walk(tree):
            if isinstance(node, python_ast.FunctionDef):
                sig = self._python_func_signature(node, content)
                docstring = python_ast.get_docstring(node)
                symbols.append(
                    Symbol(
                        name=node.name,
                        symbol_type=SymbolType.FUNCTION,
                        file_path=file_path,
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        column_start=node.col_offset,
                        column_end=node.end_col_offset or node.col_offset,
                        signature=sig,
                        docstring=docstring,
                        language="python",
                    )
                )
            elif isinstance(node, python_ast.ClassDef):
                docstring = python_ast.get_docstring(node)
                symbols.append(
                    Symbol(
                        name=node.name,
                        symbol_type=SymbolType.CLASS,
                        file_path=file_path,
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        column_start=node.col_offset,
                        column_end=node.end_col_offset or node.col_offset,
                        docstring=docstring,
                        language="python",
                    )
                )
            elif isinstance(node, python_ast.Import) or isinstance(node, python_ast.ImportFrom):
                names = []
                for alias in node.names:
                    names.append(alias.asname or alias.name)
                symbols.append(
                    Symbol(
                        name=", ".join(names),
                        symbol_type=SymbolType.IMPORT,
                        file_path=file_path,
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        language="python",
                    )
                )

        return symbols

    def _python_func_signature(self, node: python_ast.FunctionDef, content: str) -> str:
        """Extract function signature from AST node."""
        args = []
        for arg in node.args.args:
            name = arg.arg
            annotation = ""
            if arg.annotation:
                try:
                    annotation = python_ast.unparse(arg.annotation)
                except Exception:
                    annotation = "..."
            if annotation:
                args.append(f"{name}: {annotation}")
            else:
                args.append(name)

        returns = ""
        if node.returns:
            try:
                returns = f" -> {python_ast.unparse(node.returns)}"
            except Exception:
                returns = " -> ..."

        return f"def {node.name}({', '.join(args)}){returns}"

    def _parse_generic(self, content: str, file_path: str, language: str) -> list[Symbol]:
        """Generic regex-based symbol extraction for non-Python languages."""
        symbols: list[Symbol] = []
        lines = content.splitlines()

        patterns = {
            "javascript": [
                (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", SymbolType.FUNCTION),
                (r"^(?:export\s+)?class\s+(\w+)", SymbolType.CLASS),
                (r"^(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(?.*\)?\s*=>", SymbolType.FUNCTION),
            ],
            "typescript": [
                (r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", SymbolType.FUNCTION),
                (r"^(?:export\s+)?class\s+(\w+)", SymbolType.CLASS),
                (r"^(?:export\s+)?interface\s+(\w+)", SymbolType.INTERFACE),
                (r"^(?:export\s+)?type\s+(\w+)", SymbolType.TYPE_ALIAS),
            ],
            "rust": [
                (r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", SymbolType.FUNCTION),
                (r"^(?:pub\s+)?struct\s+(\w+)", SymbolType.CLASS),
                (r"^(?:pub\s+)?trait\s+(\w+)", SymbolType.INTERFACE),
                (r"^(?:pub\s+)?enum\s+(\w+)", SymbolType.CLASS),
            ],
            "go": [
                (r"^func\s+(?:\([^)]+\)\s+)?(\w+)", SymbolType.FUNCTION),
                (r"^type\s+(\w+)\s+struct", SymbolType.CLASS),
                (r"^type\s+(\w+)\s+interface", SymbolType.INTERFACE),
            ],
            "java": [
                (r"^(?:public|private|protected)?\s+(?:static\s+)?(?:class|interface)\s+(\w+)", SymbolType.CLASS),
                (r"^(?:public|private|protected)?\s+(?:static\s+)?[\w<>\[\]]+\s+(\w+)\s*\(", SymbolType.METHOD),
            ],
        }

        lang_patterns = patterns.get(language, [])
        for i, line in enumerate(lines, 1):
            for pattern, sym_type in lang_patterns:
                match = re.match(pattern, line.strip())
                if match:
                    symbols.append(
                        Symbol(
                            name=match.group(1),
                            symbol_type=sym_type,
                            file_path=file_path,
                            line_start=i,
                            line_end=i,
                            language=language,
                        )
                    )
                    break

        return symbols

    def invalidate_cache(self) -> None:
        self._cache = None
