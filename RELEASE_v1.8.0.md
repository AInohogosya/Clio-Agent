# v1.8.0

## 🚀 Highlights

This release is a **project rebranding** from VEXIS-CLI to Clio-Agent-1. All project references, configuration files, documentation, and environment variables have been updated to reflect the new naming convention. This change aligns the project name with its public GitHub repository and clarifies the product identity.

## 📦 Project Renaming — VEXIS-CLI → Clio-Agent-1

The entire project has been renamed from VEXIS-CLI to Clio-Agent-1:

- **Repository references** — All GitHub URLs, clone links, and documentation references now point to `AInohogosya/Clio-Agent`
- **Configuration files** — `.gitignore` updated to use `.clio_agent/` instead of `.vexis/`
- **Environment variables** — All `VEXIS_` prefixed variables renamed to `CLIO_` (e.g., in `eternal_supervisor.py`)
- **Documentation** — All docs updated (CONFIGURATION.md, CONTRIBUTING.md, DEPLOYMENT.md, ERROR_HANDLING.md, OLLAMA_INTEGRATION.md, README.md, SELF_HEALING_ARCHITECTURE.md, TROUBLESHOOTING.md)
- **Code references** — All docstrings, print statements, and internal references updated for consistency
- **Config examples** — `config.yaml.example` and `peripherals/config.example.yaml` updated with new project name
- **Log files** — Bot names and log file references updated
- **GUI components** — All GUI modules updated (app.py, launcher.py, main_window.py, resources.py, theme.py)
- **External integrations** — All provider modules and integration files updated
- **Peripherals** — All peripheral modules, SDKs, and utilities updated

## 📊 Stats

- **Files changed**: 71 files
- **Insertions**: 361 lines
- **Deletions**: 488 lines
- **Commits since v1.7.0**: 1

## 🔗 Full Changelog

[v1.7.0...v1.8.0](https://github.com/AInohogosya/Clio-Agent/compare/v1.7.0...v1.8.0)