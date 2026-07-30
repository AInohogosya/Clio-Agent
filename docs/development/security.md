# Security Documentation

This document describes the security architecture, threat model, and best practices for Clio-Agent-2.

## Table of Contents

1. [Threat Model](#threat-model)
2. [Security Architecture](#security-architecture)
3. [Data Handling](#data-handling)
4. [API Key Management](#api-key-management)
5. [Shell Command Execution](#shell-command-execution)
6. [Network Security](#network-security)
7. [Supply Chain Security](#supply-chain-security)
8. [Incident Response](#incident-response)

## Threat Model

### Assets to Protect

1. **User API Keys** - LLM provider keys, search API keys, bot tokens
2. **System Access** - The agent can execute arbitrary shell commands
3. **User Data** - Files read/written, conversation context, web search history
4. **Agent Integrity** - Preventing unauthorized behavior modification

### Threat Actors

| Actor | Capability | Motivation |
|-------|------------|------------|
| External attacker | Network access, malicious web content | Data theft, system compromise |
| Malicious user | Direct CLI access, config access | Privilege escalation |
| Compromised LLM | Prompt injection via web content | Unauthorized actions |
| Supply chain | Malicious dependency | Backdoor injection |

### Attack Vectors

1. **Prompt Injection** - Web content/fetched URLs steering agent behavior
2. **Shell Command Injection** - Malicious tool calls executing arbitrary commands
3. **Credential Theft** - Exfiltration of API keys from config/memory
3. **Autonomous Mode Abuse** - Unauthorized actions during unsupervised operation
4. **Supply Chain** - Compromised dependencies

## Security Architecture

### Defense Layers

```
┌─────────────────────────────────────────────────────────────┐
│                    USER CONTROLS                             │
│  • LLM Settings Lock (provider/model immutability)          │
│  • Autonomous Mode Toggle (on/off)                          │
│  • Configuration Validation (placeholder rejection)         │
├─────────────────────────────────────────────────────────────┤
│                    APPLICATION LAYER                         │
│  • Input Sanitization (tool argument validation)            │
│  • Output Filtering (no raw LLM output to shell)            │
│  • Context Isolation (per-session context logs)             │
├─────────────────────────────────────────────────────────────┤
│                    INFRASTRUCTURE                            │
│  • Virtual Environment Isolation                            │
│  • File Permission Controls (.env 600 recommended)          │
│  • Process Isolation (single-user execution)                │
└─────────────────────────────────────────────────────────────┘
```

### Key Security Features

#### 1. LLM Settings Lock (`LLM_SETTINGS_LOCKED=true`)

Prevents unauthorized changes to:
- Active LLM provider
- Active model

Requires explicit `/llm_unlock` → change → `/llm_lock` sequence.
State persisted to `.env` survives restarts.

#### 2. Placeholder Rejection

The configuration system treats these as "not set":
- `your_*_api_key_here`
- `sk-your-actual-*-api-key-here`
- `<placeholder>`
- `placeholder`
- `example`
- `xxxx`

Ensures first-run setup cannot be bypassed with template values.

#### 3. Autonomous Mode Opt-In Defaults

While enabled by default for usability, it:
- Requires explicit `DEFAULT_MODEL` to start
- Backs off exponentially on repeated errors
- Can be disabled instantly via `/stop` or config

#### 4. Context Persistence Safety

- Atomic writes with `.bak` fallback
- Clean shutdown flushes (Ctrl+C, SIGTERM, `/exit`)
- `/clear_context` creates backup before wipe
- `/restore_context` recovers from backup

## Data Handling

### What Data Leaves Your Machine

| Data | Destination | Purpose |
|------|-------------|---------|
| Prompts + context | Your configured LLM provider | Model inference |
| Search queries | Your configured search API | Web search |
| URLs fetched | Target websites | Content retrieval |
| Telegram messages | Telegram servers | Bot communication |
| Discord messages | Discord servers | Bot communication |

### What Stays Local

- All API keys and tokens (in `.env`)
- Full conversation context log (`context_log.json`)
- File system operations (read/write/edit)
- Shell command execution and output
- Configuration settings (mirrored in `config.yaml` without secrets)

### Data Retention

- Context log: Persisted until manually cleared or auto-compressed
- Compressed summaries: Retained in context log
- Backup files: `context_log.json.bak` kept until next write
- No automatic purging - user controls via `/clear_context`

## API Key Management

### Storage

Keys stored in `clio_agent_2/config/.env`:
```
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
TELEGRAM_BOT_TOKEN=123456:ABC...
```

### Protection

1. **File permissions**: Set `.env` to `600` (owner read/write only)
2. **Git ignore**: `.env` and `.env.*` in `.gitignore`
3. **No logging**: Keys never written to logs
4. **Mirror file**: `config.yaml` excludes secrets automatically

### Rotation

If a key is compromised:
1. Revoke at provider dashboard
2. Generate new key
3. Update via `/configure` or edit `.env`
4. Restart agent

### Least Privilege

Use provider-specific keys with minimal scopes:
- OpenAI: Project-scoped keys
- Anthropic: Workspace keys
- Telegram: Bot token (inherently scoped)
- Discord: Bot token with minimal intents

## Shell Command Execution

### Risk Assessment

**HIGH RISK** - The `shell_command` tool executes arbitrary commands with the agent's user privileges.

### Current Protections

1. **No sandbox** - Runs directly on host
2. **No allow-list** - Any command permitted
3. **No confirmation** - Executes immediately
4. **Retry on timeout** - Up to 5 attempts for transient failures

### Recommended Mitigations

| Mitigation | Effort | Protection Level |
|------------|--------|------------------|
| Dedicated low-privilege user | Low | Medium |
| Docker/container isolation | Medium | High |
| VM with snapshots | Medium | High |
| Read-only filesystem mounts | Low | Medium |
| Command allow-list (future) | High | Very High |

### Safe Deployment Patterns

```bash
# Option 1: Dedicated user
sudo useradd -m -s /bin/bash clioagent
sudo -u clioagent python3 run.py

# Option 2: Docker (basic)
docker run --rm -it \
  -v $(pwd):/workspace \
  -v /path/to/.env:/app/clio_agent_2/config/.env:ro \
  clio-agent-2 python3 run.py

# Option 3: Docker with resource limits
docker run --rm -it \
  --cpus=2 --memory=4g \
  --security-opt=no-new-privileges \
  --cap-drop=ALL \
  -v $(pwd):/workspace \
  clio-agent-2 python3 run.py
```

## Network Security

### Outbound Connections

The agent makes HTTPS requests to:
- LLM provider APIs (OpenAI, Google, Anthropic, etc.)
- Search APIs (Serper, Bing, etc.)
- Telegram Bot API (`api.telegram.org`)
- Discord Gateway (`gateway.discord.gg`)
- WhatsApp Business API (`graph.facebook.com`)
- Arbitrary URLs via `fetch_url` tool

### Inbound Connections

Only for webhook-based interfaces:
- WhatsApp: Configurable webhook URL/port (default 8080)
- No inbound for CLI, Telegram (long polling), Discord (gateway)

### TLS Verification

All HTTPS connections verify certificates by default.
No option to disable verification (intentional).

### Proxy Support

Respects standard environment variables:
- `HTTP_PROXY` / `HTTPS_PROXY`
- `NO_PROXY`

## Supply Chain Security

### Dependency Management

- Pinned versions in `requirements.txt` and `pyproject.toml`
- Optional dependencies for optional providers
- Regular updates via `pip-audit` in CI

### CI Security Checks

```yaml
# .github/workflows/security.yml
- Bandit static analysis
- pip-audit for known vulnerabilities
- Dependency license scanning
```

### Verification

```bash
# Local security scan
pip install bandit pip-audit
bandit -r clio_agent_2/
pip-audit -r clio_agent_2/requirements.txt
```

## Incident Response

### If You Suspect Compromise

1. **Immediate**: Stop the agent (`Ctrl+C` or kill process)
2. **Contain**: Revoke all API keys used by the agent
3. **Investigate**: Check `context_log.json` for unauthorized actions
4. **Remediate**: Rotate credentials, audit system for changes
5. **Report**: See [SECURITY.md](../SECURITY.md) for disclosure process

### Log Analysis

```bash
# Check for shell commands executed
grep "shell_command" clio_agent_2/context_log.json

# Check for autonomous actions
grep "autonomous" clio_agent_2/clio_agent.log

# Check for configuration changes
grep "config" clio_agent_2/clio_agent.log
```

### Recovery

1. Restore from known-good backup if available
2. Re-create virtual environment: `rm -rf .venv && python3 run.py`
3. Reconfigure with fresh API keys

## Security Checklist for Deployments

- [ ] `.env` file permissions set to `600`
- [ ] Running as dedicated low-privilege user
- [ ] Container/VM isolation in place
- [ ] `LLM_SETTINGS_LOCKED=true` (default)
- [ ] Autonomous mode disabled if not needed
- [ ] API keys are project-scoped, not root/organization
- [ ] Monitoring/alerting on unusual autonomous activity
- [ ] Regular dependency updates scheduled
- [ ] Incident response plan documented

## Future Security Enhancements

See [GitHub Issues](https://github.com/your-org/Clio-Agent-2/issues?q=label%3Asecurity) for tracking.

Planned:
- [ ] Command allow-list / deny-list for `shell_command`
- [ ] Sandbox execution via gVisor/Firecracker
- [ ] Audit logging to external SIEM
- [ ] mTLS for inter-component communication
- [ ] Hardware security module (HSM) key storage option