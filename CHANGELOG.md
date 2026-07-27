# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-07-27

### Added
- Perpetual autonomous think-execute loop agent
- Context survival across crashes/restarts via `.context/` directory
- LLM-powered context compression (3-level fallback)
- 16 AI provider support (Ollama, OpenAI, Anthropic, Google, xAI, Meta, Groq, DeepSeek, Mistral, Azure, Bedrock, Cohere, Together, MiniMax, Zhipu, OpenRouter)
- Provider fallback with circuit breaker pattern
- Self-healing command execution (auto-install, sudo retry, SSL fix, disk cleanup)
- Parallel command execution with `parallel_begin`/`parallel_end`
- Telegram bot with anti-duplication protocol
- Discord bot support
- Eternal Supervisor watchdog process (`--supervisor`)
- Periodic auto-save (60s interval + every 10 iterations)
- Graceful signal handling (SIGINT/SIGTERM)
- Cross-platform support (macOS, Linux, Windows)
- Docker images for Ubuntu, Alpine, Rocky Linux, macOS, Windows
- One-command installer (`install.sh`)
- Interactive provider/model selection on first run
- Comprehensive test suite
- CLI entry points: `Clio-Agent` and `clio-agent`

### Changed
- Refactored to modular architecture in `src/ai_agent/`
- Moved from single-file `run.py` to package structure
- Updated to Python 3.8+ requirement
- Switched to `pyproject.toml` with modern build system

### Security
- API keys stored in git-ignored `config.yaml`
- Optional command blocking and confirmation prompts
- Sandbox mode for restricted execution

## [2.0.0] - 2025-12-15

### Added
- Initial autonomous agent implementation
- Basic Telegram integration
- Ollama local provider support
- Context persistence

### Changed
- Complete rewrite from synchronous to asynchronous architecture

## [1.0.0] - 2025-06-01

### Added
- Initial release
- Basic command execution loop
- Simple context management