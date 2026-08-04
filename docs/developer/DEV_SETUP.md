# Development Setup

Set up a local development environment for contributing to Clio-Agent-2.

---

## 📋 Prerequisites

- **Python 3.10+** (3.12 recommended for development)
- **Git**
- **Node.js** (only if working on WhatsApp interface)

---

## 🔀 Clone and Install

```bash
git clone https://github.com/your-org/Clio-Agent-2.git
cd Clio-Agent-2

python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# Install with dev dependencies
pip install -r clio_agent_2/requirements.txt
pip install -e ".[dev]"
```

The `[dev]` extras include: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `bandit`, `pip-audit`.

---

## 🧪 Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=clio_agent_2 --cov-report=html

# Run a specific test file
pytest tests/test_agent.py

# Run in parallel (fast)
pytest -n auto
```

> Tests live in the `tests/` directory. There are 50+ test files covering every module.

---

## 🔍 Linting and Type Checking

```bash
# Lint with Ruff
ruff check clio_agent_2/

# Auto-fix linting issues
ruff check clio_agent_2/ --fix

# Format code
ruff format clio_agent_2/

# Type check with mypy
mypy clio_agent_2/
```

---

## 🔐 Security Scanning

```bash
# Bandit static analysis
bandit -r clio_agent_2/

# Dependency vulnerability scan
pip-audit
```

---

## 🚀 Running the Agent Locally

```bash
# Set up a dummy configuration for local testing
python3 run.py setup --help  # see all flags

# Or edit config manually
# Then launch
python3 run.py

# Auto-setup is skipped if config is already valid:
python3 run.py --no-setup
```

---

## 🏗️ Project Conventions

### Code Style

- **Formatter:** `ruff format` (uses Black-compatible style)
- **Linter:** `ruff check` (replaces flake8/pylint)
- **Type hints:** Full type annotations expected; checked by `mypy`
- **Line length:** 100 characters (ruff default)

### Docstrings

Every public class, function, and method has docstrings. Use Google style:

```python
def foo(bar: str, *, count: int = 1) -> List[str]:
    """Do the thing.

    Args:
        bar: The thing to bar.
        count: How many times. Defaults to 1.

    Returns:
        List of results.
    """
```

### Imports

- Standard library first
- Third-party packages second
- Local/project packages last
- No wildcard imports

### Error Handling

- Never silently swallow exceptions
- Add system messages to context log on failure
- Use the `_is_real_secret()` pattern for token detection

---

## 📝 Making Changes

1. Create a feature branch:
   ```bash
   git checkout -b feature/my-feature
   ```

2. Make your changes, following the conventions above.

3. Run the full lint/test/security suite:
   ```bash
   ruff check clio_agent_2/ && ruff format clio_agent_2/ && \
   mypy clio_agent_2/ && \
   pytest && \
   bandit -r clio_agent_2/
   ```

4. Commit with a clear message:
   ```
   feat(core): add circuit-breaker alert to Telegram
   fix(tools): handle empty web_search response
   docs: update configuration reference
   ```

5. Open a PR against the main branch.

---

## 📦 Building a Distribution

```bash
python -m pip install --upgrade build
python -m build          # creates dist/*.whl + dist/*.tar.gz
twine check dist/*
twine upload dist/*      # for releases (requires twine credentials)
```

---

## 🧭 Related Docs

- [Contributing Guide](CONTRIBUTING.md) — PR process, code conventions
- [Testing Guide](TESTING.md) — writing and running tests
- [Architecture Overview](ARCHITECTURE.md) — system design
