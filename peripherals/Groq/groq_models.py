"""
Groq Model Definitions for VEXIS-CLI Integration
Organized by company/model family with progressive selection support
Based on official Groq documentation as of 2025
"""

# Groq model families organized by company/provider
GROQ_MODEL_FAMILIES = {
    "meta": {
        "name": "Meta",
        "description": "Meta's Llama family models - High-performance open source models",
        "icon": "🦙",
        "priority": 1,
        "subfamilies": {
            "llama3.1": {
                "name": "Llama 3.1",
                "description": "Enhanced Llama 3.1 models with instant inference",
                "icon": "🚀",
                "models": {
                    "llama-3.1-8b-instant": {"name": "Llama 3.1 8B Instant", "desc": "8B parameters • Instant inference • 128K context", "icon": "⚡"},
                }
            },
            "llama3.3": {
                "name": "Llama 3.3", 
                "description": "Latest Llama 3.3 models with versatile capabilities",
                "icon": "🌟",
                "models": {
                    "llama-3.3-70b-versatile": {"name": "Llama 3.3 70B Versatile", "desc": "70B parameters • Versatile • 128K context", "icon": "🧠"},
                }
            },
            "llama4": {
                "name": "Llama 4",
                "description": "Latest Llama 4 models (Preview)",
                "icon": "🦄",
                "models": {
                    "llama-4-scout-17b-16e-instruct": {"name": "Llama 4 Scout 17B", "desc": "17B parameters • Scout • Preview • 128K context", "icon": "🔍"},
                }
            }
        }
    },
    "openai": {
        "name": "OpenAI",
        "description": "OpenAI's GPT-OSS models - Flagship open-weight models with tools",
        "icon": "🤖",
        "priority": 2,
        "subfamilies": {
            "gpt-oss": {
                "name": "GPT-OSS",
                "description": "OpenAI's open-weight models with browser search and code execution",
                "icon": "🔧",
                "models": {
                    "openai/gpt-oss-120b": {"name": "GPT-OSS 120B", "desc": "120B parameters • Browser search • Code execution • Reasoning", "icon": "👑"},
                    "openai/gpt-oss-20b": {"name": "GPT-OSS 20B", "desc": "20B parameters • Browser search • Code execution • Efficient", "icon": "🧠"},
                    "openai/gpt-oss-safeguard-20b": {"name": "GPT-OSS Safeguard 20B", "desc": "20B parameters • Safety focused • Preview", "icon": "🛡️"},
                }
            }
        }
    },
    "moonshotai": {
        "name": "Moonshot AI",
        "description": "Moonshot AI's Kimi models - Advanced agentic models",
        "icon": "🌙",
        "priority": 3,
        "subfamilies": {
            "kimi": {
                "name": "Kimi",
                "description": "Kimi models with agentic capabilities (Preview)",
                "icon": "🌙",
                "models": {
                    "kimi-k2-instruct-0905": {"name": "Kimi K2 Instruct", "desc": "Agentic • Long context • Preview • 256K context", "icon": "🧠"},
                }
            }
        }
    },
    "alibaba": {
        "name": "Alibaba",
        "description": "Alibaba's Qwen family models - High-performance multilingual models",
        "icon": "🐲",
        "priority": 4,
        "subfamilies": {
            "qwen3": {
                "name": "Qwen 3",
                "description": "Advanced Qwen 3 models (Preview)",
                "icon": "🌏",
                "models": {
                    "qwen3-32b": {"name": "Qwen 3 32B", "desc": "32B parameters • Multilingual • Preview • 32K context", "icon": "🌟"},
                }
            }
        }
    },
    "canopylabs": {
        "name": "Canopy Labs",
        "description": "Canopy Labs' Orpheus models - Specialized language models",
        "icon": "🌳",
        "priority": 5,
        "subfamilies": {
            "orpheus": {
                "name": "Orpheus",
                "description": "Specialized Orpheus models (Preview)",
                "icon": "🎵",
                "models": {
                    "canopylabs/orpheus-arabic-saudi": {"name": "Orpheus Arabic Saudi", "desc": "Arabic • Saudi dialect • Preview", "icon": "🕌"},
                    "canopylabs/orpheus-v1-english": {"name": "Orpheus V1 English", "desc": "English • V1 • Preview", "icon": "🇺🇸"},
                }
            }
        }
    },
    "audio": {
        "name": "Audio Models",
        "description": "Speech-to-text and audio processing models",
        "icon": "🎤",
        "priority": 6,
        "subfamilies": {
            "whisper": {
                "name": "Whisper",
                "description": "OpenAI's Whisper speech-to-text models",
                "icon": "🎧",
                "models": {
                    "whisper-large-v3": {"name": "Whisper Large V3", "desc": "Speech-to-text • Large • High accuracy", "icon": "🎙️"},
                    "whisper-large-v3-turbo": {"name": "Whisper Large V3 Turbo", "desc": "Speech-to-text • Large • Fast", "icon": "⚡"},
                }
            }
        }
    },
    "safety": {
        "name": "Safety Models",
        "description": "Content moderation and safety models",
        "icon": "🛡️",
        "priority": 7,
        "subfamilies": {
            "prompt-guard": {
                "name": "Prompt Guard",
                "description": "Meta's prompt safety models",
                "icon": "🔒",
                "models": {
                    "llama-prompt-guard-2-22m": {"name": "Prompt Guard 2 22M", "desc": "22M parameters • Prompt safety • Preview", "icon": "🔐"},
                    "llama-prompt-guard-2-86m": {"name": "Prompt Guard 2 86M", "desc": "86M parameters • Enhanced safety • Preview", "icon": "🛡️"},
                }
            }
        }
    }
}

# Production models (recommended for production use)
PRODUCTION_MODELS = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile", 
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "whisper-large-v3",
    "whisper-large-v3-turbo"
]

# Preview models (for evaluation only)
PREVIEW_MODELS = [
    "canopylabs/orpheus-arabic-saudi",
    "canopylabs/orpheus-v1-english",
    "llama-4-scout-17b-16e-instruct",
    "llama-prompt-guard-2-22m",
    "llama-prompt-guard-2-86m",
    "kimi-k2-instruct-0905",
    "openai/gpt-oss-safeguard-20b",
    "qwen3-32b"
]

# Default model for quick testing
DEFAULT_MODEL = "llama-3.1-8b-instant"

# Model capabilities mapping
MODEL_CAPABILITIES = {
    "chat": [
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "openai/gpt-oss-120b", 
        "openai/gpt-oss-20b",
        "kimi-k2-instruct-0905",
        "qwen3-32b",
        "canopylabs/orpheus-arabic-saudi",
        "canopylabs/orpheus-v1-english"
    ],
    "tools": [
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b"
    ],
    "audio": [
        "whisper-large-v3",
        "whisper-large-v3-turbo"
    ],
    "safety": [
        "llama-prompt-guard-2-22m",
        "llama-prompt-guard-2-86m",
        "openai/gpt-oss-safeguard-20b"
    ]
}

def get_all_models():
    """Get all available models as a flat list"""
    all_models = []
    for company_data in GROQ_MODEL_FAMILIES.values():
        for subfamily_data in company_data["subfamilies"].values():
            all_models.extend(subfamily_data["models"].keys())
    return all_models

def get_production_models():
    """Get production-ready models"""
    return PRODUCTION_MODELS

def get_preview_models():
    """Get preview models"""
    return PREVIEW_MODELS

def get_models_by_capability(capability):
    """Get models that support a specific capability"""
    return MODEL_CAPABILITIES.get(capability, [])

def get_model_info(model_name):
    """Get detailed information about a specific model"""
    for company_data in GROQ_MODEL_FAMILIES.values():
        for subfamily_data in company_data["subfamilies"].values():
            if model_name in subfamily_data["models"]:
                return {
                    "name": subfamily_data["models"][model_name]["name"],
                    "description": subfamily_data["models"][model_name]["desc"],
                    "icon": subfamily_data["models"][model_name]["icon"],
                    "company": company_data["name"],
                    "subfamily": subfamily_data["name"],
                    "is_production": model_name in PRODUCTION_MODELS,
                    "is_preview": model_name in PREVIEW_MODELS
                }
    return None
