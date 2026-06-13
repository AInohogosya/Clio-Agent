import sys

filepath = '/Users/sasakikousei/Projects/Clio-Agent/run.py'
with open(filepath, 'r') as f:
    content = f.read()

new_func = '''def _reset_config_yaml():
    """Reset config.yaml to a clean default state (used by --setting)."""
    import yaml as _yaml

    config_path = Path(__file__).parent.resolve() / "config.yaml"
    clean = {
        "api": {
            "preferred_provider": "",
            "api_keys": {
                "google": "",
                "groq": "",
                "openai": "",
                "anthropic": "",
                "xai": "",
                "meta": "",
                "mistral": "",
                "microsoft": "",
                "cohere": "",
                "deepseek": "",
                "together": "",
                "minimax": "",
                "zhipuai": "",
                "openrouter": "",
            },
            "local_endpoint": "http://localhost:11434",
            "local_model": "llama3.2:3b",
            "models": {
                "ollama": "llama3.2:3b",
                "google": "gemini-3.1-pro-preview",
                "groq": "llama-3.3-70b-versatile",
                "openai": "gpt-4o",
                "anthropic": "claude-opus-4-6-20260219",
                "xai": "grok-4.1",
                "meta": "llama-4-scout-17b-16e-instruct",
                "mistral": "mistral-large-latest",
                "microsoft": "gpt-4o",
                "amazon": "anthropic.claude-opus-4-6-20260219-v1:0",
                "cohere": "command-r-plus",
                "deepseek": "deepseek-chat",
                "together": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
                "minimax": "MiniMax-Text-01",
                "zhipuai": "glm-5",
                "openrouter": "openrouter/owl-alpha",
            },
            "timeout": 120,
            "max_retries": 3,
        },
        "security": {
            "enable_command_blocking": False,
            "enable_confirmation_prompts": False,
            "enable_sudo_warning": False,
            "enable_shell_pipe_warning": False,
            "enable_sandbox": False,
        },
        "execution": {
            "safety_mode": True,
            "dry_run": False,
            "verify_commands": True,
            "command_timeout": 1800,
            "task_timeout": 7200,
            "max_iterations": 500,
            "auto_recovery": True,
            "show_thought_log": True,
        },
        "logging": {
            "level": "INFO",
            "file": "vexis.log",
            "json_format": False,
            "console": True,
        },
        "cache": {
            "enabled": True,
            "max_size": 1000,
            "ttl": 3600,
            "persist_to_disk": True,
        },
        "cost": {
            "daily_budget": None,
            "monthly_budget": None,
            "per_request_budget": None,
            "warning_threshold": 0.8,
            "critical_threshold": 0.95,
        },
        "performance": {
            "max_concurrent_tasks": 1,
            "memory_limit_mb": 1024,
        },
        "user": {
            "name": "",
            "preferred_style": "detailed",
            "auto_confirm": False,
            "show_progress": True,
        },
        "telegram": {
            "enabled": False,
            "bot_token": "",
            "bot_username": "",
            "api_id": 0,
            "api_hash": "",
            "session_name": "vexis_telegram",
            "authorized_users": [],
            "allowed_user_ids": [],
            "enable_input_listener": True,
            "max_history_length": 50,
            "bot_name": "VEXIS Agent",
        },
        "custom_system_prompt": "",
    }
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            _yaml.dump(clean, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        print("config.yaml has been reset to defaults.")
    except Exception as e:
        print(f"Could not reset config.yaml: {e}")


'''

target = 'def _run_health_check():'
pos = content.find(target)
if pos == -1:
    print('ERROR: Could not find target')
    sys.exit(1)
content = content[:pos] + new_func + content[pos:]
with open(filepath, 'w') as f:
    f.write(content)
line_num = content[:pos].count(chr(10)) + 1
print(f'Inserted _reset_config_yaml at line {line_num}')
