# Contributing to Clio Agent 1

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

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
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install in development mode with all extras
pip install -e ".[dev,all]"

# Verify installation
Clio-Agent --health-check
pytest tests/ -v
```

### Using the Install Script
```bash
bash install.sh
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

# Check formatting
black --check src tests

# Lint
flake8 src tests

# Type check
mypy src
```

### Configuration
- **Black**: Line length 88, target Python 3.8+
- **flake8**: Max line length 88, ignore E203, W503
- **mypy**: Strict mode enabled

### Python Guidelines
- Type hints for all public functions
- Docstrings for modules, classes, and public methods (Google style)
- Max line length: 88 characters
- Use `pathlib` over `os.path`
- Prefer `async`/`await` for I/O operations

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

## Pull Request Process

### Before Submitting
1. Run all tests: `pytest tests/ -v`
2. Run linting: `black --check src tests && flake8 src tests && mypy src`
3. Update documentation if needed
4. Add tests for new functionality
5. Ensure no new warnings

### PR Requirements
- [ ] Descriptive title following conventional commits
- [ ] Clear description of changes
- [ ] Links to related issues
- [ ] Tests added/updated
- [ ] Documentation updated
- [ ] No merge conflicts with `main`

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

## Getting Help
- Open a [Question](https://github.com/clio-project/Clio-Agent-1/issues/new?template=question.md) issue
- Join [Discussions](https://github.com/clio-project/Clio-Agent-1/discussions)
- Check [Documentation](docs/)

## Recognition
Contributors are recognized in:
- `CONTRIBUTORS.md` (auto-generated)
- Release notes
- GitHub contributor graph

Thank you for contributing! 🎉