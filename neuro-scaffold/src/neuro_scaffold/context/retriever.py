"""Targeted context retrieval engine for feeding relevant code to the LLM."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import structlog

from neuro_scaffold.agent.models import ASTMap, ContextChunk, ContextRetrievalResult, Symbol
from neuro_scaffold.config.settings import Settings

logger = structlog.get_logger(__name__)


class ContextRetriever:
    """Retrieves targeted code context from the AST map."""

    def __init__(self, ast_map: ASTMap, settings: Settings) -> None:
        self._ast_map = ast_map
        self._settings = settings
        self._root = Path(ast_map.root_path)

    def query(self, query: str, max_tokens: int | None = None) -> ContextRetrievalResult:
        """Retrieve relevant code chunks for a query.

        Supports:
          - Symbol lookup: "function_name" or "ClassName"
          - File lookup: "path/to/file.py"
          - Wildcard: "*" returns top-level symbols
          - Content search: any other string does substring matching
        """
        max_tokens = max_tokens or self._settings.context_max_tokens
        start = time.monotonic()

        if query.strip() == "*":
            chunks = self._get_overview(max_tokens)
        elif query.endswith((".py", ".js", ".ts", ".json", ".rs", ".go", ".java", ".rb", ".c", ".cpp", ".h")) or "/" in query or "\\" in query:
            chunks = self._get_by_file(query, max_tokens)
        elif "::" in query:
            chunks = self._get_by_symbol(query, max_tokens)
        elif query[0].isupper() and query.replace("_", "").isalnum():
            chunks = self._get_by_symbol_name(query, max_tokens)
        elif query.isidentifier() and len(query) <= 64 and not query[0].isupper():
            chunks = self._get_by_symbol_name(query, max_tokens)
        else:
            chunks = self._get_by_content(query, max_tokens)

        total_tokens = sum(c.token_estimate for c in chunks)
        duration_ms = (time.monotonic() - start) * 1000

        return ContextRetrievalResult(
            chunks=chunks,
            total_tokens=total_tokens,
            query=query,
            duration_ms=duration_ms,
        )

    def _get_overview(self, max_tokens: int) -> list[ContextChunk]:
        """Get a high-level overview of the codebase."""
        chunks: list[ContextChunk] = []
        tokens_used = 0

        for file_path, symbol_names in sorted(self._ast_map.file_index.items()):
            if tokens_used >= max_tokens:
                break
            full_path = str(self._root / file_path)
            try:
                content = Path(full_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            lines = content.splitlines()
            overview_lines = [f"# {file_path} ({len(lines)} lines, {len(symbol_names)} symbols)"]
            for sym_key, sym in self._ast_map.symbols.items():
                if sym.file_path == file_path and sym.signature:
                    overview_lines.append(f"  {sym.signature}")
                elif sym.file_path == file_path:
                    overview_lines.append(f"  {sym.symbol_type.value}: {sym.name}")

            overview = "\n".join(overview_lines)
            token_est = len(overview) // 4
            if tokens_used + token_est > max_tokens:
                break

            chunks.append(
                ContextChunk(
                    file_path=file_path,
                    start_line=1,
                    end_line=len(lines),
                    content=overview,
                    symbols=symbol_names,
                    relevance_score=1.0,
                    token_estimate=token_est,
                )
            )
            tokens_used += token_est

        return chunks

    def _get_by_symbol(self, query: str, max_tokens: int) -> list[ContextChunk]:
        """Get context for a specific symbol (file::name)."""
        sym = self._ast_map.symbols.get(query)
        if sym is None:
            return []
        return self._extract_symbol_context(sym, max_tokens)

    def _get_by_symbol_name(self, name: str, max_tokens: int) -> list[ContextChunk]:
        """Search for symbols by name."""
        chunks: list[ContextChunk] = []
        tokens_used = 0

        for sym_key, sym in self._ast_map.symbols.items():
            if sym.name == name or sym.name.endswith(f".{name}"):
                sym_chunks = self._extract_symbol_context(sym, max_tokens - tokens_used)
                for chunk in sym_chunks:
                    tokens_used += chunk.token_estimate
                chunks.extend(sym_chunks)
                if tokens_used >= max_tokens:
                    break

        return chunks

    def _get_by_file(self, file_query: str, max_tokens: int) -> list[ContextChunk]:
        """Get context from a specific file."""
        full_path = str(self._root / file_query)
        path = Path(full_path)

        if not path.exists() or not path.is_file():
            for fp in self._ast_map.file_index:
                if file_query in fp:
                    full_path = str(self._root / fp)
                    path = Path(full_path)
                    break

        if not path.exists() or not path.is_file():
            return []

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        lines = content.splitlines()
        overlap = min(self._settings.context_chunk_overlap, 50)
        chunk_size = 200
        step = max(chunk_size - overlap, 1)
        chunks: list[ContextChunk] = []
        tokens_used = 0

        for start in range(0, len(lines), step):
            end = min(start + chunk_size, len(lines))
            chunk_content = "\n".join(lines[start:end])
            token_est = len(chunk_content) // 4

            if tokens_used + token_est > max_tokens:
                remaining = max_tokens - tokens_used
                if remaining > 100:
                    chunk_content = chunk_content[:remaining * 4]
                    token_est = remaining
                else:
                    break

            rel_path = str(path.relative_to(self._root))
            chunks.append(
                ContextChunk(
                    file_path=rel_path,
                    start_line=start + 1,
                    end_line=end,
                    content=chunk_content,
                    symbols=self._ast_map.file_index.get(rel_path, []),
                    relevance_score=1.0,
                    token_estimate=token_est,
                )
            )
            tokens_used += token_est

        return chunks

    def _get_by_content(self, search: str, max_tokens: int) -> list[ContextChunk]:
        """Search for content across all files."""
        chunks: list[ContextChunk] = []
        tokens_used = 0
        pattern = re.compile(re.escape(search), re.IGNORECASE)

        for file_path in self._ast_map.file_index:
            if tokens_used >= max_tokens:
                break

            full_path = str(self._root / file_path)
            try:
                content = Path(full_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            lines = content.splitlines()
            matching_lines: list[tuple[int, str]] = []
            for i, line in enumerate(lines, 1):
                if pattern.search(line):
                    matching_lines.append((i, line))

            if not matching_lines:
                continue

            context_lines: list[str] = []
            shown: set[int] = set()
            for line_num, _ in matching_lines:
                start = max(1, line_num - 3)
                end = min(len(lines), line_num + 3)
                for ln in range(start, end + 1):
                    if ln not in shown:
                        context_lines.append(f"{ln:4d}: {lines[ln - 1]}")
                        shown.add(ln)

            chunk_content = "\n".join(context_lines)
            token_est = len(chunk_content) // 4

            if tokens_used + token_est > max_tokens:
                remaining = max_tokens - tokens_used
                if remaining > 100:
                    chunk_content = chunk_content[:remaining * 4]
                    token_est = remaining
                else:
                    break

            chunks.append(
                ContextChunk(
                    file_path=file_path,
                    start_line=matching_lines[0][0],
                    end_line=matching_lines[-1][0],
                    content=chunk_content,
                    symbols=self._ast_map.file_index.get(file_path, []),
                    relevance_score=0.8,
                    token_estimate=token_est,
                )
            )
            tokens_used += token_est

        return chunks

    def _extract_symbol_context(self, sym: Symbol, max_tokens: int) -> list[ContextChunk]:
        """Extract the source code for a specific symbol."""
        full_path = str(self._root / sym.file_path)
        try:
            content = Path(full_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        lines = content.splitlines()
        start = max(0, sym.line_start - 1)
        end = min(len(lines), sym.line_end)

        if end - start > 200:
            end = start + 200

        chunk_content = "\n".join(lines[start:end])
        token_est = len(chunk_content) // 4

        if token_est > max_tokens:
            chunk_content = chunk_content[:max_tokens * 4]
            token_est = max_tokens

        return [
            ContextChunk(
                file_path=sym.file_path,
                start_line=start + 1,
                end_line=end,
                content=chunk_content,
                symbols=[sym.name],
                relevance_score=1.0,
                token_estimate=token_est,
            )
        ]
