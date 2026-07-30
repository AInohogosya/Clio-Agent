# Contributing to Clio-Agent-2

Thank you for your interest in contributing! This document outlines the process for contributing to this project.

## Code of Conduct

By participating, you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## How to Contribute

### Reporting Bugs

1. Check [existing issues](https://github.com/yourusername/Clio-Agent-2/issues) first
2. Create a new issue using the **Bug Report** template
3. Include:
   - Clear, descriptive title
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment (OS, Python version, provider)
   - Logs/error messages (sanitize API keys!)

### Suggesting Features

1. Check existing issues and discussions
2. Create a new issue using the **Feature Request** template
3. Explain the use case and proposed solution

### Pull Requests

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature-name`
3. Make your changes
4. Run tests and linting (see below)
5. Commit with clear messages
6. Push to your fork
7. Open a PR against `main`

## Development Setup

```bash
# Clone your fork
git clone https://github.com/yourusername/Clio-Agent-2.git
cd Clio-Agent-2

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r clio_agent_2/requirements.txt
pip install pytest pytest-asyncio pytest-cov ruff mypy bandit

# Run tests
pytest tests/ -v

# Run linting
ruff check clio_agent_2/ tests/
ruff format --check clio_agent_2/ tests/

# Run type checking
mypy clio_agent_2/
```

## Coding Standards

- **Python 3.8+** compatible
- **Type hints** for new functions (mypy strict mode for `clio_agent_2.*`)
- **Ruff** for linting and formatting (line length: 100)
- **Docstrings** for public APIs (Google style)
- **Tests** for new features and bug fixes

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add new web search provider
fix: handle timeout in shell_command retry
docs: update configuration reference
refactor: extract context compression logic
test: add test for autonomous mode backoff
chore: update dependencies
```

## Project Structure

```
Clio-Agent-2/
├── run.py                      # Root launcher (single command)
├── pyproject.toml              # Modern Python packaging
├── clio_agent_2/
│   ├── main.py                 # Real entry point with auto-setup
│   ├── requirements.txt        # Dependencies
│   ├── config/                 # Configuration management
│   ├── core/                   # Core agent logic
│   │   ├── agent.py           # Main agent + autonomous loop
│   │   ├── context_manager.py # Context log + compression
│   │   ├── llm_router.py      # Multi-provider routing
│   │   └── retry.py           # Retry with backoff
│   ├── interfaces/            # CLI, Telegram, Discord, WhatsApp
│   ├── tools/                 # Tool implementations
│   ├── meta_controller.py     # Stuck detection watchdog
│   └── utils/                 # Utilities
├── tests/                     # Test suite
└── docs/                      # Documentation
```

## Adding Features

### Adding a New Tool

1. Add the tool function in `clio_agent_2/tools/tool_registry.py`
2. Register it in `TOOL_REGISTRY`
3. Add tests in `tests/`
4. Document in `docs/tools/`

### Adding a New LLM Provider

1. Add provider config in `clio_agent_2/core/llm_router.py`
2. Implement the provider class
3. Add to `SUPPORTED_PROVIDERS`
4. Update documentation in `docs/configuration/providers.md`
5. Add tests

### Adding a New Interface

1. Create `clio_agent_2/interfaces/your_interface.py`
2. Implement the interface protocol
3. Register in `clio_agent_2/main.py`
4. Add tests and documentation

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=clio_agent_2 --cov-report=html

# Run specific test file
pytest tests/test_agent_robustness.py -v

# Run tests matching pattern
pytest tests/ -k "telegram" -v
```

## Documentation

Documentation lives in `docs/`. Update relevant files when changing features:

- Architecture: `docs/architecture/`
- Configuration: `docs/configuration/`
- Usage: `docs/usage/`
- Development: `docs/development/`

## Release Process

Maintainers only:

1. Update `CHANGELOG.md`
2. Bump version in `pyproject.toml`
3. Create release tag: `git tag vX.Y.Z`
4. Push tag: `git push origin vX.Y.Z`
5. GitHub Actions builds and publishes

## Getting Help

- Open a **Discussion** for questions
- Check [Troubleshooting](docs/usage/troubleshooting.md)
- Join our community (links in README)

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).