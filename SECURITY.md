# Security Policy

## Supported Versions

We actively maintain and provide security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 3.x     | ✅ Yes             |
| 2.x     | ❌ No              |
| 1.x     | ❌ No              |

## Reporting a Vulnerability

We take security seriously. If you discover a security vulnerability, please report it **privately** to:

**Email**: security@clio-project.org

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fixes

We will:
1. Acknowledge receipt within 48 hours
2. Provide a timeline for fix within 7 days
3. Keep you informed of progress
4. Credit you in the security advisory (if desired)

## Security Considerations

### Command Execution
Clio Agent executes arbitrary shell commands on your machine. This is by design — it's an automation agent. **Only run it in environments you trust and control.**

### API Keys
- API keys are stored in `config.yaml` (git-ignored by default)
- Never commit `config.yaml` to version control
- Use environment variables for CI/CD and production deployments
- Rotate keys regularly

### Network Access
- The agent makes outbound HTTPS requests to AI provider APIs
- No inbound network listeners by default
- Telegram/Discord bots use long-polling/webhooks (outbound only)

### Sandbox Mode
Enable sandbox mode in `config.yaml`:
```yaml
security:
  enable_sandbox: true
```

### Code Execution
The agent can write and execute code. Treat it like you would any developer with shell access.

## Security Best Practices

1. **Run in a container** for isolation
2. **Use dedicated API keys** with minimal permissions
3. **Monitor agent activity** via the real-time terminal log
4. **Restrict filesystem access** using OS permissions
5. **Enable command confirmation** for sensitive operations:
   ```yaml
   security:
     enable_confirmation_prompts: true
   ```

## Vulnerability Disclosure Timeline

| Phase | Timeline |
|-------|----------|
| Acknowledgment | 48 hours |
| Initial assessment | 7 days |
| Fix development | 30 days (critical), 90 days (non-critical) |
| Public disclosure | After fix released + 14 days |

## Security Updates

Security updates are released as patch versions (e.g., 3.0.1). Subscribe to:
- [GitHub Security Advisories](https://github.com/clio-project/Clio-Agent-1/security/advisories)
- [Release notifications](https://github.com/clio-project/Clio-Agent-1/releases)

## Scope

This policy covers the Clio Agent 1 codebase and official Docker images. It does not cover:
- Third-party AI provider APIs
- User-written code executed by the agent
- Operating system vulnerabilities