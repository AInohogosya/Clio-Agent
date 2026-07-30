#!/usr/bin/env python3
"""
Environment Variable Setup Script for Clio-Agent-2

This is the single, navigable "configuration screen" for Clio-Agent-2. It
covers every secret / setting the agent needs:

  * Large-scale model API keys  -> the LLM providers
      (OpenAI, Google, Anthropic, OpenRouter, Grok, DeepSeek, Mistral, Groq,
       Perplexity, Together, Fireworks, NVIDIA, Qwen, HuggingFace, DeepInfra,
       Ollama) plus any custom "Other" provider you add by ID
  * Search API key             -> the web-search "API"
  * Telegram bot token
  * Discord bot token
  * Default model (provider + model)

Usage (interactive configuration screen):

    python3 run.py                 # First run auto-opens the screen
    python3 run.py setup          # Open the screen manually, then exit
    python3 run.py config         # Alias for `setup`
    /configure                    # From inside the running CLI

Usage (non-interactive, single command to configure everything):

    python3 run.py setup \
        --openai sk-... --google AIza... --anthropic sk-ant-... \
        --openrouter sk-or-... --grok xai-... --deepseek sk-... \
        --search serp-... \
        --telegram-token 123456:ABC --discord-token xyz \
        --provider openai --model gpt-4o

Run `python3 run.py setup --help` for the full list of flags.
"""

import argparse
import sys
from pathlib import Path

# Add parent directory to path so `config.settings` is importable whether this
# script is launched directly or via run.py / main.py.
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import Config

# ---------------------------------------------------------------------------
# Paths / mapping helpers
# ---------------------------------------------------------------------------

def get_env_path():
    """Get the path to the .env file used by Clio-Agent-2."""
    project_root = Path(__file__).parent.parent
    return project_root / "config" / ".env"


def set_env_value(key: str, value: str):
    """Set an environment variable in the .env file."""
    config = Config()
    success = config.save_to_env(key, value)

    if success:
        print(f"✅ {key} has been set!")
        return True
    else:
        print(f"❌ Failed to set {key}")
        return False


# Provider catalogue — the single source of truth is the LLM router's
# BUILTIN_PROVIDER_INFO, so this screen always matches the providers the agent
# can actually use.
from core.llm_router import BUILTIN_PROVIDER_INFO

# Provider -> environment variable mapping used for default model selection.
PROVIDER_ENV_VARS = {
    pid: info["env_var"] for pid, info in BUILTIN_PROVIDER_INFO.items()
}

# Human-friendly labels (in display order) for the large-scale model APIs.
LLM_PROVIDERS = list(BUILTIN_PROVIDER_INFO.keys())

# Sensible default model per provider (used as the suggested value).
SUGGESTED_MODELS = {
    pid: info["default_model"] for pid, info in BUILTIN_PROVIDER_INFO.items()
}

# Pretty display label for each provider.
PROVIDER_LABELS = {
    pid: info["label"] for pid, info in BUILTIN_PROVIDER_INFO.items()
}


def _resolve_provider(choice: str, configured: list) -> str:
    """Map a user's input (number or name) to one of the configured providers."""
    if not choice:
        return configured[0]
    if choice.isdigit():
        idx = int(choice) - 1
        if 0 <= idx < len(configured):
            return configured[idx]
    lowered = choice.lower()
    if lowered in PROVIDER_ENV_VARS and lowered in configured:
        return lowered
    matches = [p for p in configured if p.startswith(lowered)]
    if len(matches) == 1:
        return matches[0]
    return None


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def _status_line(label: str, ok: bool) -> str:
    return f"  {'✅' if ok else '❌'} {label}"


def print_status(config) -> None:
    """Render a compact status overview of every configurable item."""
    status = config.validate_api_keys()

    print("Current configuration:")
    print("-" * 48)
    # Large-scale model API keys.
    for p in LLM_PROVIDERS:
        print(_status_line(PROVIDER_LABELS.get(p, p), bool(status.get(p))))
    # Custom "Other" providers (user-supplied).
    custom = getattr(config, "custom_providers", []) or []
    if custom:
        print(_status_line("Other providers (custom)", True))
        for cp in custom:
            print(f"      • {cp.get('label') or cp['id']} ({cp['id']}) -> {cp['base_url']}")
    # Search API + bot tokens.
    print(_status_line("Search API", bool(status.get("search"))))
    print(_status_line("Telegram bot token", bool(status.get("telegram"))))
    print(_status_line("Discord bot token", bool(status.get("discord"))))
    # Default model.
    provider = (getattr(config, "default_llm_provider", "") or "").strip()
    model = (getattr(config, "current_model", "") or "").strip()
    if provider and model:
        print(f"  🤖 Default model: {provider} / {model}")
    else:
        print("  🤖 Default model: (not set)")
    print("-" * 48)


# ---------------------------------------------------------------------------
# Sub-flows
# ---------------------------------------------------------------------------

def configure_llm_keys(config) -> None:
    """Interactively set one or more large-scale model API keys."""
    while True:
        print()
        print("=" * 60)
        print("🧠 Large-scale model API keys (LLM providers)")
        print("=" * 60)
        status = config.validate_api_keys()
        for i, p in enumerate(LLM_PROVIDERS, start=1):
            mark = "✅" if status.get(p) else "❌"
            print(f"  {i}. {mark} {PROVIDER_LABELS.get(p, p)} ({p})")
        print(f"  {len(LLM_PROVIDERS) + 1}. Back")
        print()

        choice = input(
            f"Choose a provider to set (1-{len(LLM_PROVIDERS)} or name, "
            f"{len(LLM_PROVIDERS) + 1} to go back): "
        ).strip()
        if not choice:
            continue
        if choice.isdigit() and int(choice) == len(LLM_PROVIDERS) + 1:
            return

        # Resolve the chosen provider (number or name).
        provider = None
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(LLM_PROVIDERS):
                provider = LLM_PROVIDERS[idx]
        else:
            lowered = choice.lower()
            if lowered in PROVIDER_ENV_VARS:
                provider = lowered
            else:
                matches = [p for p in LLM_PROVIDERS if p.startswith(lowered)]
                if len(matches) == 1:
                    provider = matches[0]

        if not provider:
            print("⚠️  Invalid selection. Please try again.")
            continue

        key = input(
            f"Enter {PROVIDER_LABELS.get(provider, provider)} API key "
            f"(Enter to skip): "
        ).strip()
        if key:
            set_env_value(PROVIDER_ENV_VARS[provider], key)
            config.reload()  # refresh so the status display updates
            print(f"✅ {PROVIDER_LABELS.get(provider, provider)} API key saved!")


def configure_custom_provider(config) -> None:
    """Interactively add, edit or remove user-supplied "Other" providers."""
    while True:
        print()
        print("=" * 60)
        print("➕ Other providers (custom / OpenAI-compatible)")
        print("=" * 60)
        custom = config.load_custom_providers()
        if custom:
            for i, cp in enumerate(custom, start=1):
                print(f"  {i}. {cp.get('label') or cp['id']} ({cp['id']}) -> {cp['base_url']}")
        else:
            print("  (none configured)")
        print()
        print("  a. Add a new custom provider")
        if custom:
            print("  r. Remove a provider")
        print("  b. Back")
        print()

        choice = input("Action (a / r / b): ").strip().lower()
        if choice in ("b", "back", ""):
            return
        if choice in ("a", "add"):
            _add_custom_provider_flow(config)
        elif choice in ("r", "remove") and custom:
            _remove_custom_provider_flow(config, custom)
        else:
            print("⚠️  Invalid selection. Please try again.")


def _add_custom_provider_flow(config) -> None:
    """Walk the user through adding a single custom provider."""
    print()
    print("Add a custom provider. Any service that exposes an")
    print("OpenAI-compatible /chat/completions endpoint will work.")
    provider_id = input("Provider ID (lowercase, e.g. 'localai'): ").strip().lower()
    if not provider_id:
        print("⚠️  No ID provided.")
        return
    base_url = input("Base URL (e.g. http://localhost:1234/v1): ").strip()
    if not base_url:
        print("⚠️  No base URL provided.")
        return
    api_key = input("API key (Enter to skip if not required): ").strip()
    label = input("Display name (Enter to use the ID): ").strip()

    # Optional advanced settings for non-standard auth / endpoints.
    advanced = input(
        "Configure advanced settings (auth header / prefix / models path)? [y/N]: "
    ).strip().lower()
    auth_header = "Authorization"
    auth_prefix = "Bearer"
    models_path = "/models"
    default_model = ""
    if advanced == "y":
        auth_header = input("Auth header [Authorization]: ").strip() or "Authorization"
        auth_prefix = input("Auth prefix [Bearer]: ").strip() or "Bearer"
        models_path = input("Models path [/models]: ").strip() or "/models"
        default_model = input("Default model (optional): ").strip()

    try:
        config.add_custom_provider(
            provider_id, base_url, api_key=api_key, label=label,
            auth_header=auth_header, auth_prefix=auth_prefix,
            models_path=models_path, default_model=default_model,
        )
        config.reload()
        print(f"✅ Custom provider '{provider_id}' added!")
    except ValueError as e:
        print(f"❌ {e}")


def _remove_custom_provider_flow(config, custom) -> None:
    """Walk the user through removing one of the existing custom providers."""
    sel = input("Enter the number of the provider to remove: ").strip()
    if not sel.isdigit():
        print("⚠️  Invalid selection.")
        return
    idx = int(sel) - 1
    if not (0 <= idx < len(custom)):
        print("⚠️  Invalid selection.")
        return
    pid = custom[idx]["id"]
    if config.remove_custom_provider(pid):
        print(f"✅ Removed custom provider '{pid}'.")
    else:
        print("❌ Could not remove provider.")


def configure_search_api(config) -> None:
    """Interactively set the Search API key (web search)."""
    print()
    print("=" * 60)
    print("🔍 Search API key (used by web search)")
    print("=" * 60)
    key = input("Enter Search API key (Enter to skip): ").strip()
    if key:
        set_env_value("SEARCH_API_KEY", key)
        config.reload()
        print("✅ Search API key saved!")


def configure_telegram(config) -> None:
    """Interactively set the Telegram bot token."""
    print()
    print("=" * 60)
    print("📱 Telegram bot token")
    print("=" * 60)
    token = input("Enter Telegram Bot Token (Enter to skip): ").strip()
    if token:
        set_env_value("TELEGRAM_BOT_TOKEN", token)
        config.reload()
        print("✅ Telegram bot token saved!")


def configure_discord(config) -> None:
    """Interactively set the Discord bot token."""
    print()
    print("=" * 60)
    print("🎮 Discord bot token (Beta)")
    print("=" * 60)
    token = input("Enter Discord Bot Token (Enter to skip): ").strip()
    if token:
        set_env_value("DISCORD_BOT_TOKEN", token)
        config.reload()
        print("✅ Discord bot token saved!")


def configure_whatsapp(config) -> None:
    """Interactively set WhatsApp Business API credentials."""
    print()
    print("=" * 60)
    print("📱 WhatsApp Business API Configuration")
    print("=" * 60)
    print("This requires a Meta Developer App with WhatsApp Business API access.")
    print("Create an app at: https://developers.facebook.com/")
    print()

    # Phone Number ID
    phone_id = input("Enter WhatsApp Phone Number ID (Enter to skip): ").strip()
    if phone_id:
        set_env_value("WHATSAPP_PHONE_NUMBER_ID", phone_id)
        config.reload()
        print("✅ Phone Number ID saved!")

    # Access Token
    access_token = input("Enter WhatsApp Access Token (Enter to skip): ").strip()
    if access_token:
        set_env_value("WHATSAPP_ACCESS_TOKEN", access_token)
        config.reload()
        print("✅ Access Token saved!")

    # App Secret
    app_secret = input("Enter Meta App Secret (Enter to skip): ").strip()
    if app_secret:
        set_env_value("WHATSAPP_APP_SECRET", app_secret)
        config.reload()
        print("✅ App Secret saved!")

    # Webhook Verify Token
    verify_token = input("Enter Webhook Verify Token (Enter to skip): ").strip()
    if verify_token:
        set_env_value("WHATSAPP_WEBHOOK_VERIFY_TOKEN", verify_token)
        config.reload()
        print("✅ Webhook Verify Token saved!")

    # Webhook URL
    webhook_url = input("Enter Webhook URL (e.g., https://your-domain.com) (Enter to skip): ").strip()
    if webhook_url:
        set_env_value("WHATSAPP_WEBHOOK_URL", webhook_url)
        config.reload()
        print("✅ Webhook URL saved!")
        print("   Note: The webhook endpoint will be at: {webhook_url}/webhook")
        print("   For local testing, use ngrok: ngrok http 8080")

    # Webhook Port (optional)
    webhook_port = input("Enter Webhook Port [8080]: ").strip()
    if webhook_port:
        try:
            set_env_value("WHATSAPP_WEBHOOK_PORT", webhook_port)
            config.reload()
            print("✅ Webhook Port saved!")
        except ValueError:
            print("❌ Invalid port number, using default (8080)")


def configure_model(config):
    """Interactively pick the default LLM provider + model and persist to .env.

    Only providers whose API key is actually configured are offered. The chosen
    provider/model is written to config/.env (DEFAULT_LLM_PROVIDER /
    DEFAULT_MODEL), so the agent is ready to use after a single setup session.
    """
    status = config.validate_api_keys()
    configured = [p for p in LLM_PROVIDERS if status.get(p)]

    print()
    print("=" * 60)
    print("🤖 Default Model Selection")
    print("=" * 60)

    if not configured:
        print("No LLM API key is configured yet, so a default model can't be set.")
        print("Add at least one API key above, then re-run setup to choose a model.")
        return

    print("Configured providers:")
    for i, p in enumerate(configured, start=1):
        print(f"  {i}. {PROVIDER_LABELS.get(p, p)} ({p})")
    print()

    choice = input(f"Choose default provider (1-{len(configured)} or name) [1]: ").strip()
    provider = _resolve_provider(choice, configured)
    if not provider:
        print("⚠️  Invalid selection - skipping model configuration.")
        return

    suggestion = SUGGESTED_MODELS.get(provider, "")
    prompt = (
        f"Default model for {provider} [{suggestion}]: "
        if suggestion
        else f"Default model for {provider}: "
    )
    model = input(prompt).strip() or suggestion
    if not model:
        print("⚠️  No model provided - skipping.")
        return

    config.save_settings({
        "DEFAULT_LLM_PROVIDER": provider,
        "DEFAULT_MODEL": model,
    })
    print(f"\n✅ Default provider/model set to: {provider} / {model}")



# ---------------------------------------------------------------------------
# Main configuration screen
# ---------------------------------------------------------------------------

def interactive_setup(prompt_for_model: bool = True, overrides: dict = None):
    """Interactive, navigable configuration screen for Clio-Agent-2.

    Covers every secret/setting the agent needs: large-scale model API keys,
    the Search API key, Telegram/Discord bot tokens, and the default model.

    Args:
        prompt_for_model: When True (default) the default-model step runs at the
            end so the agent is ready to use after a single session.
        overrides: Optional mapping of {ENV_VAR: value} applied silently before
            the screen opens (used by the non-interactive single-command path).
    """
    print("=" * 60)
    print("🔧 Clio-Agent-2 Configuration")
    print("=" * 60)
    print()

    config = Config()

    if overrides:
        print("Applying provided values...")
        config.save_settings(overrides)
        config.reload()
        print("✅ Provided values saved.\n")

    while True:
        # Refresh status each loop so choices reflect earlier changes.
        print_status(config)
        print("What would you like to configure?")
        print("  1. Large-scale model API keys (OpenAI, Google, Anthropic,")
        print("     OpenRouter, Grok, DeepSeek, Mistral, Groq, Perplexity, ...)")
        print("  2. Search API key (web search)")
        print("  3. Telegram bot token")
        print("  4. Discord bot token (Beta)")
        print("  5. WhatsApp Business API (Phone Number ID, Access Token, etc.)")
        print("  6. Default model (provider + model)")
        print("  7. Other providers (add a custom / OpenAI-compatible provider)")
        print("  8. Done / Exit")
        print()

        choice = input("Enter choice (1-8): ").strip()

        if choice == "1":
            configure_llm_keys(config)
        elif choice == "2":
            configure_search_api(config)
        elif choice == "3":
            configure_telegram(config)
        elif choice == "4":
            configure_discord(config)
        elif choice == "5":
            configure_whatsapp(config)
        elif choice == "6":
            configure_model(config)
        elif choice == "7":
            configure_custom_provider(config)
        elif choice == "8":
            print("\n🎉 Setup complete!")
            break
        else:
            print("Invalid choice. Please try again.")
        print()

    # Optionally pick the default provider/model so the agent is ready to use.
    if prompt_for_model:
        try:
            configure_model(config)
        except (EOFError, KeyboardInterrupt):
            print("\n⚠️  Skipped model selection.")


# Backwards-compatible alias.
def configure_screen(prompt_for_model: bool = True, overrides: dict = None):
    """Alias for :func:`interactive_setup` (the configuration screen)."""
    return interactive_setup(prompt_for_model=prompt_for_model, overrides=overrides)


# ---------------------------------------------------------------------------
# Non-interactive, single-command configuration
# ---------------------------------------------------------------------------

def apply_overrides_from_argv(argv) -> bool:
    """Apply configuration from CLI flags and return True on success.

    Example:
        apply_overrides_from_argv(
            ["--openai", "sk-...", "--provider", "openai", "--model", "gpt-4o"]
        )
    """
    parser = argparse.ArgumentParser(
        description="Configure Clio-Agent-2 from the command line (non-interactive).",
        add_help=True,
    )
    for p in LLM_PROVIDERS:
        parser.add_argument(
            f"--{p}", dest=f"key_{p}", default=None,
            help=f"Set the {PROVIDER_LABELS.get(p, p)} API key",
        )
    parser.add_argument("--search", dest="key_search", default=None,
                        help="Set the Search API key")
    # `--telegram-token` / `--discord-token` (the bare `--telegram` /
    # `--discord` flags are owned by main.py, which launches the bots).
    parser.add_argument("--telegram-token", dest="telegram", default=None,
                        help="Set the Telegram bot token")
    parser.add_argument("--discord-token", dest="discord", default=None,
                        help="Set the Discord bot token")
    parser.add_argument("--whatsapp-phone-number-id", dest="whatsapp_phone_number_id", default=None,
                        help="Set the WhatsApp Phone Number ID")
    parser.add_argument("--whatsapp-access-token", dest="whatsapp_access_token", default=None,
                        help="Set the WhatsApp Access Token")
    parser.add_argument("--whatsapp-app-secret", dest="whatsapp_app_secret", default=None,
                        help="Set the WhatsApp App Secret")
    parser.add_argument("--whatsapp-webhook-verify-token", dest="whatsapp_webhook_verify_token", default=None,
                        help="Set the WhatsApp Webhook Verify Token")
    parser.add_argument("--whatsapp-webhook-url", dest="whatsapp_webhook_url", default=None,
                        help="Set the WhatsApp Webhook URL")
    parser.add_argument("--whatsapp-webhook-port", dest="whatsapp_webhook_port", default=None,
                        help="Set the WhatsApp Webhook Port (default: 8080)")
    parser.add_argument("--provider", dest="provider", default=None,
                        help="Default LLM provider (e.g. openai)")
    parser.add_argument("--model", dest="model", default=None,
                        help="Default LLM model (e.g. gpt-4o)")
    parser.add_argument(
        "--custom", dest="custom", default=None,
        help=(
            "Add custom 'Other' providers as a JSON array, e.g. "
            "'[{\"id\":\"localai\",\"base_url\":\"http://localhost:1234/v1\"}]'"
        ),
    )

    args = parser.parse_args(argv)

    config = Config()
    overrides = {}

    for p in LLM_PROVIDERS:
        val = getattr(args, f"key_{p}")
        if val:
            overrides[PROVIDER_ENV_VARS[p]] = val
    if args.key_search:
        overrides["SEARCH_API_KEY"] = args.key_search
    if args.telegram:
        overrides["TELEGRAM_BOT_TOKEN"] = args.telegram
    if args.discord:
        overrides["DISCORD_BOT_TOKEN"] = args.discord
    if args.whatsapp_phone_number_id:
        overrides["WHATSAPP_PHONE_NUMBER_ID"] = args.whatsapp_phone_number_id
    if args.whatsapp_access_token:
        overrides["WHATSAPP_ACCESS_TOKEN"] = args.whatsapp_access_token
    if args.whatsapp_app_secret:
        overrides["WHATSAPP_APP_SECRET"] = args.whatsapp_app_secret
    if args.whatsapp_webhook_verify_token:
        overrides["WHATSAPP_WEBHOOK_VERIFY_TOKEN"] = args.whatsapp_webhook_verify_token
    if args.whatsapp_webhook_url:
        overrides["WHATSAPP_WEBHOOK_URL"] = args.whatsapp_webhook_url
    if args.whatsapp_webhook_port:
        overrides["WHATSAPP_WEBHOOK_PORT"] = args.whatsapp_webhook_port

    # Custom "Other" providers (JSON array). These are written via
    # add_custom_provider() so they are persisted alongside the built-ins.
    if args.custom:
        import json
        try:
            items = json.loads(args.custom)
        except Exception as e:
            print(f"❌ Invalid --custom JSON: {e}")
            return False
        added = 0
        for item in items:
            pid = item.get("id")
            base_url = item.get("base_url")
            if not pid or not base_url:
                print(f"⚠️  Skipping custom provider without id/base_url: {item}")
                continue
            config.add_custom_provider(
                pid, base_url,
                api_key=item.get("api_key", ""),
                label=item.get("label", ""),
                auth_header=item.get("auth_header", "Authorization"),
                auth_prefix=item.get("auth_prefix", "Bearer"),
                models_path=item.get("models_path", "/models"),
                default_model=item.get("default_model", ""),
            )
            added += 1
        if added:
            print(f"✅ Configured {added} custom provider(s).")

    if overrides:
        config.save_settings(overrides)
        print(f"✅ Saved {len(overrides)} setting(s) to {config.get_env_path()}")

    if args.provider or args.model:
        model_settings = {}
        if args.provider:
            model_settings["DEFAULT_LLM_PROVIDER"] = args.provider
        if args.model:
            model_settings["DEFAULT_MODEL"] = args.model
        config.save_settings(model_settings)
        print(
            "✅ Default model set to: "
            f"{args.provider or '(unchanged)'} / {args.model or '(unchanged)'}"
        )

    if not overrides and not (args.provider or args.model):
        print("ℹ️  No configuration flags provided; nothing changed.")
        parser.print_help()
        return False

    return True



# ---------------------------------------------------------------------------
# Standalone entry point (legacy - use `python3 run.py setup` instead)
# ---------------------------------------------------------------------------

def main():
    """Entry point when run directly (legacy; prefer `python3 run.py setup`)."""
    env_path = get_env_path()

    if not env_path.exists():
        print(f"⚠️  .env file not found at {env_path}")
        print("Creating from template...")
        example_path = env_path.parent / ".env.example"
        if example_path.exists():
            with open(example_path) as f:
                content = f.read()
            with open(env_path, 'w') as f:
                f.write(content)
            print("✅ Created .env file from template")
        else:
            with open(env_path, 'w') as f:
                f.write("# Clio-Agent-2 Configuration\n")
            print("✅ Created empty .env file")
        print()

    # Non-interactive mode: any recognised flag => apply and exit.
    non_interactive_flags = (
        [f"--{p}" for p in LLM_PROVIDERS]
        + ["--search", "--telegram-token", "--discord-token",
           "--provider", "--model", "-h", "--help"]
    )
    has_flag = any(arg in non_interactive_flags for arg in sys.argv[1:])

    if has_flag:
        apply_overrides_from_argv(sys.argv[1:])
        return

    # Backwards-compatible single-token shortcuts.
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()

        if arg in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        elif arg == "--telegram":
            token = input("Enter Telegram Bot Token: ").strip()
            if token:
                set_env_value("TELEGRAM_BOT_TOKEN", token)
            else:
                print("❌ No token provided")
                sys.exit(1)
            return
        elif arg == "--discord":
            token = input("Enter Discord Bot Token: ").strip()
            if token:
                set_env_value("DISCORD_BOT_TOKEN", token)
            else:
                print("❌ No token provided")
                sys.exit(1)
            return
        elif arg == "--openai":
            key = input("Enter OpenAI API Key: ").strip()
            if key:
                set_env_value("OPENAI_API_KEY", key)
            else:
                print("❌ No key provided")
                sys.exit(1)
            return
        elif arg == "--google":
            key = input("Enter Google API Key: ").strip()
            if key:
                set_env_value("GOOGLE_API_KEY", key)
            else:
                print("❌ No key provided")
                sys.exit(1)
            return
        elif arg == "--anthropic":
            key = input("Enter Anthropic API Key: ").strip()
            if key:
                set_env_value("ANTHROPIC_API_KEY", key)
            else:
                print("❌ No key provided")
                sys.exit(1)
            return
        elif arg == "--search":
            key = input("Enter Search API Key: ").strip()
            if key:
                set_env_value("SEARCH_API_KEY", key)
            else:
                print("❌ No key provided")
                sys.exit(1)
            return
        else:
            print(f"Unknown argument: {arg}")
            print("Use --help for usage information")
            sys.exit(1)

    # Default: interactive configuration screen.
    interactive_setup(prompt_for_model=True)


if __name__ == "__main__":
    main()

