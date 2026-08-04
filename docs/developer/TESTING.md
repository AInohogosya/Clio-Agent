# Testing Guide

How to write and run tests for Clio-Agent-2.

---

## 🛠️ Test Stack

| Tool | Role |
|------|------|
| **pytest** | Test runner |
| **pytest-asyncio** | Running async tests |
| **pytest-cov** | Code coverage |
| **xdist** | Parallel test execution |

---

## 🚀 Running Tests

```bash
# All tests (from project root)
pytest

# Watch mode (requires pytest-watch)
ptw

# With coverage report
pytest --cov=clio_agent_2 --cov-report=term
pytest --cov=clio_agent_2 --cov-report=html  # opens htmlcov/index.html

# Specific file
pytest tests/test_agent.py

# Specific function
pytest tests/test_agent.py::TestClioAgent::test_process_message

# Run in parallel (speed up large test suites)
pytest -n auto

# Stop on first failure
pytest -x

# Verbose output
pytest -vv
```

---

## 📁 Test Directory Layout

```
tests/
├── conftest.py                     # Shared fixtures
├── test_agent.py
├── test_llm_router.py
├── test_context_manager.py
├── test_tool_registry.py
├── test_config.py
├── test_setup_env.py
├── test_retry.py
├── test_token_budget.py
├── test_instance_lock.py
├── test_cli_interface.py
├── test_telegram_interface.py
├── test_discord_interface.py
├── test_whatsapp_interface.py
├── test_tool_shell_command.py
├── test_tool_web_search.py
├── test_tool_file_operations.py
├── test_autonomous_loop.py
├── test_circuit_breaker.py
├── test_tool_parsing.py
├── test_context_compression.py
├── test_context_persistence.py
├── test_llm_settings_lock.py
├── test_response_sink.py
├── test_settings_persistence.py
├── test_argument_parsing.py
└── run_tool_parsing_check.py
```

---

## ✍️ Writing Tests

### Basic Test Structure

```python
import pytest
from clio_agent_2.core.agent import ClioAgent
from clio_agent_2.core.context_manager import ContextLog


class TestClioAgent:
    @pytest.fixture
    def agent(self):
        """Create a minimal ClioAgent for testing."""
        config = MockConfig()  # see conftest.py
        llm_router = MockLLMRouter()
        return ClioAgent(config, llm_router)

    def test_process_message_returns_empty(self, agent):
        result = asyncio.run(agent.process_message("hello"))
        assert result == ""
```

### Async Tests

```python
import pytest

@pytest.mark.asyncio
async def test_llm_call():
    router = MockLLMRouter()
    result = await router.chat([{"role": "user", "content": "hi"}])
    assert result == "mock response"
```

### Mocking LLM Calls

Never call real LLM APIs in tests. Always mock:

```python
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_with_mock_llm():
    mock_response = AsyncMock(return_value="mocked LLM response")
    with patch.object(llm_router, "chat", mock_response):
        result = await llm_router.chat(messages)
    assert result == "mocked LLM response"
```

---

## 🔧 Common Fixtures (`conftest.py`)

The shared `conftest.py` provides:

- `mock_config` — Minimal Config stub
- `mock_llm_router` — LLMRouter stub returning a fixed response
- `temp_context_path` — Temporary context file path
- `agent` — Fully-constructed ClioAgent with mocks

Use these fixtures instead of constructing objects manually.

---

## ⚡ Fast Test Tips

| Tip | Impact |
|-----|--------|
| Use `pytest -n auto` | Parallelizes (useful with many I/O-bound tests) |
| Skip slow tests in normal runs | Mark with `@pytest.mark.slow` and exclude with `-m "not slow"` |
| Use `tmp_path` fixture | Avoid disk I/O to real files |
| Mock `asyncio.sleep` | Avoid real waits in autonomous loop tests |

---

## 🧪 What to Test

| Layer | What to Test |
|-------|-------------|
| **Core (`core/`)** | Agent turns, context management, compression, LLM routing |
| **Tools (`tools/`)** | Each tool individually, edge cases, error handling |
| **Interfaces (`interfaces/`)** | Message routing, slash command dispatch, response formatting |
| **Config (`config/`)** | Loading, validation, placeholder detection, persistence |
| **Utils** | Lock acquisition / release, expiry logic |

---

## 📝 Test Naming

Tests should read like specifications:

```python
def test_context_compression_triggers_after_max_lines():
def test_llm_settings_lock_prevents_provider_change():
def test_shell_command_retries_on_timeout():
def test_autonomous_loop_trips_circuit_breaker_after_5_failures():
```

---

## 🧭 Related Docs

- [Development Setup](DEV_SETUP.md) — running tests locally
- [API Reference](API.md) — what to test against
- [Contributing Guide](CONTRIBUTING.md) — PR requirements
