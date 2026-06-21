# Neuro-Scaffold

Production-grade AI coding agent subagent for the Clio Agent framework.

## Architecture

Neuro-Scaffold implements a **Plan -> Execute -> Observe -> Reflect** state machine with:

- **AST-based context mapping** for targeted code retrieval
- **Persistent shell sessions** via pexpect
- **Smart diff-based editing** with strict validation
- **Background linter/syntax interception**
- **Docker sandbox containment** with non-root user, read-only rootfs, capability dropping
- **JWT authentication** for secure Clio Agent integration
- **FastAPI gateway** with session management

## Quick Start

```bash
cd neuro-scaffold
uv sync --extra dev
source .venv/bin/activate
pytest tests/ -v
python3 -m neuro_scaffold.gateway.server
```

## Security

- Non-root user (UID 10000) with minimal capabilities
- Read-only root filesystem
- Network isolation via internal Docker bridge
- PID limit (256) to prevent fork bombs
- JWT-based authentication with rate limiting
- Output truncation for large logs
- Dangerous command detection and blocking

## Project Structure

```
neuro-scaffold/
├── src/neuro_scaffold/
│   ├── agent/          # State machine, models, planner, executor, observer, reflector
│   ├── tools/          # Shell, editing, linter
│   ├── context/        # AST mapper, context retriever
│   ├── security/       # Auth, session management, Docker sandbox
│   ├── gateway/        # FastAPI server for Clio integration
│   └── config/         # Settings, prompts
├── tests/
│   ├── unit/           # Unit tests for all tools
│   └── integration/    # Integration tests for agent loop
├── Dockerfile          # Multi-stage secure build
├── docker-compose.yml  # Containment configuration
└── pyproject.toml
```
