# Contributing to Clio Agent 1

Thank you for your interest in contributing! This guide will help you get started.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Code Style](#code-style)
- [Pull Request Process](#pull-request-process)
- [Release Process](#release-process)

## Code of Conduct

This project adheres to the [Contributor Covenant Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

## Getting Started

### Prerequisites

- Python 3.8+
- Git
- (Optional) Docker for container testing

### Fork & Clone

```bash
git clone https://github.com/your-username/Clio-Agent-1.git
cd Clio-Agent-1
git remote add upstream https://github.com/clio-project/Clio-Agent-1.git
```

## Development Setup

### Quick Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in development mode with all extras
pip install -e ".[dev,all]"

# Install pre-commit hooks
pre-commit install

# Verify installation
Clio-Agent --health-check
pytest tests/ -v
```

### Using the Install Script

```bash
bash install.sh
```

### Using Make

```bash
make install    # Install development dependencies
make test       # Run tests
make check      # Run all checks (format, lint, typecheck)
```

## Making Changes

### Branch Naming

| Type | Prefix | Example |
|------|--------|---------|
| Feature | `feature/` | `feature/add-groq-support` |
| Bug Fix | `fix/` | `fix/permission-denied-handling` |
| Documentation | `docs/` | `docs/update-readme` |
| Refactor | `refactor/` | `refactor/executor-error-handling` |
| Test | `test/` | `test/add-loop-controller-tests` |

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Examples:
```
feat(provider): add support for Groq API
fix(executor): handle sudo prompt in non-interactive mode
docs(readme): update installation instructions
test(core): add tests for context compression
```

## Code Style

### Formatting & Linting

```bash
# Format code
black src tests
isort src tests

# Check formatting
black --check src tests

# Lint
flake8 src tests
ruff check src tests

# Type check
mypy src
```

### Configuration

| Tool | Config |
|------|--------|
| Black | Line length 88, target Python 3.8+ |
| isort | Profile: black, line length 88 |
| flake8 | Max line length 88, ignore E203, W503 |
| mypy | Strict mode enabled |
| ruff | Select: E, W, F, I, B, C4, UP, PT, PIE, TID, ARG |

### Python Guidelines

- Type hints for all public functions
- Docstrings for modules, classes, and public methods (Google style)
- Max line length: 88 characters
- Use `pathlib` over `os.path`
- Prefer `async`/`await` for I/O operations
- Use `structlog` for logging

## Testing

### Running Tests

```bash
# All tests
pytest tests/ -v

# With coverage
pytest tests/ --cov=src/ai_agent --cov-report=html

# Specific test file
pytest tests/test_autonomous_loop_engine.py -v

# By marker
pytest tests/ -m "unit" -v
pytest tests/ -m "integration" -v
```

### Test Markers

| Marker | Description |
|--------|-------------|
| `unit` | Fast unit tests |
| `integration` | Integration tests |
| `e2e` | End-to-end tests |
| `slow` | Slow-running tests |

### Writing Tests

- Place tests in `tests/` mirroring `src/` structure
- Name test files: `test_<module>.py` or `<module>_test.py`
- Use `pytest` fixtures for common setup
- Mock external dependencies (APIs, filesystem, network)
- Aim for meaningful coverage, not just line coverage

## Pull Request Process

### Before Submitting

1. **Sync with upstream**:
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Run all checks**:
   ```bash
   make check
   # or:
   black src tests
   isort src tests
   flake8 src tests
   mypy src
   pytest tests/ -v
   ```

3. **Update documentation** if needed

4. **Add tests** for new functionality

### PR Requirements

- [ ] Descriptive title following conventional commits
- [ ] Clear description of changes
- [ ] Links to related issues
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No merge conflicts with `main`
- [ ] All CI checks passing

### Review Process

1. Automated checks must pass (CI)
2. At least one maintainer approval
3. All conversations resolved
4. Branch up to date with `main`

## Release Process

### Versioning

Follows [Semantic Versioning](https://semver.org/):
- `MAJOR` - Breaking changes
- `MINOR` - New features (backward compatible)
- `PATCH` - Bug fixes (backward compatible)

### Release Steps (Maintainers)

1. Update version in `pyproject.toml`
2. Update `CHANGELOG.md`
3. Create release tag: `git tag vX.Y.Z`
4. Push tag: `git push origin vX.Y.Z`
5. GitHub Actions builds and publishes to PyPI
6. Docker images pushed to GHCR

## Project Structure

```
Clio-Agent/
├── run.py                    # Main entry point
├── agent_core.py             # Backward compat wrapper
├── src/clio_agent.py         # Console script entry
├── src/ai_agent/             # Main package
│   ├── core_processing/      # Agent loop engine
│   ├── tools/                # Agent tools
│   ├── sub_agents/           # Sub-agent system
│   └── utils/                # Shared utilities
├── external_integration/     # External services
├── peripherals/              # Peripheral tools
├── docker/                   # Docker configurations
├── tests/                    # Test suite
├── docs/                     # Documentation
└── examples/                 # Usage examples
```

## Getting Help

- Open a [Question](https://github.com/clio-project/Clio-Agent-1/issues/new?template=question.md) issue
- Join [Discussions](https://github.com/clio-project/Clio-Agent-1/discussions)
- Check [Documentation](docs/)

## Recognition

Contributors are recognized in:
- GitHub Contributors graph
- Release notes
- `AUTHORS.md` (if maintained)

Thank you for contributing! 🎉