"""Unit tests for AST mapper and context retriever."""

from __future__ import annotations

from pathlib import Path

import pytest

from neuro_scaffold.config.settings import Settings
from neuro_scaffold.context.ast_mapper import ASTMapper
from neuro_scaffold.context.retriever import ContextRetriever


class TestASTMapper:
    def test_scan_python_project(self, sample_project: Path) -> None:
        mapper = ASTMapper(str(sample_project))
        ast_map = mapper.scan()
        assert ast_map.total_files >= 2
        assert "python" in ast_map.language_stats
        assert ast_map.total_symbols > 0

    def test_scan_caches_results(self, sample_project: Path) -> None:
        mapper = ASTMapper(str(sample_project))
        map1 = mapper.scan()
        map2 = mapper.scan()
        assert map1.scanned_at == map2.scanned_at

    def test_scan_force_rescan(self, sample_project: Path) -> None:
        mapper = ASTMapper(str(sample_project))
        map1 = mapper.scan()
        map2 = mapper.scan(force=True)
        assert map2.scanned_at >= map1.scanned_at

    def test_file_index_populated(self, sample_project: Path) -> None:
        mapper = ASTMapper(str(sample_project))
        ast_map = mapper.scan()
        assert len(ast_map.file_index) >= 2
        for file_path, symbols in ast_map.file_index.items():
            assert len(symbols) > 0

    def test_parse_python_functions(self, tmp_path: Path) -> None:
        file_path = tmp_path / "test.py"
        file_path.write_text(
            "def add(a: int, b: int) -> int:\n"
            '    """Add two numbers."""\n'
            "    return a + b\n"
            "\n"
            "class Calculator:\n"
            "    def multiply(self, x: int, y: int) -> int:\n"
            "        return x * y\n"
        )
        mapper = ASTMapper(str(tmp_path))
        ast_map = mapper.scan()
        symbol_names = [s.name for s in ast_map.symbols.values()]
        assert "add" in symbol_names
        assert "Calculator" in symbol_names

    def test_parse_python_with_syntax_error(self, tmp_path: Path) -> None:
        file_path = tmp_path / "broken.py"
        file_path.write_text("def broken(\n")
        mapper = ASTMapper(str(tmp_path))
        ast_map = mapper.scan()
        assert ast_map.total_files == 1

    def test_invalidate_cache(self, sample_project: Path) -> None:
        mapper = ASTMapper(str(sample_project))
        mapper.scan()
        mapper.invalidate_cache()
        assert mapper._cache is None

    def test_ignore_directories(self, tmp_path: Path) -> None:
        (tmp_path / "valid.py").write_text("x = 1\n")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "test.py").write_text("y = 2\n")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "test.py").write_text("z = 3\n")
        mapper = ASTMapper(str(tmp_path))
        ast_map = mapper.scan()
        assert ast_map.total_files == 1

    def test_parse_javascript(self, tmp_path: Path) -> None:
        file_path = tmp_path / "app.js"
        file_path.write_text(
            "export function greet(name) {\n"
            '    return `Hello, ${name}!`;\n'
            "}\n"
            "\n"
            "export class Greeter {\n"
            "    constructor(greeting) {\n"
            "        this.greeting = greeting;\n"
            "    }\n"
            "}\n"
        )
        mapper = ASTMapper(str(tmp_path))
        ast_map = mapper.scan()
        symbol_names = [s.name for s in ast_map.symbols.values()]
        assert "greet" in symbol_names
        assert "Greeter" in symbol_names


class TestContextRetriever:
    @pytest.fixture
    def retriever(self, sample_project: Path) -> ContextRetriever:
        mapper = ASTMapper(str(sample_project))
        ast_map = mapper.scan()
        settings = Settings(context_max_tokens=4000)
        return ContextRetriever(ast_map, settings)

    def test_query_by_symbol_name(self, retriever: ContextRetriever) -> None:
        result = retriever.query("helper")
        assert len(result.chunks) > 0
        assert any("helper" in c.content for c in result.chunks)

    def test_query_by_file(self, retriever: ContextRetriever) -> None:
        result = retriever.query("utils.py")
        assert len(result.chunks) > 0
        assert any("utils.py" in c.file_path for c in result.chunks)

    def test_query_wildcard_overview(self, retriever: ContextRetriever) -> None:
        result = retriever.query("*")
        assert len(result.chunks) >= 2

    def test_query_by_content(self, retriever: ContextRetriever) -> None:
        result = retriever.query("name: str")
        assert len(result.chunks) > 0
        assert result.total_tokens > 0

    def test_query_nonexistent_symbol(self, retriever: ContextRetriever) -> None:
        result = retriever.query("nonexistent_function")
        assert len(result.chunks) == 0

    def test_token_limit_respected(self, sample_project: Path) -> None:
        mapper = ASTMapper(str(sample_project))
        ast_map = mapper.scan()
        settings = Settings(context_max_tokens=1000)
        retriever = ContextRetriever(ast_map, settings)
        result = retriever.query("*")
        assert result.total_tokens <= 1000

    def test_relevance_scores(self, retriever: ContextRetriever) -> None:
        result = retriever.query("helper")
        for chunk in result.chunks:
            assert 0.0 <= chunk.relevance_score <= 1.0

    def test_duration_tracked(self, retriever: ContextRetriever) -> None:
        result = retriever.query("*")
        assert result.duration_ms >= 0
