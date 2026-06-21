# Quick Start Guide

Get Clio Agent 1 up and running in under 5 minutes.

## 1. Install

```bash
git clone https://github.com/AInohogosya/Clio-Agent-1.git
cd Clio-Agent-1
bash install.sh
```

This will:
- Check Python 3.8+ is available
- Create a virtual environment (`venv/`)
- Install all dependencies
- Register the `Clio-Agent` global command

## 2. Configure

```bash
cp config.yaml.example config.yaml
```

Edit `config.yaml` and add at least one API key:

```yaml
api:
  preferred_provider: "openrouter"
  api_keys:
    openrouter: "sk-or-v1-your-key-here"
```

Or set an environment variable:

```bash
export OPENROUTER_API_KEY="sk-or-v1-your-key-here"
```

## 3. Run

```bash
# Interactive setup (prompts for provider/model)
Clio-Agent

# Run a specific task
Clio-Agent "list all Python files in the current directory"

# Show all options
Clio-Agent --help
```

## 4. Next Steps

- Read `DEVELOPERS.md` for the full architecture guide
- Read `AGENTS.md` to understand how the agent behaves
- Check `config.yaml.example` for all configuration options
- Run `pytest tests/ -v` to verify everything works
- Try `Clio-Agent --health-check` for system diagnostics

## Common First Tasks

```bash
# Check the environment
Clio-Agent --health-check

# Run a simple exploration task
Clio-Agent "explore this codebase and summarize what it does"

# Run with auto-restart supervisor
Clio-Agent --supervisor "monitor the system logs"

# Run tests
pytest tests/ -v
```
