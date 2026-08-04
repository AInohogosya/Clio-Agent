# Documentation

This folder contains structured documentation for Clio-Agent-2, split into two perspectives:

- **[User Documentation](user/INDEX.md)** — Everything an end-user needs to install, configure, and use the agent.
- **[Developer Documentation](developer/INDEX.md)** — Architecture, APIs, development setup, and internals for contributors and integrators.

---

## 📁 Structure

```
docs/
├── README.md                          # This file
│
├── user/                              # 👤 End-user documentation
│   ├── INDEX.md                       # User docs table of contents
│   ├── INSTALLATION.md                # System requirements and setup
│   ├── QUICK_START.md                 # Get running in minutes
│   ├── FIRST_LAUNCH.md                # First-run walkthrough
│   ├── CONFIGURATION.md               # All settings explained
│   ├── LLM_PROVIDERS.md               # Supported AI models and how to switch
│   ├── BOT_INTERFACES.md              # Telegram, Discord, WhatsApp setup
│   ├── CLI_GUIDE.md                   # Terminal interface guide + commands
│   ├── AUTONOMOUS_MODE.md             # Background loop: how it works and controls
│   ├── SAFETY.md                      # Risks and best practices
│   ├── TROUBLESHOOTING.md             # Common issues and fixes
│   ├── LIMITATIONS.md                 # Known weaknesses and tradeoffs
│   └── FAQ.md                         # Frequently asked questions
│
└── developer/                         # 🛠️ Developer documentation
    ├── INDEX.md                       # Developer docs table of contents
    ├── ARCHITECTURE.md                # System design, modules, data flow
    ├── API.md                         # Python API reference
    ├── DEV_SETUP.md                   # Local dev environment and tooling
    ├── CONTRIBUTING.md                # Contribution guidelines and PR process
    ├── TESTING.md                     # Running and writing tests
    ├── CORE_MODULES.md                # Deep dives into agent, router, context, retry
    ├── CONFIGURATION_REFERENCE.md     # Exhaustive env var / settings list
    ├── tools/
    │   └── OVERVIEW.md                # Tool registry, registering tools, existing tools
    └── interfaces/
        └── OVERVIEW.md                # CLI, Telegram, Discord, WhatsApp internals
```

---

## 🚀 Quick Links

| I want to... | Go to |
|--------------|-------|
| Install the agent | [user/INSTALLATION.md](user/INSTALLATION.md) |
| Get started quickly | [user/QUICK_START.md](user/QUICK_START.md) |
| Understand configuration | [user/CONFIGURATION.md](user/CONFIGURATION.md) |
| Use slash commands | [user/CLI_GUIDE.md](user/CLI_GUIDE.md) |
| Set up a Telegram bot | [user/BOT_INTERFACES.md](user/BOT_INTERFACES.md) |
| Stay safe | [user/SAFETY.md](user/SAFETY.md) |
| Fix a problem | [user/TROUBLESHOOTING.md](user/TROUBLESHOOTING.md) |
| Understand the architecture | [developer/ARCHITECTURE.md](developer/ARCHITECTURE.md) |
| Embed or extend the API | [developer/API.md](developer/API.md) |
| Set up a dev environment | [developer/DEV_SETUP.md](developer/DEV_SETUP.md) |
| Contribute a change | [developer/CONTRIBUTING.md](developer/CONTRIBUTING.md) |
| Write or run tests | [developer/TESTING.md](developer/TESTING.md) |
