# Configuration Reference

Complete reference for all `config.yaml` options.

## Config File Location

Priority order:
1. CLI: `Clio-Agent --config /path/to/config.yaml`
2. Environment: `CLIO_CONFIG=/path/to/config.yaml`
3. Default: `config.yaml` in project root

## API Section

```yaml
api:
  # Preferred AI provider (used when no specific provider is requested)
  preferred_provider: "openrouter"
  
  # API keys (alternatively, use environment variables)
  api_keys:
    google: "YOUR_GOOGLE_API_KEY"
    groq: "YOUR_GROQ_API_KEY"
    openai: "YOUR_OPENAI_API_KEY"
    anthropic: "YOUR_ANTHROPIC_API_KEY"
    xai: "YOUR_XAI_API_KEY"
    meta: "YOUR_META_API_KEY"
    mistral: "YOUR_MISTRAL_API_KEY"
    microsoft: "YOUR_MICROSOFT_API_KEY"
    cohere: "YOUR_COHERE_API_KEY"
    deepseek: "YOUR_DEEPSEEK_API_KEY"
    together: "YOUR_TOGETHER_API_KEY"
    minimax: "YOUR_MINIMAX_API_KEY"
    zhipuai: "YOUR_ZHIPUAI_API_KEY"
    openrouter: "YOUR_OPENROUTER_API_KEY"
  
  # Local Ollama endpoint
  local_endpoint: "http://localhost:11434"
  local_model: "llama3.2:3b"
  
  # Per-provider model selection
  models:
    ollama: "llama3.2:3b"
    google: "gemini-3.1-pro-preview"
    groq: "llama-3.3-70b-versatile"
    openai: "gpt-4o"
    anthropic: "claude-opus-4-6-20260219"
    xai: "grok-4.1"
    meta: "llama-4-scout-17b-16e-instruct"
    mistral: "mistral-large-latest"
    microsoft: "gpt-4o"
    amazon: "anthropic.claude-opus-4-6-20260219-v1:0"
    cohere: "command-r-plus"
    deepseek: "deepseek-chat"
    together: "meta-llama/Llama-4-Scout-17B-16E-Instruct"
    minimax: "MiniMax-Text-01"
    zhipuai: "glm-5"
    openrouter: "openrouter/owl-alpha"
  
  timeout: 120        # API request timeout in seconds
  max_retries: 3      # Max retries per request
```

## Security Section

```yaml
security:
  enable_command_blocking: false    # Block dangerous commands (rm -rf, etc.)
  enable_confirmation_prompts: false  # Ask before executing commands
  enable_sudo_warning: false        # Warn before sudo commands
  enable_shell_pipe_warning: false  # Warn before pipe commands
  enable_sandbox: false             # Run commands in sandbox
```

## Execution Section

```yaml
execution:
  safety_mode: true          # Enable safety checks on commands
  dry_run: false             # Don't actually execute commands
  verify_commands: true      # Verify commands before execution
  command_timeout: 1800      # Max command runtime in seconds (30 min)
  task_timeout: 7200         # Max task runtime in seconds (2 hours)
  max_iterations: 500        # Max loop iterations before auto-sleep
  auto_recovery: true        # Auto-recover from errors
  show_thought_log: true     # Show thinking in terminal output
  idle_behavior: "fairy"     # "sleep" (default) or "fairy" (curiosity-driven)
```

## Logging Section

```yaml
logging:
  level: "INFO"              # DEBUG, INFO, WARNING, ERROR
  file: "clio_agent.log"     # Log file path
  json_format: false         # Use JSON format for log entries
  console: true              # Also log to console
```

## Cache Section

```yaml
cache:
  enabled: true              # Enable response caching
  max_size: 1000             # Max cached responses
  ttl: 3600                  # Cache TTL in seconds
  persist_to_disk: true      # Persist cache to disk
```

## Cost Section

```yaml
cost:
  daily_budget: null         # Daily budget in USD (null = unlimited)
  monthly_budget: null       # Monthly budget in USD
  per_request_budget: null   # Per-request budget in USD
  warning_threshold: 0.8     # Warn at 80% of budget
  critical_threshold: 0.95   # Critical at 95% of budget
```

## Performance Section

```yaml
performance:
  max_concurrent_tasks: 1    # Max concurrent tasks
  memory_limit_mb: 1024      # Memory limit in MB
```

## User Section

```yaml
user:
  name: ""                   # Your name
  preferred_style: "detailed"  # concise, detailed, minimal
  auto_confirm: false        # Auto-confirm commands
  show_progress: true        # Show progress updates
```

## Telegram Section

```yaml
telegram:
  enabled: true              # Enable Telegram bot mode
  bot_token: "YOUR_BOT_TOKEN"  # Or set TELEGRAM_BOT_TOKEN env var
  bot_username: "@your_bot"
  telegram_user_id: ""       # Default user/chat ID for startup replies
  authorized_users:          # Allowed Telegram user IDs
    - 123456789
  allowed_user_ids:          # Alias for authorized_users
    - 123456789
  enable_input_listener: true   # Listen for incoming messages
  max_history_length: 50        # Max conversation history
  bot_name: "Solvent"            # Bot display name
```

## Discord Section

```yaml
discord:
  enabled: false             # Enable Discord bot mode
  bot_token: "YOUR_DISCORD_BOT_TOKEN"
  authorized_users: []
  allowed_user_ids: []
  max_history_length: 50
  bot_name: "Clio Agent"
```

## Custom System Prompt

```yaml
# Custom prompt injected into Phase 1 system prompt only
custom_system_prompt: ""
```

## Environment Variable Overrides

| Environment Variable | Overrides |
|---|---|
| `CLIO_CONFIG` | Config file path |
| `OPENROUTER_API_KEY` | `api.api_keys.openrouter` |
| `OPENAI_API_KEY` | `api.api_keys.openai` |
| `ANTHROPIC_API_KEY` | `api.api_keys.anthropic` |
| `GOOGLE_API_KEY` | `api.api_keys.google` |
| `TELEGRAM_BOT_TOKEN` | `telegram.bot_token` |
| `TELEGRAM_AUTHORIZED_USERS` | `telegram.authorized_users` |
| `CLIO_WATCHDOG_DISABLED` | Disable watchdog (set to "1" or "true") |
| `CLIO_WATCHDOG_TIMEOUT` | Watchdog heartbeat timeout in seconds |
