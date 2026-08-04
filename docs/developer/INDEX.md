# Developer Documentation

Welcome to the Clio-Agent-2 developer reference. This folder contains everything you need to understand, extend, and contribute to the codebase.

---

## 📋 Contents

### Overview
- [Architecture Overview](ARCHITECTURE.md) — System design, module relationships, data flow
- [API Reference](API.md) — Python API for embedding or extending Clio-Agent-2

### Development Guide
- [Development Setup](DEV_SETUP.md) — Setting up a dev environment, running tests, linting
- [Contributing Guide](CONTRIBUTING.md) — How to submit changes, code conventions, PR process
- [Testing Guide](TESTING.md) — Running tests, writing new tests, test conventions

### Core Modules
- [Core Modules](CORE_MODULES.md) — `agent.py`, `llm_router.py`, `context_manager.py`, `retry.py`, `token_budget.py`
- [Tool System](tools/OVERVIEW.md) — Tool registry, registering new tools, existing tool implementations
- [Interfaces](interfaces/OVERVIEW.md) — CLI, Telegram, Discord, WhatsApp interface implementations

### Reference
- [Configuration Reference](CONFIGURATION_REFERENCE.md) — All env vars and settings, data types, defaults
