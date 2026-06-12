# Contributing Guide

## Overview

Thank you for your interest in contributing to VEXIS-CLI! This guide covers everything you need to know to contribute effectively, from code standards to submission process.

## Getting Started

### Prerequisites

- **Python 3.9+**
- **Git**
- **GitHub account**
- **Development environment setup**

### First-Time Setup

1. **Fork the Repository**
   ```bash
   # Fork on GitHub, then clone your fork
   git clone https://github.com/YOUR_USERNAME/VEXIS-CLI.git
   cd VEXIS-CLI
   ```

2. **Add Upstream Remote**
   ```bash
   git remote add upstream https://github.com/AInohogosya/VEXIS-CLI.git
   ```

3. **Set Up Development Environment**
   ```bash
   # Create virtual environment
   python3 -m venv venv
   source venv/bin/activate  # Linux/macOS
   # or
   venv\Scripts\activate     # Windows
   
   # Install dependencies
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   pip install -e .
   
   # Set up pre-commit hooks
   pre-commit install
   ```

4. **Verify Setup**
   ```bash
   # Run tests
   pytest
   
   # Check code quality
   black --check src/ tests/
   flake8 src/ tests/
   mypy src/
   ```

## Development Workflow

### Branch Strategy

We use a simplified branch workflow:

- **main**: Stable production code
- **develop**: Integration branch (if needed)
- **feature/feature-name**: New features
- **bugfix/bug-description**: Bug fixes
- **hotfix/critical-fix**: Urgent fixes

### Creating a Feature Branch

```bash
# Sync with upstream
git fetch upstream
git checkout main
git merge upstream/main

# Create feature branch
git checkout -b feature/your-feature-name

# Start development
```

### Development Process

1. **Write Code**
   - Follow coding standards
   - Add tests for new functionality
   - Update documentation

2. **Test Changes**
   ```bash
   # Run unit tests
   pytest tests/unit/
   
   # Run integration tests
   pytest tests/integration/
   
   # Run all tests with coverage
   pytest --cov=src/ai_agent --cov-report=html
   ```

3. **Code Quality Checks**
   ```bash
   # Format code
   black src/ tests/
   
   # Sort imports
   isort src/ tests/
   
   # Lint code
   flake8 src/ tests/
   
   # Type check
   mypy src/
   
   # Run pre-commit hooks
   pre-commit run --all-files
   ```

4. **Commit Changes**
   ```bash
   # Stage changes
   git add .
   
   # Commit with conventional message
   git commit -m "feat(parser): add support for nested commands"
   ```

5. **Push and Create PR**
   ```bash
   # Push to your fork
   git push origin feature/your-feature-name
   
   # Create Pull Request on GitHub
   ```

## Coding Standards

### Python Style

We follow **PEP 8** with additional rules:

#### Code Formatting

Use **Black** with default settings:

```bash
# Format code
black src/ tests/

# Check formatting
black --check src/ tests/
```

#### Import Sorting

Use **isort** with Black profile:

```bash
# Sort imports
isort --profile black src/ tests/
```

#### Type Hints

All functions must have type hints:

```python
from typing import List, Dict, Optional, Any

def process_instruction(
    instruction: str,
    context: Optional[Dict[str, Any]] = None
) -> CommandResult:
    """Process a natural language instruction."""
    pass
```

#### Documentation

Use **Google Style** docstrings:

```python
def process_instruction(
    instruction: str,
    context: Optional[Dict[str, Any]] = None
) -> CommandResult:
    """Process a natural language instruction.
    
    Args:
        instruction: Natural language instruction to process
        context: Optional context information
        
    Returns:
        CommandResult: Result of instruction processing
        
    Raises:
        ValidationError: If instruction is invalid
        ProcessingError: If processing fails
        
    Example:
        >>> result = process_instruction("list files")
        >>> print(result.success)
        True
    """
    pass
```

### Naming Conventions

```python
# Classes: PascalCase
class TwoPhaseEngine:
    pass

# Functions: snake_case
def process_instruction(instruction: str) -> Result:
    pass

# Variables: snake_case
user_input = "example"
model_response = "response"

# Constants: UPPER_SNAKE_CASE
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 120

# Private members: prefix with underscore
self._internal_state = {}
self._helper_method()
```

### Error Handling

```python
# Use specific exceptions
try:
    result = process_instruction(instruction)
except ValidationError as e:
    logger.error(f"Validation failed: {e}")
    return None
except ProviderError as e:
    logger.error(f"Provider error: {e}")
    return None
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    raise

# Create custom exceptions
class VexisError(Exception):
    """Base exception for VEXIS-CLI."""
    pass

class ValidationError(VexisError):
    """Raised when input validation fails."""
    pass
```

### Logging

```python
import logging

logger = logging.getLogger(__name__)

def process_instruction(instruction: str):
    logger.info(f"Processing instruction: {instruction[:50]}...")
    
    try:
        result = _process_instruction_internal(instruction)
        logger.info(f"Instruction processed successfully")
        return result
    except Exception as e:
        logger.error(f"Instruction processing failed: {e}")
        raise
```

## Testing

### Test Structure

```
tests/
├── unit/                           # Unit tests
│   ├── test_command_parser.py
│   ├── test_model_runner.py
│   └── test_error_handler.py
├── integration/                    # Integration tests
│   ├── test_ollama_integration.py
│   ├── test_gemini_integration.py
│   └── test_full_workflow.py
├── fixtures/                       # Test data
│   ├── sample_instructions.yaml
│   └── mock_responses.json
└── conftest.py                     # Pytest configuration
```

### Writing Tests

#### Unit Tests

```python
import pytest
from unittest.mock import Mock, patch
from ai_agent.core_processing.command_parser import CommandParser

class TestCommandParser:
    """Test cases for CommandParser class."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.parser = CommandParser()
    
    def test_parse_simple_instruction(self):
        """Test parsing simple file listing instruction."""
        instruction = "list files in current directory"
        result = self.parser.parse(instruction)
        
        assert result.success is True
        assert len(result.commands) == 1
        assert result.commands[0].executable == "ls"
    
    def test_parse_invalid_instruction(self):
        """Test parsing invalid instruction raises error."""
        instruction = ""  # Empty instruction
        
        with pytest.raises(ValidationError):
            self.parser.parse(instruction)
    
    @patch('ai_agent.core_processing.command_parser.subprocess.run')
    def test_command_execution(self, mock_run):
        """Test command execution with mocked subprocess."""
        mock_run.return_value = Mock(returncode=0, stdout="file1\nfile2\n")
        
        result = self.parser.execute_command("ls")
        
        assert result.success is True
        assert "file1" in result.output
```

#### Integration Tests

```python
import pytest
from ai_agent.external_integration.model_runner import ModelRunner

class TestModelRunnerIntegration:
    """Integration tests for ModelRunner."""
    
    @pytest.mark.slow
    def test_ollama_integration(self):
        """Test actual Ollama integration."""
        runner = ModelRunner()
        
        # Skip if Ollama not available
        if not runner.is_ollama_available():
            pytest.skip("Ollama not available")
        
        result = runner.generate_response(
            task_type="task_generation",
            prompt="Generate a simple ls command",
            provider="ollama"
        )
        
        assert result.success is True
        assert len(result.content) > 0
```

### Test Markers

Use pytest markers to categorize tests:

```python
@pytest.mark.slow
def test_large_model_processing():
    """Test that takes a long time."""
    pass

@pytest.mark.internet
def test_api_connectivity():
    """Test that requires internet connection."""
    pass

@pytest.mark.integration
def test_full_workflow():
    """Test complete workflow integration."""
    pass
```

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/unit/test_command_parser.py

# Run with coverage
pytest --cov=src/ai_agent --cov-report=html

# Run only fast tests
pytest -m "not slow"

# Run integration tests
pytest -m integration

# Run with verbose output
pytest -v
```

## Documentation

### Documentation Standards

#### Code Documentation

- All public functions must have docstrings
- Use Google Style format
- Include examples for complex functions
- Document parameters, return values, and exceptions

#### README Updates

When adding features:
- Update feature list
- Add usage examples
- Update installation instructions if needed

#### API Documentation

- Update API reference for new functions
- Document new endpoints
- Include request/response examples

### Writing Documentation

#### Markdown Style

```markdown
# Heading 1

## Heading 2

### Heading 3

- **Bold text** for emphasis
- `code` for inline code
- [Links](url) for references

```python
# Code blocks with language
def example():
    pass
```

> **Note**: Important information
> 
> **Warning**: Critical information
```

#### Example Documentation

```markdown
### Example Usage

```python
from ai_agent.core_processing.two_phase_engine import TwoPhaseEngine

# Initialize engine
engine = TwoPhaseEngine(config)

# Process instruction
result = await engine.process_instruction("list files")

if result.success:
    print(f"Commands: {len(result.commands)}")
else:
    print(f"Error: {result.error}")
```

**Expected Output:**
```
Commands: 1
```
```

## Pull Request Process

### Before Submitting

1. **Code Quality**
   ```bash
   # Run all quality checks
   pre-commit run --all-files
   pytest --cov=src/ai_agent
   ```

2. **Documentation**
   - Update relevant documentation
   - Add examples for new features
   - Update README if needed

3. **Testing**
   - Ensure all tests pass
   - Add tests for new functionality
   - Update test fixtures if needed

### Pull Request Template

Use this template for your PR:

```markdown
## Description
Brief description of changes and motivation.

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Unit tests pass
- [ ] Integration tests pass
- [ ] Manual testing completed

## Checklist
- [ ] Code follows project standards
- [ ] Self-review completed
- [ ] Documentation updated
- [ ] Tests added/updated
- [ ] Ready for review

## Additional Notes
Any additional information for reviewers.
```

### Review Process

1. **Automated Checks**
   - Code quality checks
   - Test suite
   - Documentation build

2. **Peer Review**
   - At least one maintainer approval required
   - Focus on code quality, design, and functionality
   - Request changes as needed

3. **Integration**
   - Merge into main branch
   - Update version if needed
   - Deploy to staging/production

## Contribution Types

### Bug Fixes

**Process:**
1. Create issue describing bug
2. Create branch: `bugfix/bug-description`
3. Write tests that reproduce bug
4. Fix the issue
5. Ensure tests pass
6. Submit PR

**Example:**
```bash
# Branch
git checkout -b bugfix/fix-ollama-timeout

# Test that reproduces issue
def test_ollama_timeout_handling():
    # Test code
    pass

# Fix implementation
def handle_ollama_timeout():
    # Fix code
    pass
```

### New Features

**Process:**
1. Create issue or discuss in GitHub Discussions
2. Design the feature
3. Create branch: `feature/feature-name`
4. Implement feature with tests
5. Update documentation
6. Submit PR

**Example:**
```bash
# Branch
git checkout -b feature/support-custom-models

# Implementation
class CustomModelSupport:
    def __init__(self):
        pass
    
    def add_custom_model(self, model_config):
        pass

# Tests
def test_custom_model_addition():
    pass

# Documentation
# Update API reference and README
```

### Documentation

**Process:**
1. Identify documentation gaps
2. Create branch: `docs/update-section`
3. Write/update documentation
4. Review for clarity and accuracy
5. Submit PR

**Example:**
```bash
# Branch
git checkout -b docs/update-api-reference

# Update documentation
# Edit docs/API_REFERENCE.md

# Review
# Check formatting and links
```

## Community Guidelines

### Code of Conduct

We are committed to providing a welcoming and inclusive environment. Please:

- Be respectful and considerate
- Use inclusive language
- Focus on constructive feedback
- Help others learn and grow

### Communication Channels

- **GitHub Issues**: Bug reports and feature requests
- **GitHub Discussions**: General questions and ideas
- **Pull Requests**: Code contributions and reviews

### Getting Help

1. **Check Documentation**
   - README.md
   - docs/ directory
   - API reference

2. **Search Issues**
   - Look for similar issues
   - Check if already resolved

3. **Ask Questions**
   - GitHub Discussions for general questions
   - Issues for specific problems

## Recognition

### Contributors

All contributors are recognized in:

- **CONTRIBUTORS.md**: List of all contributors
- **Release Notes**: Credit for specific contributions
- **GitHub Statistics**: Contribution graphs and insights

### Types of Contributions

We value all types of contributions:

- **Code**: Features, bug fixes, tests
- **Documentation**: Guides, API docs, examples
- **Design**: UI/UX improvements, graphics
- **Community**: Support, feedback, ideas
- **Infrastructure**: CI/CD, deployment, monitoring

## Release Process

### Version Management

We use **Semantic Versioning**:

- **MAJOR**: Breaking changes
- **MINOR**: New features (backward compatible)
- **PATCH**: Bug fixes (backward compatible)

### Release Checklist

1. **Code Quality**
   - All tests pass
   - Code coverage meets requirements
   - Documentation updated

2. **Version Update**
   - Update version in pyproject.toml
   - Update CHANGELOG.md
   - Tag release

3. **Deployment**
   - Build and test package
   - Deploy to PyPI
   - Update documentation

4. **Announcement**
   - GitHub release
   - Community notification
   - Update README

## Development Tools

### IDE Configuration

#### VS Code

**.vscode/settings.json**:
```json
{
    "python.defaultInterpreterPath": "./venv/bin/python",
    "python.linting.enabled": true,
    "python.linting.flake8Enabled": true,
    "python.linting.mypyEnabled": true,
    "python.formatting.provider": "black",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
        "source.organizeImports": true
    }
}
```

#### PyCharm

- Set Python interpreter to `./venv/bin/python`
- Enable Black formatter
- Configure Flake8 linter
- Set up pytest runner

### Useful Commands

```bash
# Development commands
make test          # Run tests
make lint          # Run linting
make format        # Format code
make docs          # Build documentation
make clean         # Clean build artifacts

# Git commands
make sync          # Sync with upstream
make branch        # Create feature branch
make pr            # Create pull request
```

### Debugging

```bash
# Debug tests
pytest --pdb tests/unit/test_file.py::test_function

# Debug application
python -m pdb run.py "test instruction"

# Profile performance
python -m cProfile -o profile.stats run.py "instruction"
```

## Troubleshooting

### Common Issues

#### Environment Setup

**Problem**: Virtual environment issues
**Solution**:
```bash
# Remove and recreate venv
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### Test Failures

**Problem**: Tests failing locally
**Solution**:
```bash
# Update dependencies
pip install -r requirements-dev.txt --upgrade

# Clear pytest cache
pytest --cache-clear

# Run specific test
pytest tests/unit/test_file.py -v
```

#### Code Quality

**Problem**: Linting errors
**Solution**:
```bash
# Auto-fix formatting
black src/ tests/
isort src/ tests/

# Check specific issues
flake8 src/ --show-source
```

### Getting Help

1. **Check Documentation**
   - Development guide
   - API reference
   - Troubleshooting guide

2. **Ask Community**
   - GitHub Discussions
   - Issues for specific problems

3. **Maintainer Support**
   - @mention maintainers in issues
   - Request review on PRs

Thank you for contributing to VEXIS-CLI! Your contributions help make this project better for everyone.
