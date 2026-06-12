"""
Unified Provider & Model Configuration Flow

Flow:
1. User selects provider
2. If cloud provider, user enters API key (or confirms existing one)
3. Models are fetched LIVE from the provider's API
4. User selects from the live model list, or chooses "Custom Model" to type any name
5. Configuration is saved to settings & config.yaml

Every provider includes a "Custom Model" option so users can enter any model name
that the provider supports, even if it's not in the fetched list.
"""

import os
import sys
import subprocess
import curses
from typing import Optional, List, Dict, Tuple
from pathlib import Path

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
COLOR_TITLE = 1
COLOR_HIGHLIGHT = 2
COLOR_NORMAL = 3
COLOR_FOOTER = 4
COLOR_ERROR = 5
COLOR_FILTER = 6


def _setup_colors():
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(COLOR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
        curses.init_pair(COLOR_HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        curses.init_pair(COLOR_NORMAL, curses.COLOR_WHITE, curses.COLOR_BLACK)
        curses.init_pair(COLOR_FOOTER, curses.COLOR_YELLOW, curses.COLOR_BLACK)
        curses.init_pair(COLOR_ERROR, curses.COLOR_RED, curses.COLOR_BLACK)
        curses.init_pair(COLOR_FILTER, curses.COLOR_GREEN, curses.COLOR_BLACK)


def _attr(pair, bold=False):
    if curses.has_colors():
        return curses.color_pair(pair) | (curses.A_BOLD if bold else 0)
    return curses.A_BOLD if bold else 0


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------
PROVIDERS = {
    "ollama": {
        "name": "Ollama (Local)",
        "icon": "O",
        "description": "Run models locally via Ollama - Privacy-focused",
        "needs_key": False,
    },
    "google": {
        "name": "Google Gemini",
        "icon": "G",
        "description": "Google Gemini API - Enterprise-grade",
        "needs_key": True,
        "env_var": "GOOGLE_API_KEY",
    },
    "openai": {
        "name": "OpenAI",
        "icon": "O",
        "description": "OpenAI GPT API - Advanced capabilities",
        "needs_key": True,
        "env_var": "OPENAI_API_KEY",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "icon": "A",
        "description": "Anthropic Claude API - Strong reasoning",
        "needs_key": True,
        "env_var": "ANTHROPIC_API_KEY",
    },
    "deepseek": {
        "name": "DeepSeek",
        "icon": "D",
        "description": "DeepSeek API - Advanced reasoning",
        "needs_key": True,
        "env_var": "DEEPSEEK_API_KEY",
    },
    "groq": {
        "name": "Groq",
        "icon": "G",
        "description": "Groq API - Ultra-low latency inference",
        "needs_key": True,
        "env_var": "GROQ_API_KEY",
    },
    "mistral": {
        "name": "Mistral AI",
        "icon": "M",
        "description": "Mistral API - Multilingual European AI",
        "needs_key": True,
        "env_var": "MISTRAL_API_KEY",
    },
    "xai": {
        "name": "xAI Grok",
        "icon": "X",
        "description": "xAI Grok API - Real-time knowledge",
        "needs_key": True,
        "env_var": "XAI_API_KEY",
    },
    "meta": {
        "name": "Meta",
        "icon": "M",
        "description": "Meta API - Llama models",
        "needs_key": True,
        "env_var": "META_API_KEY",
    },
    "cohere": {
        "name": "Cohere",
        "icon": "C",
        "description": "Cohere API - Enterprise language models",
        "needs_key": True,
        "env_var": "COHERE_API_KEY",
    },
    "openrouter": {
        "name": "OpenRouter",
        "icon": "O",
        "description": "OpenRouter API - Access 300+ AI models",
        "needs_key": True,
        "env_var": "OPENROUTER_API_KEY",
    },
    "together": {
        "name": "Together AI",
        "icon": "T",
        "description": "Together AI API - Open-source model hosting",
        "needs_key": True,
        "env_var": "TOGETHER_API_KEY",
    },
    "minimax": {
        "name": "MiniMax",
        "icon": "M",
        "description": "MiniMax API - Productivity models",
        "needs_key": True,
        "env_var": "MINIMAX_API_KEY",
    },
    "zhipuai": {
        "name": "Zhipu AI",
        "icon": "Z",
        "description": "Zhipu AI GLM API",
        "needs_key": True,
        "env_var": "ZHIPUAI_API_KEY",
    },
    "microsoft": {
        "name": "Microsoft Azure",
        "icon": "M",
        "description": "Azure OpenAI API - Enterprise cloud",
        "needs_key": True,
        "env_var": "AZURE_API_KEY",
    },
    "amazon": {
        "name": "Amazon Bedrock",
        "icon": "A",
        "description": "AWS Bedrock API - Enterprise models",
        "needs_key": True,
        "env_var": "AWS_ACCESS_KEY_ID",
    },
}

PROVIDER_API_KEY_ENV_VARS = {
    "google": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "xai": "XAI_API_KEY",
    "meta": "META_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "microsoft": "AZURE_API_KEY",
    "azure": "AZURE_API_KEY",
    "amazon": "AWS_ACCESS_KEY_ID",
    "cohere": "COHERE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "together": "TOGETHER_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "zhipuai": "ZHIPUAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

# Sentinel ID for the "Custom Model" option
CUSTOM_MODEL_ID = "__custom_model__"


# ---------------------------------------------------------------------------
# Scrolling curses list helper
# ---------------------------------------------------------------------------
def _scrolling_list(stdscr, items: list, title: str, subtitle: str = "") -> Optional[int]:
    """
    Display a scrollable list and return the selected index, or None if cancelled.
    Each item: {id, name, description, icon?}
    Supports real-time filtering: type characters to filter, backspace to delete.
    """
    curses.curs_set(0)
    _setup_colors()

    current = 0
    scroll_offset = 0
    filter_text = ""

    def _get_filtered():
        if not filter_text:
            return list(range(len(items)))
        ft = filter_text.lower()
        result = []
        for i in range(len(items)):
            item = items[i]
            name = item.get("name", item.get("id", ""))
            item_id = item.get("id", "")
            desc = item.get("description", "")
            if ft in name.lower() or ft in item_id.lower() or ft in desc.lower():
                result.append(i)
        return result

    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()

        filtered_indices = _get_filtered()

        # Reset current if out of bounds
        if current >= len(filtered_indices):
            current = max(0, len(filtered_indices) - 1)

        header_lines = 4 if subtitle else 3
        # Add extra line for filter display when active
        filter_height = 1 if filter_text else 0
        footer_lines = 2
        available = max_y - header_lines - filter_height - footer_lines
        per_page = max(1, available // 3)

        if current < scroll_offset:
            scroll_offset = current
        elif current >= scroll_offset + per_page:
            scroll_offset = current - per_page + 1
        if filtered_indices:
            scroll_offset = max(0, min(scroll_offset, len(filtered_indices) - 1))
        else:
            scroll_offset = 0

        stdscr.addstr(0, 0, title[:max_x - 1], _attr(COLOR_TITLE, True))
        sep = "=" * min(50, max_x - 1)
        stdscr.addstr(1, 0, sep, _attr(COLOR_TITLE))
        if subtitle:
            stdscr.addstr(2, 0, subtitle[:max_x - 1], _attr(COLOR_NORMAL))

        # Show filter status line
        filter_y = header_lines
        if filter_text:
            total = len(items)
            showing = len(filtered_indices)
            filter_display = "  Filter: '%s_'  %d/%d" % (filter_text, showing, total)
            stdscr.addstr(filter_y, 0, filter_display[:max_x - 1], _attr(COLOR_FILTER, True))
        else:
            stdscr.addstr(filter_y, 0, "  Type to search"[:max_x - 1], _attr(COLOR_FOOTER))

        stdscr.addstr(max_y - 1, 0, "Arrows:Navigate  Type:Search  Enter:Select  BS:Clear  Q:Quit", _attr(COLOR_FOOTER))

        start_y = header_lines + 1 + filter_height
        vis_start = scroll_offset
        vis_end = min(scroll_offset + per_page, len(filtered_indices))

        if scroll_offset > 0 and start_y > 1:
            stdscr.addstr(start_y - 1, 0, "  ^ More ^", _attr(COLOR_FOOTER))

        for di in range(vis_end - vis_start):
            list_idx = vis_start + di
            if list_idx >= len(filtered_indices):
                break
            idx = filtered_indices[list_idx]
            y = start_y + di * 3
            if y >= max_y - 3:
                break
            item = items[idx]
            icon = item.get("icon", "")
            name = item.get("name", item.get("id", ""))
            desc = item.get("description", "")
            sel = (list_idx == current)
            prefix = "> " if sel else "  "
            l1 = "%s%s %s" % (prefix, icon, name)
            l2 = "    %s" % desc
            stdscr.addstr(y, 0, l1[:max_x - 1], _attr(COLOR_HIGHLIGHT, True) if sel else _attr(COLOR_NORMAL))
            stdscr.addstr(y + 1, 0, l2[:max_x - 1], _attr(COLOR_HIGHLIGHT) if sel else _attr(COLOR_NORMAL))

        if vis_end < len(filtered_indices):
            iy = start_y + (vis_end - vis_start) * 3
            if iy < max_y - 2:
                stdscr.addstr(iy, 0, "  v More v", _attr(COLOR_FOOTER))

        stdscr.refresh()
        key = stdscr.getch()

        if key == curses.KEY_UP and current > 0:
            current -= 1
        elif key == curses.KEY_DOWN and current < len(filtered_indices) - 1:
            current += 1
        elif key == curses.KEY_PPAGE:
            current = max(0, current - per_page)
        elif key == curses.KEY_NPAGE:
            if filtered_indices:
                current = min(len(filtered_indices) - 1, current + per_page)
            else:
                current = 0
        elif key == curses.KEY_HOME:
            current = 0
        elif key == curses.KEY_END:
            current = max(0, len(filtered_indices) - 1)
        elif key in (10, 13):
            if filtered_indices:
                return filtered_indices[current]
            return None
        elif key in (ord('q'), ord('Q'), 27):
            return None
        elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:
            if filter_text:
                filter_text = filter_text[:-1]
                current = 0
                scroll_offset = 0
        elif 32 <= key <= 126:
            if len(filter_text) < 50:
                filter_text += chr(key)
                current = 0
                scroll_offset = 0

# ---------------------------------------------------------------------------
# Custom model name input
# ---------------------------------------------------------------------------
def _input_custom_model(stdscr, provider_name: str) -> Optional[str]:
    """Let user type a custom model name. Returns name or None."""
    curses.curs_set(1)
    _setup_colors()

    text = ""
    error_msg = ""

    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()

        stdscr.addstr(0, 0, "Custom Model for %s" % provider_name, _attr(COLOR_TITLE, True))
        stdscr.addstr(1, 0, "=" * min(50, max_x - 1), _attr(COLOR_TITLE))
        stdscr.addstr(2, 0, "Enter any model name supported by this provider.", _attr(COLOR_NORMAL))
        stdscr.addstr(3, 0, "This will be used directly - no validation is performed.", _attr(COLOR_NORMAL))

        if provider_key_hint(provider_name):
            stdscr.addstr(4, 0, "Example: %s" % provider_key_hint(provider_name), _attr(COLOR_FOOTER))

        if error_msg:
            stdscr.addstr(6, 0, error_msg[:max_x - 1], _attr(COLOR_ERROR))

        y = 8 if error_msg else 6
        stdscr.addstr(y, 0, "Model name: ", _attr(COLOR_NORMAL))
        stdscr.addstr(y, 12, text, _attr(COLOR_NORMAL))
        stdscr.move(y, 12 + len(text))

        stdscr.addstr(max_y - 1, 0, "Enter:Confirm  Ctrl+C:Cancel", _attr(COLOR_FOOTER))
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == 27 or ch == 3:
            return None
        elif ch in (10, 13):
            if not text.strip():
                error_msg = "Model name cannot be empty."
                continue
            return text.strip()
        elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
            text = text[:-1]
            error_msg = ""
        elif 32 <= ch <= 126:
            text += chr(ch)
            error_msg = ""


def provider_key_hint(provider_name: str) -> str:
    """Return an example model name for a provider."""
    hints = {
        "openai": "gpt-4o, o3, gpt-4.1",
        "google": "gemini-2.5-pro, gemini-2.0-flash",
        "anthropic": "claude-sonnet-4-20250514",
        "deepseek": "deepseek-chat, deepseek-reasoner",
        "groq": "llama-3.3-70b-versatile",
        "mistral": "mistral-large-latest",
        "xai": "grok-4-0709",
        "meta": "llama-4-scout-17b-16e-instruct",
        "cohere": "command-r-plus",
        "openrouter": "openai/gpt-4o, anthropic/claude-sonnet-4",
        "together": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
        "minimax": "MiniMax-Text-01",
        "zhipuai": "glm-4-plus",
        "microsoft": "gpt-4o",
        "amazon": "anthropic.claude-sonnet-4-20250514-v1:0",
        "ollama": "llama3.2, qwen3:8b",
    }
    return hints.get(provider_name, "")


# ---------------------------------------------------------------------------
# Provider selection
# ---------------------------------------------------------------------------
def select_provider(stdscr=None) -> Optional[str]:
    """Let user select a provider. Returns provider key or None."""
    items = []
    for key in sorted(PROVIDERS.keys()):
        p = PROVIDERS[key]
        items.append({"id": key, "icon": p["icon"], "name": p["name"], "description": p["description"]})

    if stdscr:
        idx = _scrolling_list(stdscr, items, "Select AI Provider", "Choose your AI provider:")
        return items[idx]["id"] if idx is not None else None
    else:
        return curses.wrapper(lambda s: select_provider(s))


# ---------------------------------------------------------------------------
# API key input
# ---------------------------------------------------------------------------
def _input_api_key(stdscr, provider_name: str, existing_key: Optional[str] = None) -> Optional[str]:
    """Curses-based API key input. Returns key string or None to cancel."""
    curses.curs_set(1)
    _setup_colors()

    key_input = existing_key if existing_key else ""
    error_msg = ""

    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()

        stdscr.addstr(0, 0, "%s API Key" % provider_name, _attr(COLOR_TITLE, True))
        stdscr.addstr(1, 0, "=" * min(50, max_x - 1), _attr(COLOR_TITLE))
        stdscr.addstr(2, 0, "Enter your API key to fetch available models.", _attr(COLOR_NORMAL))
        stdscr.addstr(3, 0, "(Leave empty and press Enter to cancel)", _attr(COLOR_NORMAL))

        if error_msg:
            stdscr.addstr(5, 0, error_msg[:max_x - 1], _attr(COLOR_ERROR))

        y = 7 if error_msg else 5
        masked = "*" * len(key_input) if key_input else ""
        # Clamp masked length and cursor position to screen width
        max_cursor_x = max_x - 1
        if len(masked) > max_cursor_x - 9:
            masked = masked[:max_cursor_x - 9]
        stdscr.addstr(y, 0, "API Key: ", _attr(COLOR_NORMAL))
        stdscr.addstr(y, 9, masked, _attr(COLOR_NORMAL))
        stdscr.move(y, min(9 + len(masked), max_cursor_x))

        stdscr.addstr(max_y - 1, 0, "Enter:Confirm  Ctrl+C:Cancel", _attr(COLOR_FOOTER))
        stdscr.refresh()

        ch = stdscr.getch()
        if ch == 27 or ch == 3:
            return None
        elif ch in (10, 13):
            if not key_input.strip():
                return None
            if len(key_input.strip()) < 10:
                error_msg = "API key seems too short. Please check and try again."
                continue
            return key_input.strip()
        elif ch == curses.KEY_BACKSPACE or ch == 127 or ch == 8:
            key_input = key_input[:-1]
            error_msg = ""
        elif 32 <= ch <= 126:
            key_input += chr(ch)
            error_msg = ""


def get_api_key(provider_key: str, stdscr=None) -> Optional[str]:
    """
    Get API key for a provider. Checks env vars first, then settings,
    then prompts the user. Returns key or None.
    """
    env_var = PROVIDER_API_KEY_ENV_VARS.get(provider_key, "")

    # Check environment variable
    existing = os.getenv(env_var) if env_var else None
    if existing:
        return existing

    # Check settings manager
    try:
        from ai_agent.utils.settings_manager import get_settings_manager
        mgr = get_settings_manager()
        existing = mgr.get_api_key(provider_key)
        if existing:
            return existing
    except Exception:
        pass

    # No key found - prompt the user
    name = PROVIDERS.get(provider_key, {}).get("name", provider_key)

    if stdscr:
        return _input_api_key(stdscr, name, None)
    else:
        return curses.wrapper(lambda s: _input_api_key(s, name, None))


# ---------------------------------------------------------------------------
# Live model fetching
# ---------------------------------------------------------------------------
def fetch_models_ollama() -> Optional[List[Dict]]:
    """Fetch installed models from Ollama."""
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        if resp.status_code != 200:
            return None
        data = resp.json()
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            if name:
                size = m.get("size", 0)
                size_gb = size / (1024 ** 3) if size else 0
                models.append({
                    "id": name,
                    "name": name,
                    "description": "Size: %.1f GB" % size_gb if size_gb else "Local model",
                    "icon": "O",
                })
        return models if models else None
    except Exception:
        return None


def _model_desc(m) -> str:
    """Build a model description with context window and pricing."""
    parts = []
    ctx = getattr(m, "context_window", None)
    if ctx:
        parts.append("ctx:%d" % ctx)
    # Known pricing per 1M tokens (input, output)
    _pricing = {
        # Google
        "gemini-2.5-pro": (1.25, 10.0),
        "gemini-2.5-flash": (0.075, 0.30),
        "gemini-2.0-flash": (0.075, 0.30),
        "gemini-1.5-pro": (1.25, 10.0),
        "gemini-1.5-flash": (0.075, 0.30),
        # OpenAI
        "gpt-4o": (2.50, 10.0),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4-turbo": (10.0, 30.0),
        "gpt-4": (30.0, 60.0),
        "gpt-3.5-turbo": (0.50, 1.50),
        "o1": (15.0, 60.0),
        "o3": (2.0, 8.0),
        "o3-mini": (1.10, 4.40),
        "o4-mini": (1.10, 4.40),
    }
    mid = m.id
    price = None
    if mid in _pricing:
        price = _pricing[mid]
    else:
        for prefix, p in _pricing.items():
            if mid.startswith(prefix):
                price = p
                break
    if price:
        parts.append("in:$%.2f/M out:$%.2f/M" % (price[0], price[1]))
    if parts:
        return " ".join(parts)
    return getattr(m, "description", None) or mid


def fetch_models_from_api(provider_key: str, api_key: str) -> Optional[List[Dict]]:
    """Fetch models from a cloud provider's API. Returns None on failure."""
    try:
        if provider_key == "google":
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "peripherals" / "api"))
            from api.google_client import GoogleLLMClient
            client = GoogleLLMClient(api_key=api_key)
            infos = client.list_models()
            return [{"id": m.id, "name": m.id, "description": _model_desc(m), "icon": "G"} for m in infos]

        elif provider_key == "openai":
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "peripherals" / "api"))
            from api.openai_client import OpenAILLMClient
            client = OpenAILLMClient(api_key=api_key)
            infos = client.list_models()
            return [{"id": m.id, "name": m.id, "description": _model_desc(m), "icon": "O"} for m in infos]

        elif provider_key == "openrouter":
            import requests
            headers = {"Authorization": "Bearer %s" % api_key}
            resp = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=10)
            if resp.status_code != 200:
                return None
            data = resp.json()
            models = []
            for m in data.get("data", []):
                mid = m.get("id", "")
                if not mid:
                    continue
                ctx = m.get("context_length", 0)
                pricing = m.get("pricing", {})
                prompt_price = float(pricing.get("prompt", 0)) * 1_000_000
                output_price = float(pricing.get("completion", 0)) * 1_000_000
                if ctx:
                    desc = "ctx:%d in:$%.2f/M out:$%.2f/M" % (ctx, prompt_price, output_price)
                else:
                    desc = "in:$%.2f/M out:$%.2f/M" % (prompt_price, output_price)
                models.append({"id": mid, "name": mid, "description": desc, "icon": "O"})
            models.sort(key=lambda m: m["id"])
            return models if models else None

        elif provider_key == "together":
            import requests
            headers = {"Authorization": "Bearer %s" % api_key}
            resp = requests.get("https://api.together.xyz/v1/models", headers=headers, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                models = []
                for m in data:
                    mid = m.get("id", "")
                    if mid and m.get("type") == "chat":
                        ctx = m.get("context_length", 0)
                        pricing = m.get("pricing", {})
                        input_price = float(pricing.get("input", 0))
                        output_price = float(pricing.get("output", 0))
                        if ctx:
                            desc = "ctx:%d in:$%.2f/M out:$%.2f/M" % (ctx, input_price, output_price)
                        else:
                            desc = "in:$%.2f/M out:$%.2f/M" % (input_price, output_price)
                        models.append({"id": mid, "name": mid, "description": desc, "icon": "T"})
                models.sort(key=lambda m: m["id"])
                return models if models else None
            return None

        elif provider_key == "anthropic":
            return [
                {"id": "claude-opus-4-20250514", "name": "Claude Opus 4", "description": "Most capable model", "icon": "A"},
                {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "description": "Balanced performance", "icon": "A"},
                {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "description": "Strong performance", "icon": "A"},
                {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku", "description": "Fast & efficient", "icon": "A"},
                {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus", "description": "Legacy powerful model", "icon": "A"},
            ]

        elif provider_key == "groq":
            return [
                {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "description": "Ultra-fast on Groq", "icon": "G"},
                {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B", "description": "Low latency", "icon": "G"},
                {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B", "description": "Fast on Groq", "icon": "G"},
                {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B", "description": "MoE architecture", "icon": "G"},
                {"id": "gemma2-9b-it", "name": "Gemma 2 9B", "description": "Fast inference", "icon": "G"},
                {"id": "llama-guard-3-8b", "name": "Llama Guard 3 8B", "description": "Safety classifier", "icon": "G"},
            ]

        elif provider_key == "mistral":
            return [
                {"id": "mistral-large-latest", "name": "Mistral Large 2", "description": "Flagship 123B model", "icon": "M"},
                {"id": "mistral-medium-latest", "name": "Mistral Medium", "description": "Balanced performance", "icon": "M"},
                {"id": "mistral-small-latest", "name": "Mistral Small", "description": "Fast & efficient", "icon": "M"},
                {"id": "ministral-3b-latest", "name": "Ministral 3B", "description": "Edge deployment", "icon": "M"},
                {"id": "ministral-8b-latest", "name": "Ministral 8B", "description": "Edge deployment", "icon": "M"},
                {"id": "mixtral-8x7b-instruct", "name": "Mixtral 8x7B", "description": "MoE instruction", "icon": "M"},
                {"id": "codestral-latest", "name": "Codestral", "description": "Code generation", "icon": "M"},

            ]

        elif provider_key == "deepseek":
            return [
                {"id": "deepseek-chat", "name": "DeepSeek Chat", "description": "General conversation", "icon": "D"},
                {"id": "deepseek-coder", "name": "DeepSeek Coder", "description": "Code specialist", "icon": "D"},
                {"id": "deepseek-reasoner", "name": "DeepSeek R1", "description": "Advanced reasoning", "icon": "D"},
            ]

        elif provider_key == "xai":
            return [
                {"id": "grok-4-0709", "name": "Grok 4", "description": "Latest, #1 on LMArena", "icon": "X"},
                {"id": "grok-4-fast", "name": "Grok 4 Fast", "description": "Quick responses", "icon": "X"},
                {"id": "grok-3", "name": "Grok 3", "description": "Previous generation", "icon": "X"},
                {"id": "grok-3-mini", "name": "Grok 3 Mini", "description": "Lightweight", "icon": "X"},
            ]

        elif provider_key == "cohere":
            return [
                {"id": "command-r-plus", "name": "Command R+", "description": "Best RAG model", "icon": "C"},
                {"id": "command-r", "name": "Command R", "description": "Balanced", "icon": "C"},
                {"id": "command", "name": "Command", "description": "Legacy model", "icon": "C"},
                {"id": "command-r7b-12-2024", "name": "Command R7B", "description": "Compact 7B", "icon": "C"},
            ]

        elif provider_key == "minimax":
            return [
                {"id": "MiniMax-Text-01", "name": "MiniMax Text-01", "description": "Latest general model", "icon": "M"},
                {"id": "MiniMax-M2.5", "name": "MiniMax M2.5", "description": "Coding & productivity", "icon": "M"},
                {"id": "abab6.5s", "name": "ABAB 6.5S", "description": "Chat model", "icon": "M"},
            ]

        elif provider_key == "zhipuai":
            return [
                {"id": "glm-4-plus", "name": "GLM-4 Plus", "description": "Strong general performance", "icon": "Z"},
                {"id": "glm-4", "name": "GLM-4", "description": "Base model", "icon": "Z"},

                {"id": "glm-4-air", "name": "GLM-4 Air", "description": "Lightweight", "icon": "Z"},
                {"id": "glm-4-flash", "name": "GLM-4 Flash", "description": "Free tier", "icon": "Z"},
            ]

        elif provider_key == "microsoft":
            return [
                {"id": "gpt-4o", "name": "GPT-4o (Azure)", "description": "Azure hosted GPT-4o", "icon": "M"},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini (Azure)", "description": "Azure hosted", "icon": "M"},
                {"id": "gpt-4", "name": "GPT-4 (Azure)", "description": "Azure hosted GPT-4", "icon": "M"},
                {"id": "o3-mini", "name": "O3 Mini (Azure)", "description": "Reasoning model", "icon": "M"},
            ]

        elif provider_key == "amazon":
            return [
                {"id": "anthropic.claude-sonnet-4-20250514-v1:0", "name": "Claude Sonnet 4", "description": "AWS hosted", "icon": "A"},
                {"id": "anthropic.claude-opus-4-20250514-v1:0", "name": "Claude Opus 4", "description": "AWS hosted", "icon": "A"},
                {"id": "amazon.nova-pro-v1:0", "name": "Nova Pro", "description": "AWS native model", "icon": "A"},
                {"id": "amazon.nova-lite-v1:0", "name": "Nova Lite", "description": "AWS native fast", "icon": "A"},
                {"id": "meta.llama4-scout-17b-16e-instruct-v1:0", "name": "Llama 4 Scout", "description": "AWS hosted", "icon": "A"},
                {"id": "meta.llama4-maverick-17b-128e-instruct-v1:0", "name": "Llama 4 Maverick", "description": "AWS hosted", "icon": "A"},
            ]

        else:
            return None

    except Exception as e:
        sys.stderr.write("Error fetching models for %s: %s\n" % (provider_key, e))
        return None


# ---------------------------------------------------------------------------
# Model selection (with Custom Model option)
# ---------------------------------------------------------------------------
def select_model(stdscr, models: List[Dict], provider_name: str) -> Optional[str]:
    """
    Let user select a model from a list. The list always includes a
    "Custom Model" entry at the end. Returns model ID or None.
    If user selects "Custom Model", prompts for a custom name.
    """
    # Append "Custom Model" option
    display_models = list(models) + [{
        "id": CUSTOM_MODEL_ID,
        "name": "Custom Model",
        "description": "Enter any model name manually",
        "icon": "?",
    }]

    idx = _scrolling_list(
        stdscr, display_models,
        "%s Models" % provider_name,
        "Choose a model (%d available + custom):" % len(models)
    )

    if idx is None:
        return None

    selected = display_models[idx]
    if selected["id"] == CUSTOM_MODEL_ID:
        return _input_custom_model(stdscr, provider_name)

    return selected["id"]


# ---------------------------------------------------------------------------
# Ollama check
# ---------------------------------------------------------------------------
def check_ollama_status() -> Tuple[bool, str]:
    """Check if Ollama is installed and running. Returns (ok, message)."""
    try:
        result = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False, "Ollama is installed but returned an error"
    except FileNotFoundError:
        return False, "Ollama is not installed. Download from https://ollama.com/"
    except Exception as e:
        return False, "Cannot check Ollama: %s" % e

    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            count = len(data.get("models", []))
            return True, "Ollama is running with %d local model(s)" % count
    except Exception:
        pass

    return False, "Ollama is installed but not running. Start it with 'ollama serve'"


# ---------------------------------------------------------------------------
# Main configuration flow
# ---------------------------------------------------------------------------
def configure_provider_and_model() -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Complete flow: select provider -> enter API key -> fetch models -> select model.
    Returns (provider_key, model_id, api_key) or (None, None, None) if cancelled.
    api_key is None for Ollama, the key string for cloud providers.
    """
    return curses.wrapper(_configure_flow_curses)


def _configure_flow_curses(stdscr) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    _setup_colors()

    while True:
        # Step 1: Select provider
        provider_key = select_provider(stdscr)
        if provider_key is None:
            return None, None, None

        provider = PROVIDERS[provider_key]

        # Step 2: Check / get API key for cloud providers
        api_key = None
        if provider.get("needs_key"):
            api_key = get_api_key(provider_key, stdscr)
            if not api_key:
                _show_message(stdscr,
                              "No API key provided.\n"
                              "Please enter an API key to fetch models.\n"
                              "Press any key to try again...")
                continue

        # Step 3: Fetch models
        if provider_key == "ollama":
            ok, msg = check_ollama_status()
            if not ok:
                _show_message(stdscr, msg + "\n\nPress any key to return to provider selection...")
                continue
            models = fetch_models_ollama()
            if not models:
                _show_message(stdscr,
                              "No Ollama models found.\n"
                              "Install one with: ollama pull llama3\n"
                              "Or choose 'Custom Model' to enter a name.\n\n"
                              "Press any key to continue...")
                # For Ollama, we still let them proceed with custom model
                models = []
        else:
            stdscr.clear()
            stdscr.addstr(0, 0, "Fetching models from %s..." % provider["name"], _attr(COLOR_TITLE, True))
            stdscr.refresh()

            models = fetch_models_from_api(provider_key, api_key)
            if not models:
                _show_message(stdscr,
                              "Could not fetch models from %s.\n"
                              "Check your API key and internet connection.\n"
                              "You can still choose 'Custom Model' to enter a name.\n\n"
                              "Press any key to continue, or Q to go back..." % provider["name"])
                ch = stdscr.getch()
                if ch in (ord('q'), ord('Q')):
                    continue
                models = []

        # Step 4: Select model (always includes "Custom Model" option)
        selected = select_model(stdscr, models, provider["name"])
        if selected is None:
            continue

        return provider_key, selected, api_key


def _show_message(stdscr, text: str):
    """Show a message and wait for keypress."""
    stdscr.clear()
    _setup_colors()
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stdscr.addstr(i, 0, line, _attr(COLOR_NORMAL))
    stdscr.addstr(len(lines) + 2, 0, "Press any key...", _attr(COLOR_FOOTER))
    stdscr.refresh()
    stdscr.getch()


# ---------------------------------------------------------------------------
# Config sync
# ---------------------------------------------------------------------------
def sync_selection_to_config(provider: str, model: str, api_key: Optional[str] = None):
    """Sync provider/model selection to settings manager and config.yaml."""
    try:
        from ai_agent.utils.settings_manager import get_settings_manager

        mgr = get_settings_manager()
        mgr.set_preferred_provider(provider)
        mgr.set_model(provider, model)
        if api_key and provider != "ollama":
            mgr.set_api_key(provider, api_key)
    except Exception as e:
        sys.stderr.write("Warning: could not sync config: %s\n" % e)
