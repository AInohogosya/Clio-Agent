# Security Policy

## Supported Versions

We release security patches for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |
| < 1.0   | :x:                |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to **security@clio-agent-2.example.com** (replace with actual contact).

You should receive a response within 48 hours. If for some reason you do not, please follow up via email to ensure we received your original message.

Please include the following information in your report:

- Type of issue (e.g., buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit it

## Security Considerations for Users

Clio-Agent-2 is a powerful autonomous agent. Please understand these security implications before running it:

### 1. Shell Command Execution

The agent has a `shell_command` tool that can execute **arbitrary shell commands** with the privileges of the user running the agent.

- **No sandbox**, **no allow-list**, **no confirmation prompt**
- Combined with web access and autonomous mode, a poorly-worded task or malicious web content could cause destructive actions
- **Mitigation**: Run in a dedicated/least-privilege account, container, or VM you don't mind wiping

### 2. Autonomous Mode

Autonomous mode is **enabled by default**. The agent runs a background loop that:

- Calls the LLM every few seconds (configurable via `THINKING_INTERVAL`)
- Can take actions and message you **without any prompt**
- **Costs money and API quota** on every cycle

Disable with `AUTONOMOUS_MODE=false` in `.env` or `/stop` in the CLI.

### 3. API Keys and Secrets

- All API keys and tokens are stored in plaintext in `clio_agent_2/config/.env`
- The `.env` file is **gitignored** by default
- Never commit your `.env` file
- Rotate keys if you suspect they were exposed

### 4. Prompt Injection

Because the agent can:

- Fetch web pages (`fetch_url`, `web_search`)
- Execute shell commands (`shell_command`)
- Run autonomously

...malicious content it encounters could attempt to steer its behavior. The **LLM Settings Lock** (`LLM_SETTINGS_LOCKED=true`) prevents unauthorized model/provider changes, but does not restrict tool use.

### 5. Data Handling

- Conversation context is persisted to disk (`context_log.json`)
- Context is compressed via LLM summarization when it grows large
- No data is sent to third parties except your configured LLM provider and search API

## Disclosure Policy

When we receive a security report:

1. We acknowledge receipt within 48 hours
2. We investigate and validate the issue
3. We develop a fix and prepare a release
4. We coordinate disclosure timing with the reporter
5. We publish a security advisory and release the fix

## Security Best Practices for Contributors

- Never commit secrets (API keys, tokens) to the repository
- Use `bandit` for static security analysis: `bandit -r clio_agent_2/`
- Keep dependencies updated: `pip-audit` or `pip list --outdated`
- Follow the principle of least privilege in code changes
- Sanitize all user inputs, especially for shell command execution

## Responsible Use

This software is provided for educational and research purposes. Users are responsible for:

- Complying with all applicable laws and regulations
- Respecting the terms of service of LLM providers and APIs
- Running the agent in a secure, isolated environment
- Monitoring autonomous behavior and costs

---

*This policy is adapted from GitHub's [SECURITY.md template](https://docs.github.com/en/code-security/getting-started/adding-a-security-policy-to-your-repository).*