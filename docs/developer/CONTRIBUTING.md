# Contributing Guide

How to contribute to Clio-Agent-2.

---

## 🤝 How to Contribute

1. **Fork** the repository
2. **Create a feature branch:**
   ```bash
   git checkout -b feature/my-change
   ```
3. **Make your changes** following the code conventions in [DEV_SETUP.md](DEV_SETUP.md)
4. **Run the full check suite:**
   ```bash
   ruff check clio_agent_2/ && ruff format clio_agent_2/ && mypy clio_agent_2/ && pytest
   ```
5. **Commit** with a clear message:
   ```
   feat(core): add retry with jitter to LLM calls
   ```
6. **Open a Pull Request** against the `main` branch
7. Address review feedback

---

## 📋 Code of Conduct

See [CODE_OF_CONDUCT.md](../../CODE_OF_CONDUCT.md) at the repository root.

---

## 🔀 Branching Convention

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready code |
| `feature/*` | New features |
| `fix/*` | Bug fixes |
| `docs/*` | Documentation only |

---

## 🔀 Commit Message Convention

Follow Conventional Commits:

```
type(scope): short description

type: feat | fix | docs | style | refactor | perf | test | chore | build | ci | revert
scope: (optional) core | agent | llm_router | context_manager | tools | interfaces | config | cli | telegram | discord | whatsapp
```

---

## 📥 Pull Request Process

1. **Title** — clear, concise (max 72 chars)
2. **Description** — what changed and why
3. **Linked issue** — reference related issue numbers (e.g. `Closes #42`)
4. **Checks must pass** — CI runs lint, type check, test, security
5. **At least one approval** required before merge

---

## 🧪 Testing Requirements

- Add tests for new features (functional tests expected)
- All existing tests must still pass
- See [Testing Guide](TESTING.md) for details

---

## 🛡️ Security Policy

For security issues, please do NOT open a public issue. See [SECURITY.md](../../SECURITY.md) for responsible disclosure instructions.

---

## 🔍 What to Contribute

- Bug fixes
- New tools (following the tool pattern in `tools/tool_registry.py`)
- New interface adapters
- Documentation improvements
- Test coverage improvements
- Performance optimizations

Not encouraged:
- Rewriting the CLI interface without discussion
- Changing core tool safety semantics without a strong argument

---

## 🧭 Related Docs

- [Development Setup](DEV_SETUP.md) — local dev environment
- [API Reference](API.md) — extending the Python API
- [Testing Guide](TESTING.md) — writing tests
