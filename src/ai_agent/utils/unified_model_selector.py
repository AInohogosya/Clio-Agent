"""
Unified Model Selector with Scrolling, Filtering, and Yellow Highlighting

Features:
- Shows all available models for each provider
- Scrolling viewport to prevent off-screen scrolling
- Real-time filtering by typing characters
- Synchronization with config.yaml
- Yellow highlighting for selection
"""

import curses
import os
import yaml
from typing import Optional, List, Dict, Any, Tuple
from pathlib import Path

# Color pairs
COLOR_TITLE = 1
COLOR_HIGHLIGHT = 2
COLOR_NORMAL = 3
COLOR_FOOTER = 4
COLOR_FILTER = 5
COLOR_SUCCESS = 6


# Unified provider-model definitions
PROVIDER_MODELS = {
    "ollama": {
        "name": "Ollama (Local)",
        "icon": "🦊",
        "description": "Run models locally via Ollama • Privacy-focused",
        "models": [
            # Meta Llama
            {"id": "llama3.2:1b", "name": "Llama 3.2 1B", "desc": "1B params • Ultra lightweight • 128K context"},
            {"id": "llama3.2:3b", "name": "Llama 3.2 3B", "desc": "3B params • Lightweight • 128K context"},
            {"id": "llama3.1:8b", "name": "Llama 3.1 8B", "desc": "8B params • Enhanced • 128K context"},
            {"id": "llama3.1:70b", "name": "Llama 3.1 70B", "desc": "70B params • Enhanced • 128K context"},
            {"id": "llama4:latest", "name": "Llama 4 Latest", "desc": "Latest Llama 4 • Advanced reasoning • Text"},
            {"id": "llama4:16x17b", "name": "Llama 4 16x17B", "desc": "272B total • 16x17B MoE • Advanced reasoning"},
            {"id": "llama4:128x17b", "name": "Llama 4 128x17B", "desc": "2.18T total • 128x17B MoE • Frontier"},
            # Google Gemma
            {"id": "gemma3:1b", "name": "Gemma 3 1B", "desc": "1B params • Text only • 32K context"},
            {"id": "gemma3:4b", "name": "Gemma 3 4B", "desc": "4B params • Text only • 128K context"},
            {"id": "gemma3:12b", "name": "Gemma 3 12B", "desc": "12B params • Text only • 128K context"},
            {"id": "gemma3:27b", "name": "Gemma 3 27B", "desc": "27B params • Text only • 128K context"},
            {"id": "gemma3n:e2b", "name": "Gemma 3n E2B", "desc": "2B effective • Text only • 32K context"},
            {"id": "gemma3n:e4b", "name": "Gemma 3n E4B", "desc": "4B effective • Text only • 32K context"},
            {"id": "gemma4:e2b", "name": "Gemma 4 E2B (New)", "desc": "2B effective • Text only • Efficient"},
            {"id": "gemma4:e4b", "name": "Gemma 4 E4B (New)", "desc": "4B effective • Text only • Frontier"},
            {"id": "gemma4:26b", "name": "Gemma 4 26B (New)", "desc": "26B params • Text only • High performance"},
            {"id": "gemma4:31b", "name": "Gemma 4 31B (New)", "desc": "31B params • Text only • Advanced"},
            # DeepSeek
            {"id": "deepseek-r1:1.5b", "name": "DeepSeek R1 1.5B", "desc": "1.5B params • Reasoning • 128K context"},
            {"id": "deepseek-r1:7b", "name": "DeepSeek R1 7B", "desc": "7B params • Reasoning • 128K context"},
            {"id": "deepseek-r1:8b", "name": "DeepSeek R1 8B", "desc": "8B params • Reasoning • 128K context"},
            {"id": "deepseek-r1:14b", "name": "DeepSeek R1 14B", "desc": "14B params • Reasoning • 128K context"},
            {"id": "deepseek-r1:32b", "name": "DeepSeek R1 32B", "desc": "32B params • Reasoning • 128K context"},
            {"id": "deepseek-r1:70b", "name": "DeepSeek R1 70B", "desc": "70B params • Reasoning • 128K context"},
            {"id": "deepseek-v3:latest", "name": "DeepSeek V3 Latest", "desc": "671B params • 160K context • MoE"},
            {"id": "deepseek-v3.1:latest", "name": "DeepSeek V3.1 Latest", "desc": "671B params • Enhanced • 160K context"},
            {"id": "deepseek-coder-v2:latest", "name": "DeepSeek Coder V2", "desc": "16B params • 160K context • MoE coding"},
            # Microsoft Phi
            {"id": "phi3:mini", "name": "Phi-3 Mini", "desc": "3.8B params • Mini • 4K context"},
            {"id": "phi3:3.8b", "name": "Phi-3 Mini 3.8B", "desc": "3.8B params • Mini • 128K context"},
            {"id": "phi3:medium", "name": "Phi-3 Medium", "desc": "14B params • Medium • 4K context"},
            {"id": "phi3:14b", "name": "Phi-3 Medium 14B", "desc": "14B params • Medium • 128K context"},
            {"id": "phi4:latest", "name": "Phi-4 Latest", "desc": "14B params • State-of-the-art • 16K context"},
            # Mistral
            {"id": "mistral:7b", "name": "Mistral 7B", "desc": "7B params • Latest • 32K context"},
            {"id": "mistral-large:latest", "name": "Mistral Large 2 Latest", "desc": "123B params • 128K context • Advanced"},
            {"id": "ministral-3:3b", "name": "Ministral 3 3B", "desc": "3B params • Edge • Text only • 256K context"},
            {"id": "ministral-3:8b", "name": "Ministral 3 8B", "desc": "8B params • Edge • Text only • 256K context"},
            {"id": "ministral-3:14b", "name": "Ministral 3 14B", "desc": "14B params • Edge • Text only • 256K context"},
            # Alibaba Qwen
            {"id": "qwen2.5:0.5b", "name": "Qwen 2.5 0.5B", "desc": "0.5B params • Multilingual • 128K context"},
            {"id": "qwen2.5:1.5b", "name": "Qwen 2.5 1.5B", "desc": "1.5B params • Multilingual • 128K context"},
            {"id": "qwen2.5:3b", "name": "Qwen 2.5 3B", "desc": "3B params • Multilingual • 128K context"},
            {"id": "qwen2.5:7b", "name": "Qwen 2.5 7B", "desc": "7B params • Multilingual • 128K context"},
            {"id": "qwen2.5:14b", "name": "Qwen 2.5 14B", "desc": "14B params • Multilingual • 128K context"},
            {"id": "qwen2.5:32b", "name": "Qwen 2.5 32B", "desc": "32B params • Multilingual • 128K context"},
            {"id": "qwen2.5-coder", "name": "Qwen 2.5 Coder", "desc": "7B params • Code-focused • 128K context"},
            {"id": "qwen3:0.6b", "name": "Qwen 3 0.6B", "desc": "0.6B params • Dense • 40K context"},
            {"id": "qwen3:1.7b", "name": "Qwen 3 1.7B", "desc": "1.7B params • Dense • 40K context"},
            {"id": "qwen3:4b", "name": "Qwen 3 4B", "desc": "4B params • Dense • 256K context"},
            {"id": "qwen3:8b", "name": "Qwen 3 8B", "desc": "8B params • Dense • 40K context"},
            {"id": "qwen3:14b", "name": "Qwen 3 14B", "desc": "14B params • Dense • 40K context"},
            {"id": "qwen3:32b", "name": "Qwen 3 32B", "desc": "32B params • Dense • 40K context"},
            {"id": "qwen3:235b", "name": "Qwen 3 235B", "desc": "235B params • MoE • 256K context"},
            {"id": "qwen3-coder:30b", "name": "Qwen 3 Coder 30B", "desc": "30B params • Agentic coding • 256K context"},
            
            {"id": "qwen3.5:0.8b", "name": "Qwen 3.5 0.8B (New)", "desc": "0.8B params • Text only • Ultra lightweight"},
            {"id": "qwen3.5:2b", "name": "Qwen 3.5 2B (New)", "desc": "2B params • Text only • Lightweight"},
            {"id": "qwen3.5:4b", "name": "Qwen 3.5 4B (New)", "desc": "4B params • Text only • Efficient"},
            {"id": "qwen3.5:9b", "name": "Qwen 3.5 9B (New)", "desc": "9B params • Text only • Exceptional"},
            {"id": "qwen3.5:27b", "name": "Qwen 3.5 27B (New)", "desc": "27B params • Text only • High performance"},
            {"id": "qwen3.5:35b", "name": "Qwen 3.5 35B (New)", "desc": "35B params • Text only • Advanced"},
            {"id": "qwen3.5:122b", "name": "Qwen 3.5 122B (New)", "desc": "122B params • Text only • Frontier"},
            {"id": "qwen3-next:80b", "name": "Qwen 3 Next 80B", "desc": "80B params • High efficiency • 256K context"},
            # BigCode StarCoder
            {"id": "starcoder2:3b", "name": "StarCoder 2 3B", "desc": "3B params • 17 languages • 16K context"},
            {"id": "starcoder2:7b", "name": "StarCoder 2 7B", "desc": "7B params • 17 languages • 16K context"},
            {"id": "starcoder2:15b", "name": "StarCoder 2 15B", "desc": "15B params • 600+ languages • 16K context"},
            # IBM Granite
            {"id": "granite-code:3b", "name": "Granite Code 3B", "desc": "3B params • Code generation • 125K context"},
            {"id": "granite-code:8b", "name": "Granite Code 8B", "desc": "8B params • Code generation • 125K context"},
            {"id": "granite-code:20b", "name": "Granite Code 20B", "desc": "20B params • Code generation • 8K context"},
            {"id": "granite-code:34b", "name": "Granite Code 34B", "desc": "34B params • Code generation • 8K context"},
            {"id": "granite4:350m", "name": "Granite 4 350M", "desc": "350M params • 32K context • Efficient"},
            {"id": "granite4:1b", "name": "Granite 4 1B", "desc": "1B params • 128K context • Compact"},
            {"id": "granite4:3b", "name": "Granite 4 3B", "desc": "3B params • 128K context • Balanced"},
            # Cohere
            {"id": "command-r:latest", "name": "Command R Latest", "desc": "35B params • RAG capabilities • 128K context"},
            {"id": "command-r7b:latest", "name": "Command R7B Latest", "desc": "7B params • Efficient • 8K context"},
            # 01.AI Yi
            {"id": "yi:6b", "name": "Yi 6B", "desc": "6B params • Bilingual • 4K context"},
            {"id": "yi:9b", "name": "Yi 9B", "desc": "9B params • Bilingual • 4K context"},
            {"id": "yi:34b", "name": "Yi 34B", "desc": "34B params • Bilingual • 4K context"},
            {"id": "yi-coder:1.5b", "name": "Yi Coder 1.5B", "desc": "1.5B params • Code-focused • 128K context"},
            {"id": "yi-coder:9b", "name": "Yi Coder 9B", "desc": "9B params • Code-focused • 128K context"},
            # Specialized
            {"id": "codestral:latest", "name": "Codestral Latest", "desc": "22B params • Code generation • 32K context"},

            {"id": "hermes3:3b", "name": "Hermes 3 3B", "desc": "3B params • 128K context • Efficient"},
            {"id": "hermes3:8b", "name": "Hermes 3 8B", "desc": "8B params • 128K context • Balanced"},
            {"id": "hermes3:70b", "name": "Hermes 3 70B", "desc": "70B params • 128K context • Powerful"},
            {"id": "hermes3:405b", "name": "Hermes 3 405B", "desc": "405B params • 128K context • Frontier"},
            {"id": "wizardlm2:7b", "name": "WizardLM 2 7B", "desc": "7B params • 32K context • Efficient"},
            {"id": "wizardlm2:8x22b", "name": "WizardLM 2 8x22B", "desc": "176B params • 64K context • Advanced"},
            {"id": "reflection:70b", "name": "Reflection 70B", "desc": "70B params • 128K context • Self-correcting"},

            {"id": "devstral-small-2:24b", "name": "Devstral Small 2 24B", "desc": "24B params • 384K context • Agentic coding"},
            # Zhipu AI
            {"id": "glm4:9b", "name": "GLM-4 9B", "desc": "9B params • 128K context • Text only"},
            # OpenAI GPT-OSS
            {"id": "gpt-oss:20b", "name": "GPT-OSS 20B", "desc": "20B params • High performance • 128K context"},
            {"id": "gpt-oss:120b", "name": "GPT-OSS 120B", "desc": "120B params • Frontier • 128K context"},
            # NVIDIA Nemotron
            {"id": "nemotron-3-nano:4b", "name": "Nemotron 3 Nano 4B", "desc": "4B params • Text only • Edge optimized"},
            {"id": "nemotron-3-nano:30b", "name": "Nemotron 3 Nano 30B", "desc": "30B params • Text only • 1M context"},
        ]
    },
    "google": {
        "name": "Google",
        "icon": "🌐",
        "description": "Google Gemini models • Enterprise-grade",
        "models": [
            {"id": "gemini-3.1-pro-preview", "name": "Gemini 3.1 Pro Preview", "desc": "2M context • Advanced agentic coding • Latest"},
            {"id": "gemini-3-flash-preview", "name": "Gemini 3 Flash Preview", "desc": "Frontier performance • Cost-effective • Latest"},
            {"id": "gemini-3.1-flash-lite-preview", "name": "Gemini 3.1 Flash Lite Preview", "desc": "Ultra-efficient • New • Fast"},
            {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro", "desc": "1M context • Advanced reasoning • Text"},
            {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash", "desc": "Fast & efficient • Text • 1M context"},
            {"id": "gemini-2.5-flash-lite", "name": "Gemini 2.5 Flash Lite", "desc": "Lightweight • Fast • Cost-effective"},
            {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "desc": "Fast • Text • 1M context"},
            {"id": "gemini-2.0-flash-lite", "name": "Gemini 2.0 Flash Lite", "desc": "Efficient • Low latency • 1M context"},
        ]
    },
    "openai": {
        "name": "OpenAI",
        "icon": "🤖",
        "description": "OpenAI GPT models • Advanced capabilities",
        "models": [
            {"id": "gpt-5.4", "name": "GPT-5.4", "desc": "OpenAI flagship • 1M context • Best reasoning & coding"},
            {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "desc": "Strong mini model • Coding & computer use"},
            {"id": "gpt-5.4-nano", "name": "GPT-5.4 Nano", "desc": "Cheapest GPT-5.4 • High volume tasks"},
            {"id": "gpt-4.1", "name": "GPT-4.1", "desc": "1M context • Smarter & more efficient"},
            {"id": "gpt-4.1-mini", "name": "GPT-4.1 Mini", "desc": "Fast & cost-effective • 1M context"},
            {"id": "gpt-4.1-nano", "name": "GPT-4.1 Nano", "desc": "Ultra-fast • Cheapest • 1M context"},
            {"id": "gpt-4o", "name": "GPT-4o", "desc": "Text model • 128K context"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "desc": "Efficient • 128K context"},
            {"id": "o3", "name": "O3", "desc": "Advanced reasoning • STEM & complex tasks • 200K context"},
            {"id": "o3-mini", "name": "O3 Mini", "desc": "Efficient reasoning • 200K context"},
            {"id": "o4-mini", "name": "O4 Mini", "desc": "Fast reasoning • Cost-effective • 200K context"},
        ]
    },
    "anthropic": {
        "name": "Anthropic",
        "icon": "🧠",
        "description": "Anthropic Claude models • Strong reasoning",
        "models": [
            {"id": "claude-opus-4-6-20260219", "name": "Claude Opus 4.6", "desc": "Most capable • 1M context • Agent teams • Latest"},
            {"id": "claude-sonnet-4-6-20260219", "name": "Claude Sonnet 4.6", "desc": "Near-Opus performance • Balanced • Latest"},
            {"id": "claude-opus-4-5-20251125", "name": "Claude Opus 4.5", "desc": "Outperforms humans on coding exams • 200K context"},
            {"id": "claude-sonnet-4-5-20251125", "name": "Claude Sonnet 4.5", "desc": "Efficient & capable • 200K context"},
            {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "desc": "Strong performance • 200K context"},
            {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku", "desc": "Fast & efficient • 200K context"},
        ]
    },
    "xai": {
        "name": "xAI",
        "icon": "🚀",
        "description": "xAI Grok models • Real-time knowledge",
        "models": [
            {"id": "grok-4.1", "name": "Grok 4.1", "desc": "State-of-the-art • #1 on LMArena • Real-time"},
            {"id": "grok-4.1-fast", "name": "Grok 4.1 Fast", "desc": "Quick responses • Dec 2025"},
            {"id": "grok-4.1-thinking", "name": "Grok 4.1 Thinking", "desc": "Deep reasoning mode • Complex tasks"},
        ]
    },
    "meta": {
        "name": "Meta",
        "icon": "🦙",
        "description": "Meta Llama models • Open source",
        "models": [
            {"id": "llama-4-scout-17b-16e-instruct", "name": "Llama 4 Scout", "desc": "10M context • 17B active • Text • Fast"},
            {"id": "llama-4-maverick-17b-128e-instruct", "name": "Llama 4 Maverick", "desc": "1M context • 128 experts • Text • High performance"},
            {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "desc": "70B params • 128K context • Versatile"},
            {"id": "llama-3.1-70b-instruct", "name": "Llama 3.1 70B", "desc": "70B params • 128K context • Strong performance"},
            {"id": "llama-3.1-8b-instruct", "name": "Llama 3.1 8B", "desc": "8B params • 128K context • Efficient"},
        ]
    },
    "groq": {
        "name": "Groq",
        "icon": "⚡",
        "description": "Groq fast inference • Ultra-low latency",
        "models": [
            {"id": "llama-3.3-70b-versatile", "name": "Llama 3.3 70B", "desc": "Groq hosted • Ultra-fast • 128K context"},
            {"id": "llama-3.1-8b-instant", "name": "Llama 3.1 8B", "desc": "Groq hosted • Low latency • 128K context"},
            {"id": "llama-3.1-70b-versatile", "name": "Llama 3.1 70B", "desc": "Groq hosted • Fast • 128K context"},
            {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B", "desc": "Groq hosted • MoE architecture • 32K context"},
            {"id": "gemma2-9b-it", "name": "Gemma 2 9B", "desc": "Groq hosted • Fast • 8K context"},
        ]
    },
    "deepseek": {
        "name": "DeepSeek",
        "icon": "🔍",
        "description": "DeepSeek reasoning models • Advanced AI",
        "models": [
            {"id": "deepseek-chat", "name": "DeepSeek Chat", "desc": "General conversation • 64K context"},
            {"id": "deepseek-coder", "name": "DeepSeek Coder", "desc": "Code generation specialist • 128K context"},
            {"id": "deepseek-reasoner", "name": "DeepSeek Reasoner", "desc": "Advanced reasoning • 64K context"},
            {"id": "deepseek-v3", "name": "DeepSeek V3", "desc": "671B params • MoE • Fast inference"},
            {"id": "deepseek-r1", "name": "DeepSeek R1", "desc": "Reasoning model • Open source • 64K context"},
        ]
    },
    "together": {
        "name": "Together AI",
        "icon": "🤝",
        "description": "Together AI open-source models",
        "models": [
            {"id": "meta-llama/Llama-4-Scout-17B-16E-Instruct", "name": "Llama 4 Scout", "desc": "Together hosted • 10M context • Text"},
            {"id": "meta-llama/Llama-4-Maverick-17B-128E-Instruct", "name": "Llama 4 Maverick", "desc": "Together hosted • 1M context • Text"},
            {"id": "meta-llama/Llama-3.3-70B-Instruct", "name": "Llama 3.3 70B", "desc": "Together hosted • 128K context"},
            {"id": "Qwen/Qwen2.5-72B-Instruct", "name": "Qwen 2.5 72B", "desc": "Together hosted • 128K context"},
            {"id": "mistralai/Mixtral-8x7B-Instruct-v0.1", "name": "Mixtral 8x7B", "desc": "Together hosted • MoE • 32K context"},
        ]
    },
    "microsoft": {
        "name": "Microsoft Azure",
        "icon": "☁️",
        "description": "Azure OpenAI GPT models • Enterprise",
        "models": [
            {"id": "gpt-5.4", "name": "GPT-5.4", "desc": "Azure hosted • 1M context • Latest"},
            {"id": "gpt-5.4-mini", "name": "GPT-5.4 Mini", "desc": "Azure hosted • Efficient • 1M context"},
            {"id": "gpt-4.1", "name": "GPT-4.1", "desc": "Azure hosted • 1M context • Efficient"},
            {"id": "gpt-4o", "name": "GPT-4o", "desc": "Azure hosted • 128K context • Text"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "desc": "Azure hosted • 128K context • Efficient"},
        ]
    },
    "mistral": {
        "name": "Mistral AI",
        "icon": "🌍",
        "description": "Mistral multilingual models • European AI",
        "models": [
            {"id": "mistral-large-latest", "name": "Mistral Large 2", "desc": "123B params • 128K context • Advanced reasoning"},
            {"id": "mistral-medium-latest", "name": "Mistral Medium", "desc": "Balanced performance • 32K context"},
            {"id": "mistral-small-latest", "name": "Mistral Small", "desc": "Fast & efficient • 32K context"},
            {"id": "ministral-3b-latest", "name": "Ministral 3B", "desc": "Edge deployment • 128K context"},
            {"id": "ministral-8b-latest", "name": "Ministral 8B", "desc": "Edge deployment • 128K context"},
            {"id": "mixtral-8x7b-instruct", "name": "Mixtral 8x7B", "desc": "MoE architecture • 32K context"},
            {"id": "codestral-latest", "name": "Codestral", "desc": "Code generation • 32K context"},
        ]
    },
    "amazon": {
        "name": "Amazon Bedrock",
        "icon": "🏭",
        "description": "AWS Bedrock models • Enterprise cloud",
        "models": [
            {"id": "anthropic.claude-opus-4-6-20260219-v1:0", "name": "Claude Opus 4.6", "desc": "AWS hosted • 1M context • Latest"},
            {"id": "anthropic.claude-sonnet-4-6-20260219-v1:0", "name": "Claude Sonnet 4.6", "desc": "AWS hosted • 1M context • Latest"},
            {"id": "anthropic.claude-3-5-sonnet-20241022-v2:0", "name": "Claude 3.5 Sonnet", "desc": "AWS hosted • 200K context"},
            {"id": "meta.llama4-scout-17b-16e-instruct-v1:0", "name": "Llama 4 Scout", "desc": "AWS hosted • 10M context"},
            {"id": "meta.llama4-maverick-17b-128e-instruct-v1:0", "name": "Llama 4 Maverick", "desc": "AWS hosted • 1M context"},
            {"id": "amazon.nova-pro-v1:0", "name": "Nova Pro", "desc": "AWS native • 300K context • Text"},
            {"id": "amazon.nova-lite-v1:0", "name": "Nova Lite", "desc": "AWS native • 300K context • Text"},
        ]
    },
    "cohere": {
        "name": "Cohere",
        "icon": "🏢",
        "description": "Cohere Command models • Enterprise",
        "models": [
            {"id": "command-r-plus", "name": "Command R+", "desc": "Cohere's best • 128K context • RAG"},
            {"id": "command-r", "name": "Command R", "desc": "Balanced performance • 128K context"},
            {"id": "command", "name": "Command", "desc": "Legacy model • 4K context"},
            {"id": "command-r7b", "name": "Command R7B", "desc": "Compact 7B • 128K context • Efficient"},
        ]
    },
    "minimax": {
        "name": "MiniMax",
        "icon": "🚀",
        "description": "MiniMax M2-series models • Productivity",
        "models": [
            {"id": "MiniMax-Text-01", "name": "MiniMax Text-01", "desc": "Latest general model • 1M context"},
            {"id": "MiniMax-M2.5", "name": "MiniMax M2.5", "desc": "State-of-the-art • Productivity & coding"},
            {"id": "MiniMax-M2.7", "name": "MiniMax M2.7", "desc": "Agent teams • Complex skills • 200K context"},
            {"id": "abab6.5s", "name": "ABAB 6.5S", "desc": "Chat model • Fast responses"},
        ]
    },
    "zhipuai": {
        "name": "Zhipu AI",
        "icon": "🌐",
        "description": "Zhipu AI GLM models • Chinese AI",
        "models": [
            {"id": "glm-5", "name": "GLM-5 (New)", "desc": "744B total params • 40B active • Advanced coding"},
            {"id": "glm-5.1", "name": "GLM-5.1 (New)", "desc": "Enhanced • Feb 2026 release • Strong reasoning"},
            {"id": "glm-4-plus", "name": "GLM-4 Plus", "desc": "Strong general performance • 128K context"},
            {"id": "glm-4", "name": "GLM-4", "desc": "Base model • Capable generalist • 128K context"},

        ]
    },
    "openrouter": {
        "name": "OpenRouter",
        "icon": "🔀",
        "description": "Access 300+ AI models • Universal API",
        "models": [
            {"id": "openai/gpt-4o", "name": "OpenAI/GPT-4o", "desc": "128K context • Text • Via OpenRouter"},
            {"id": "openai/gpt-4o-mini", "name": "OpenAI/GPT-4o Mini", "desc": "128K context • Efficient • Via OpenRouter"},
            {"id": "openai/gpt-5.4", "name": "OpenAI/GPT-5.4", "desc": "1M context • Latest • Via OpenRouter"},
            {"id": "openai/o3", "name": "OpenAI/O3", "desc": "Advanced reasoning • Via OpenRouter"},
            {"id": "openai/o4-mini", "name": "OpenAI/O4 Mini", "desc": "Fast reasoning • Via OpenRouter"},
            {"id": "anthropic/claude-opus-4-6-20260219", "name": "Anthropic/Claude Opus 4.6", "desc": "1M context • Latest • Via OpenRouter"},
            {"id": "anthropic/claude-sonnet-4-6-20260219", "name": "Anthropic/Claude Sonnet 4.6", "desc": "1M context • Latest • Via OpenRouter"},
            {"id": "anthropic/claude-3-5-sonnet-20241022", "name": "Anthropic/Claude 3.5 Sonnet", "desc": "200K context • Via OpenRouter"},
            {"id": "google/gemini-2.5-pro", "name": "Google/Gemini 2.5 Pro", "desc": "1M context • Via OpenRouter"},
            {"id": "google/gemini-2.5-flash", "name": "Google/Gemini 2.5 Flash", "desc": "1M context • Fast • Via OpenRouter"},
            {"id": "google/gemini-3.1-pro-preview", "name": "Google/Gemini 3.1 Pro", "desc": "2M context • Latest • Via OpenRouter"},
            {"id": "meta-llama/llama-4-scout-17b-16e-instruct", "name": "Meta/Llama 4 Scout", "desc": "10M context • Text • Via OpenRouter"},
            {"id": "meta-llama/llama-4-maverick-17b-128e-instruct", "name": "Meta/Llama 4 Maverick", "desc": "1M context • Text • Via OpenRouter"},
            {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Meta/Llama 3.3 70B", "desc": "128K context • Via OpenRouter"},
            {"id": "deepseek/deepseek-r1", "name": "DeepSeek/R1", "desc": "Reasoning model • Via OpenRouter"},
            {"id": "deepseek/deepseek-v3", "name": "DeepSeek/V3", "desc": "671B MoE • Via OpenRouter"},
            {"id": "x-ai/grok-4.1", "name": "xAI/Grok 4.1", "desc": "Real-time knowledge • Via OpenRouter"},
            {"id": "mistralai/mistral-large", "name": "Mistral/Large", "desc": "123B params • Via OpenRouter"},
            {"id": "qwen/qwen3-8b", "name": "Alibaba/Qwen 3 8B", "desc": "40K context • Via OpenRouter"},
            {"id": "qwen/qwen3-235b-a22b", "name": "Alibaba/Qwen 3 235B", "desc": "MoE • 256K context • Via OpenRouter"},
            {"id": "openrouter/auto", "name": "OpenRouter/Auto", "desc": "Automatic model selection • Optimized"},
        ]
    },
}


class ScrollingModelSelector:
    """
    Curses-based model selector with:
    - Scrolling viewport to prevent off-screen scrolling
    - Real-time filtering by typing characters
    - Yellow highlighting for selection
    - Synchronization with config.yaml
    """

    def __init__(self, provider: str, preselect_model: Optional[str] = None):
        self.provider = provider
        self.provider_info = PROVIDER_MODELS.get(provider, {})
        self.models = self.provider_info.get("models", [])
        self.current_index = 0
        self.scroll_offset = 0
        self.filter_text = ""
        self.filtered_indices = []

        # If a model is pre-selected from config, highlight it
        if preselect_model:
            for i, m in enumerate(self.models):
                if m["id"] == preselect_model:
                    self.current_index = i
                    self.scroll_offset = max(0, i - 2)
                    break
        
    def _get_filtered_models(self) -> List[int]:
        """Get indices of models matching the filter text"""
        if not self.filter_text:
            return list(range(len(self.models)))
        
        filter_lower = self.filter_text.lower()
        return [
            i for i, model in enumerate(self.models)
            if (filter_lower in model["id"].lower() or 
                filter_lower in model["name"].lower() or
                filter_lower in model["desc"].lower())
        ]
    
    def _calculate_viewport(self, max_y: int) -> Tuple[int, int]:
        """Calculate visible viewport bounds, ensuring no overflow"""
        # Reserve space for header (4 lines), filter (2 lines), instructions (1 line), footer (2 lines)
        header_height = 4
        filter_height = 2 if self.filter_text else 0
        instructions_height = 1
        footer_height = 2
        available_height = max_y - header_height - filter_height - instructions_height - footer_height
        items_per_page = max(1, available_height // 3)  # Each item takes 3 lines
        
        return items_per_page, header_height
    
    def run(self, stdscr) -> Optional[str]:
        """Run the model selector"""
        curses.curs_set(0)  # Hide cursor
        stdscr.clear()
        
        # Initialize colors
        if curses.has_colors():
            curses.start_color()
            curses.init_pair(COLOR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(COLOR_HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(COLOR_NORMAL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FOOTER, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FILTER, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(COLOR_SUCCESS, curses.COLOR_GREEN, curses.COLOR_BLACK)
        
        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()
            
            # Get filtered models
            self.filtered_indices = self._get_filtered_models()
            
            # Reset current_index if out of bounds
            if self.current_index >= len(self.filtered_indices):
                self.current_index = max(0, len(self.filtered_indices) - 1)
            
            # Calculate viewport
            items_per_page, header_height = self._calculate_viewport(max_y)
            
            # Adjust scroll offset to keep current item visible
            if self.filtered_indices:
                if self.current_index < self.scroll_offset:
                    self.scroll_offset = self.current_index
                elif self.current_index >= self.scroll_offset + items_per_page:
                    self.scroll_offset = self.current_index - items_per_page + 1
            
            # Draw header
            title = f"🤖 {self.provider_info.get('name', self.provider)} Model Selection"
            stdscr.addstr(0, 0, title[:max_x-1], 
                         curses.A_BOLD | curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_BOLD)
            
            separator = "=" * min(50, max_x - 1)
            stdscr.addstr(1, 0, separator, curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)
            
            total_models = len(self.models)
            filtered_count = len(self.filtered_indices)
            if self.filter_text:
                status = f"Filter: '{self.filter_text}' • {filtered_count}/{total_models} models"
            else:
                status = f"{total_models} models available • Type to filter"
            stdscr.addstr(2, 0, status[:max_x-1], curses.color_pair(COLOR_FILTER) if curses.has_colors() else curses.A_NORMAL)
            
            # Draw filter input if active
            filter_y = 4
            if self.filter_text:
                filter_display = f"🔍 {self.filter_text}_"
                stdscr.addstr(3, 0, filter_display[:max_x-1], 
                             curses.color_pair(COLOR_FILTER) | curses.A_BOLD if curses.has_colors() else curses.A_BOLD)
                filter_y = 5
            
            # Instructions
            stdscr.addstr(filter_y, 0, "💡 ↑↓:Navigate • Type:Filter • Enter:Select • Backspace:Clear • Q:Quit"[:max_x-1],
                         curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)
            
            # Draw visible items
            start_y = filter_y + 2
            visible_start = self.scroll_offset
            visible_end = min(self.scroll_offset + items_per_page, len(self.filtered_indices))
            
            for display_idx in range(visible_end - visible_start):
                list_idx = visible_start + display_idx
                if list_idx >= len(self.filtered_indices):
                    break
                    
                model_idx = self.filtered_indices[list_idx]
                model = self.models[model_idx]
                y = start_y + (display_idx * 3)
                
                if y >= max_y - 3:
                    break
                
                is_selected = (list_idx == self.current_index)
                
                # Determine icon
                if "latest" in model["id"].lower() or "new" in model["desc"].lower():
                    icon = "✨"
                elif "frontier" in model["desc"].lower() or "flagship" in model["desc"].lower():
                    icon = "🚀"
                else:
                    icon = "🧠"
                
                line1 = f"  {'▶' if is_selected else ' '} {icon} {model['name']}"
                line2 = f"     {model['desc']}"
                
                if is_selected:
                    # Yellow highlighting
                    if len(line1) < max_x:
                        stdscr.addstr(y, 0, line1, 
                                     curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD if curses.has_colors() else curses.A_REVERSE)
                    if len(line2) < max_x:
                        stdscr.addstr(y + 1, 0, line2, 
                                     curses.color_pair(COLOR_HIGHLIGHT) if curses.has_colors() else curses.A_REVERSE)
                else:
                    if len(line1) < max_x:
                        stdscr.addstr(y, 0, line1, 
                                     curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)
                    if len(line2) < max_x:
                        stdscr.addstr(y + 1, 0, line2, 
                                     curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_DIM)
            
            # Draw scroll indicators
            if self.scroll_offset > 0:
                stdscr.addstr(start_y - 1, 0, "  ▲ More above ▲", 
                             curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)
            if visible_end < len(self.filtered_indices):
                indicator_y = start_y + ((visible_end - visible_start) * 3)
                if indicator_y < max_y - 3:
                    stdscr.addstr(indicator_y, 0, "  v More below v",
                                 curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)
            
            # Footer
            footer_y = max_y - 2
            if footer_y > start_y and footer_y < max_y:
                stdscr.addstr(footer_y, 0, separator, curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)
            
            stdscr.refresh()
            
            # Handle input
            key = stdscr.getch()
            
            if key == curses.KEY_UP:
                if self.current_index > 0:
                    self.current_index -= 1
            elif key == curses.KEY_DOWN:
                if self.current_index < len(self.filtered_indices) - 1:
                    self.current_index += 1
            elif key == curses.KEY_PPAGE:  # Page Up
                self.current_index = max(0, self.current_index - items_per_page)
            elif key == curses.KEY_NPAGE:  # Page Down
                self.current_index = min(len(self.filtered_indices) - 1, self.current_index + items_per_page)
            elif key == curses.KEY_HOME:
                self.current_index = 0
            elif key == curses.KEY_END:
                self.current_index = max(0, len(self.filtered_indices) - 1)
            elif key in [10, 13]:  # Enter key
                if self.filtered_indices:
                    selected_idx = self.filtered_indices[self.current_index]
                    return self.models[selected_idx]["id"]
            elif key in [ord('q'), ord('Q'), 27]:  # Q or ESC
                return None
            elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:  # Backspace
                if self.filter_text:
                    self.filter_text = self.filter_text[:-1]
                    self.current_index = 0
                    self.scroll_offset = 0
            elif key >= 32 and key <= 126:  # Printable characters
                if len(self.filter_text) < 50:  # Limit filter length
                    self.filter_text += chr(key)
                    self.current_index = 0
                    self.scroll_offset = 0
    
    def show(self) -> Optional[str]:
        """Show the model selector"""
        return curses.wrapper(self.run)


class ProviderSelector:
    """Curses-based provider selector with advanced settings"""

    # Special return values for extra menu items
    ADVANCED_SETTINGS = "__advanced_settings__"
    MESSAGING_SETTINGS = "__messaging_settings__"
    EXIT_SETTINGS = "__exit_settings__"

    def __init__(self):
        self.providers = list(PROVIDER_MODELS.items())
        # Extra items appended after providers: messaging, advanced, exit
        self.extra_items = [
            ("messaging", "💬 Messaging Settings", "Configure Telegram & Discord bot"),
            ("advanced",  "⚙️  Advanced Settings",  "Tune idle behavior and other options"),
            ("exit",      "✅ Exit Settings",       "Save and start the agent"),
        ]
        self.total_items = len(self.providers) + len(self.extra_items)
        self.current_index = 0
        self.scroll_offset = 0

    def _draw_items(self, stdscr, max_y, max_x, items_per_page, start_y, visible_start, visible_end):
        """Draw provider rows plus extra menu items."""
        for display_idx in range(visible_end - visible_start):
            list_idx = visible_start + display_idx
            if list_idx >= self.total_items:
                break

            y = start_y + (display_idx * 3)
            if y >= max_y - 3:
                break

            is_selected = (list_idx == self.current_index)

            if list_idx < len(self.providers):
                # --- Provider row ---
                provider_key, provider_info = self.providers[list_idx]
                icon = provider_info.get("icon", "📋")
                model_count = len(provider_info.get("models", []))
                line1 = f"  {'▶' if is_selected else ' '} {icon} {provider_info['name']}"
                line2 = f"     {provider_info['description']} • {model_count} models"
            else:
                # --- Extra menu item ---
                extra_idx = list_idx - len(self.providers)
                _, name, desc = self.extra_items[extra_idx]
                line1 = f"  {'▶' if is_selected else ' '} {name}"
                line2 = f"     {desc}"

            if is_selected:
                if len(line1) < max_x:
                    stdscr.addstr(y, 0, line1,
                                 curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD
                                 if curses.has_colors() else curses.A_REVERSE)
                if len(line2) < max_x:
                    stdscr.addstr(y + 1, 0, line2,
                                 curses.color_pair(COLOR_HIGHLIGHT)
                                 if curses.has_colors() else curses.A_REVERSE)
            else:
                if len(line1) < max_x:
                    stdscr.addstr(y, 0, line1,
                                 curses.color_pair(COLOR_NORMAL)
                                 if curses.has_colors() else curses.A_NORMAL)
                if len(line2) < max_x:
                    stdscr.addstr(y + 1, 0, line2,
                                 curses.color_pair(COLOR_NORMAL)
                                 if curses.has_colors() else curses.A_DIM)

    def _handle_enter(self):
        """Return the value for the currently selected item."""
        if self.current_index < len(self.providers):
            return self.providers[self.current_index][0]
        extra_idx = self.current_index - len(self.providers)
        action_key = self.extra_items[extra_idx][0]
        if action_key == "messaging":
            return self.MESSAGING_SETTINGS
        elif action_key == "advanced":
            return self.ADVANCED_SETTINGS
        elif action_key == "exit":
            return self.EXIT_SETTINGS
        return None

    def run(self, stdscr) -> Optional[str]:
        """Run the provider selector with advanced-settings / exit items."""
        curses.curs_set(0)
        stdscr.clear()

        if curses.has_colors():
            curses.start_color()
            curses.init_pair(COLOR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(COLOR_HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(COLOR_NORMAL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FOOTER, curses.COLOR_YELLOW, curses.COLOR_BLACK)

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            header_height = 5
            footer_height = 3
            available_height = max_y - header_height - footer_height
            items_per_page = max(1, available_height // 3)

            # Adjust scroll offset
            if self.current_index < self.scroll_offset:
                self.scroll_offset = self.current_index
            elif self.current_index >= self.scroll_offset + items_per_page:
                self.scroll_offset = self.current_index - items_per_page + 1
            self.scroll_offset = max(0, self.scroll_offset)

            # Header
            title = "🔧 Select AI Provider"
            stdscr.addstr(0, 0, title[:max_x-1],
                         curses.A_BOLD | curses.color_pair(COLOR_TITLE)
                         if curses.has_colors() else curses.A_BOLD)

            separator = "=" * min(50, max_x - 1)
            stdscr.addstr(1, 0, separator,
                         curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)
            stdscr.addstr(2, 0, "Choose how you want to run AI models:"[:max_x-1],
                         curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)
            stdscr.addstr(4, 0,
                         "💡 ↑↓:Navigate • Enter:Select • Q:Quit"[:max_x-1],
                         curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            # Draw items
            start_y = 6
            visible_start = self.scroll_offset
            visible_end = min(self.scroll_offset + items_per_page, self.total_items)
            self._draw_items(stdscr, max_y, max_x, items_per_page,
                             start_y, visible_start, visible_end)

            # Scroll indicators
            if self.scroll_offset > 0:
                stdscr.addstr(start_y - 1, 0, "  ▲ More above ▲",
                             curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)
            if visible_end < self.total_items:
                indicator_y = start_y + ((visible_end - visible_start) * 3)
                if indicator_y < max_y - 3:
                    stdscr.addstr(indicator_y, 0, "  v More below v",
                                 curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            # Footer
            footer_y = max_y - 2
            if footer_y > start_y and footer_y < max_y:
                stdscr.addstr(footer_y, 0, separator,
                             curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)

            stdscr.refresh()

            key = stdscr.getch()

            if key == curses.KEY_UP:
                if self.current_index > 0:
                    self.current_index -= 1
            elif key == curses.KEY_DOWN:
                if self.current_index < self.total_items - 1:
                    self.current_index += 1
            elif key == curses.KEY_PPAGE:
                self.current_index = max(0, self.current_index - items_per_page)
            elif key == curses.KEY_NPAGE:
                self.current_index = min(self.total_items - 1, self.current_index + items_per_page)
            elif key == curses.KEY_HOME:
                self.current_index = 0
            elif key == curses.KEY_END:
                self.current_index = max(0, self.total_items - 1)
            elif key in [10, 13]:
                return self._handle_enter()
            elif key in [ord('q'), ord('Q'), 27]:
                return None

    def show(self) -> Optional[str]:
        """Show the provider selector"""
        return curses.wrapper(self.run)


class AdvancedSettingsMenu:
    """Curses-based advanced settings menu.

    Currently supports:
      - idle_behavior: "sleep" (default) or "fairy"
    """

    IDLE_OPTIONS = [
        ("sleep", "🛏  Sleep (default)",
         "After 5+ min idle → sleep/restart to free resources"),
        ("fairy", "🧚 Curiosity Fairy",
         "When stuck in a loop → invoke the Curiosity Fairy for a new direction"),
    ]

    def __init__(self):
        self.current_index = 0
        self.scroll_offset = 0
        # Load current value from config
        config = load_config()
        exec_cfg = config.get("execution", {})
        current = exec_cfg.get("idle_behavior", "sleep")
        # Pre-select the current value
        for i, (key, _, _) in enumerate(self.IDLE_OPTIONS):
            if key == current:
                self.current_index = i
                break

    def _save_selection(self, value: str):
        """Persist the selected idle_behavior to config.yaml."""
        config = load_config()
        if "execution" not in config:
            config["execution"] = {}
        config["execution"]["idle_behavior"] = value
        save_config(config)

    def run(self, stdscr) -> Optional[str]:
        curses.curs_set(0)
        stdscr.clear()

        if curses.has_colors():
            curses.start_color()
            curses.init_pair(COLOR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(COLOR_HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(COLOR_NORMAL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FOOTER, curses.COLOR_YELLOW, curses.COLOR_BLACK)

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            header_height = 6
            footer_height = 3
            available_height = max_y - header_height - footer_height
            items_per_page = max(1, available_height // 3)

            if self.current_index < self.scroll_offset:
                self.scroll_offset = self.current_index
            elif self.current_index >= self.scroll_offset + items_per_page:
                self.scroll_offset = self.current_index - items_per_page + 1
            self.scroll_offset = max(0, self.scroll_offset)

            # Header
            title = "⚙️  Advanced Settings"
            stdscr.addstr(0, 0, title[:max_x-1],
                         curses.A_BOLD | curses.color_pair(COLOR_TITLE)
                         if curses.has_colors() else curses.A_BOLD)

            separator = "=" * min(50, max_x - 1)
            stdscr.addstr(1, 0, separator,
                         curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)
            stdscr.addstr(2, 0, "Configure agent behavior:"[:max_x-1],
                         curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)
            stdscr.addstr(3, 0, ""[:max_x-1])
            stdscr.addstr(4, 0,
                         "💡 ↑↓:Navigate • Enter:Save & Back • Q:Back without saving"[:max_x-1],
                         curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            # Section label
            start_y = 6
            stdscr.addstr(start_y, 0, "  Idle Behavior (after 5+ minutes of inactivity):",
                         curses.A_BOLD | curses.color_pair(COLOR_TITLE)
                         if curses.has_colors() else curses.A_BOLD)

            # Draw options
            item_start = start_y + 2
            visible_start = self.scroll_offset
            visible_end = min(self.scroll_offset + items_per_page, len(self.IDLE_OPTIONS))

            for display_idx in range(visible_end - visible_start):
                list_idx = visible_start + display_idx
                if list_idx >= len(self.IDLE_OPTIONS):
                    break

                y = item_start + (display_idx * 3)
                if y >= max_y - 3:
                    break

                is_selected = (list_idx == self.current_index)
                key, name, desc = self.IDLE_OPTIONS[list_idx]

                line1 = f"  {'▶' if is_selected else ' '} {name}"
                line2 = f"     {desc}"

                if is_selected:
                    if len(line1) < max_x:
                        stdscr.addstr(y, 0, line1,
                                     curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD
                                     if curses.has_colors() else curses.A_REVERSE)
                    if len(line2) < max_x:
                        stdscr.addstr(y + 1, 0, line2,
                                     curses.color_pair(COLOR_HIGHLIGHT)
                                     if curses.has_colors() else curses.A_REVERSE)
                else:
                    if len(line1) < max_x:
                        stdscr.addstr(y, 0, line1,
                                     curses.color_pair(COLOR_NORMAL)
                                     if curses.has_colors() else curses.A_NORMAL)
                    if len(line2) < max_x:
                        stdscr.addstr(y + 1, 0, line2,
                                     curses.color_pair(COLOR_NORMAL)
                                     if curses.has_colors() else curses.A_DIM)

            # Footer
            footer_y = max_y - 2
            if footer_y > item_start and footer_y < max_y:
                stdscr.addstr(footer_y, 0, separator,
                             curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)

            stdscr.refresh()

            key = stdscr.getch()

            if key == curses.KEY_UP:
                if self.current_index > 0:
                    self.current_index -= 1
            elif key == curses.KEY_DOWN:
                if self.current_index < len(self.IDLE_OPTIONS) - 1:
                    self.current_index += 1
            elif key in [10, 13]:
                # Enter → save and return
                selected_key = self.IDLE_OPTIONS[self.current_index][0]
                self._save_selection(selected_key)
                return selected_key
            elif key in [ord('q'), ord('Q'), 27]:
                return None

    def show(self) -> Optional[str]:
        return curses.wrapper(self.run)


class MessagingSettingsMenu:
    """Curses-based messaging (Telegram / Discord) settings menu.

    Allows the user to:
      - Enable / disable Telegram bot
      - Enter Telegram bot token
      - Enter Telegram authorized user IDs (comma-separated)
      - Enable / disable Discord bot
      - Enter Discord bot token
      - Enter Discord authorized user IDs (comma-separated)
    """

    PLATFORM_OPTIONS = [
        ("telegram", "📱 Telegram", "Telegram bot via python-telegram-bot"),
        ("discord",  "🎮 Discord",  "Discord bot via discord.py"),
    ]

    def __init__(self):
        self.current_index = 0
        self.scroll_offset = 0
        config = load_config()
        tg = config.get("telegram", {})
        self._tg_enabled = tg.get("enabled", False)
        self._tg_token = tg.get("bot_token", "")
        self._tg_users = ",".join(str(u) for u in tg.get("authorized_users") or tg.get("allowed_user_ids") or [])
        dc = config.get("discord", {})
        self._dc_enabled = dc.get("enabled", False)
        self._dc_token = dc.get("bot_token", "")
        self._dc_users = ",".join(str(u) for u in dc.get("authorized_users") or dc.get("allowed_user_ids") or [])

    def _save_config(self):
        """Persist Telegram & Discord settings to config.yaml.

        Fix #8: Properly save all fields including bot_username, normalize
        user IDs to integers, save output_recipients, and disable the other
        platform to avoid duplicate bot notifications.
        """
        config = load_config()
        if "telegram" not in config:
            config["telegram"] = {}
        config["telegram"]["enabled"] = self._tg_enabled
        config["telegram"]["bot_token"] = self._tg_token.strip()
        # Fix #12: bot_username is not available in this menu's state,
        # but we preserve any existing value in config
        if "bot_username" not in config["telegram"]:
            config["telegram"]["bot_username"] = ""
        # Fix #5: Normalize user IDs to List[int] for consistency
        tg_uids_str = [u.strip() for u in self._tg_users.split(",") if u.strip()]
        tg_uids_int = []
        for uid_str in tg_uids_str:
            try:
                tg_uids_int.append(int(uid_str))
            except ValueError:
                pass
        config["telegram"]["authorized_users"] = tg_uids_int
        config["telegram"]["allowed_user_ids"] = tg_uids_int
        # Fix #8: Save output_recipients for the telegram bot to use
        config["telegram"]["output_recipients"] = tg_uids_int
        # Fix #8: Set telegram_user_id to first recipient if available
        config["telegram"]["telegram_user_id"] = str(tg_uids_int[0]) if tg_uids_int else ""
        if "discord" not in config:
            config["discord"] = {}
        config["discord"]["enabled"] = self._dc_enabled
        config["discord"]["bot_token"] = self._dc_token.strip()
        dc_uids_str = [u.strip() for u in self._dc_users.split(",") if u.strip()]
        dc_uids_int = []
        for uid_str in dc_uids_str:
            try:
                dc_uids_int.append(int(uid_str))
            except ValueError:
                pass
        config["discord"]["authorized_users"] = dc_uids_int
        config["discord"]["allowed_user_ids"] = dc_uids_int
        save_config(config)

    def _inline_input(self, stdscr, y, x, prompt, initial_value="", mask=False):
        """Show a text input field at (y, x) with an initial value."""
        curses.echo()
        curses.curs_set(1)
        max_y, max_x = stdscr.getmaxyx()
        stdscr.addstr(y, x, prompt[:max_x - x - 1],
                      curses.A_BOLD | curses.color_pair(COLOR_TITLE)
                      if curses.has_colors() else curses.A_BOLD)
        input_x = x + len(prompt)
        input_width = max(10, max_x - input_x - 2)
        display_val = initial_value if not mask else "*" * len(initial_value)
        stdscr.addstr(y, input_x, display_val[:input_width],
                      curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)
        cur_pos = len(initial_value)
        stdscr.move(y, input_x + min(cur_pos, input_width - 1))
        stdscr.refresh()
        result = list(initial_value)
        while True:
            ch = stdscr.getch()
            if ch in (10, 13):
                break
            elif ch == 27:
                result = list(initial_value)
                break
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                if cur_pos > 0:
                    cur_pos -= 1
                    result.pop(cur_pos)
            elif ch == curses.KEY_LEFT:
                if cur_pos > 0:
                    cur_pos -= 1
            elif ch == curses.KEY_RIGHT:
                if cur_pos < len(result):
                    cur_pos += 1
            elif 32 <= ch <= 126:
                if cur_pos < 200:
                    result.insert(cur_pos, chr(ch))
                    cur_pos += 1
            display = "".join(result) if not mask else "*" * len(result)
            stdscr.move(y, input_x)
            stdscr.clrtoeol()
            stdscr.addstr(y, input_x, display[:input_width],
                          curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)
            stdscr.move(y, input_x + min(cur_pos, input_width - 1))
            stdscr.refresh()
        curses.noecho()
        curses.curs_set(0)
        return "".join(result)

    def _edit_telegram(self, stdscr):
        """Edit Telegram settings on a full curses screen."""
        curses.curs_set(0)
        enabled = self._tg_enabled
        token = self._tg_token
        users = self._tg_users
        field = 0

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            title = "Telegram Bot Settings"
            stdscr.addstr(0, 0, title[:max_x-1],
                          curses.A_BOLD | curses.color_pair(COLOR_TITLE)
                          if curses.has_colors() else curses.A_BOLD)

            sep = "=" * min(50, max_x - 1)
            stdscr.addstr(1, 0, sep,
                          curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)

            stdscr.addstr(2, 0,
                          "UP/DOWN:Navigate | Enter:Edit | Q/ESC:Save&Back"[:max_x-1],
                          curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            row = 4
            rows = [
                ("enabled", "Telegram Bot", "Enabled" if enabled else "Disabled"),
                ("token",   "Bot Token",
                 (token[:20] + "...") if len(token) > 20 else (token or "(not set)")),
                ("users",   "Auth Users",    users if users else "(anyone)"),
                ("back",    "<- Back",        "Save and return"),
            ]

            for i, (key, label, value) in enumerate(rows):
                y = row + i * 3
                if y >= max_y - 3:
                    break
                sel = (i == field)
                line = f"  {'>' if sel else ' '} {label}: {value}"
                attr = (curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD
                        if curses.has_colors() else curses.A_REVERSE) if sel else \
                       (curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)
                stdscr.addstr(y, 0, line[:max_x-1], attr)

            footer_y = max_y - 2
            if footer_y > row:
                stdscr.addstr(footer_y, 0, sep,
                              curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)
            stdscr.refresh()

            ch = stdscr.getch()
            if ch == curses.KEY_UP:
                field = max(0, field - 1)
            elif ch == curses.KEY_DOWN or ch == 9:
                field = min(len(rows) - 1, field + 1)
            elif ch in (ord('q'), ord('Q'), 27):
                self._tg_enabled = enabled
                self._tg_token = token
                self._tg_users = users
                self._save_config()
                return
            elif ch in (10, 13):
                if field == 0:
                    enabled = not enabled
                elif field == 1:
                    token = self._inline_input(stdscr, 8, 4, "Bot Token: ", token, mask=True)
                    stdscr.clear()
                elif field == 2:
                    users = self._inline_input(stdscr, 11, 4, "User IDs (comma-sep): ", users)
                    stdscr.clear()
                elif field == 3:
                    self._tg_enabled = enabled
                    self._tg_token = token
                    self._tg_users = users
                    self._save_config()
                    return

    def _edit_discord(self, stdscr):
        """Edit Discord settings on a full curses screen."""
        curses.curs_set(0)
        enabled = self._dc_enabled
        token = self._dc_token
        users = self._dc_users
        field = 0

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            title = "Discord Bot Settings"
            stdscr.addstr(0, 0, title[:max_x-1],
                          curses.A_BOLD | curses.color_pair(COLOR_TITLE)
                          if curses.has_colors() else curses.A_BOLD)

            sep = "=" * min(50, max_x - 1)
            stdscr.addstr(1, 0, sep,
                          curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)

            stdscr.addstr(2, 0,
                          "UP/DOWN:Navigate | Enter:Edit | Q/ESC:Save&Back"[:max_x-1],
                          curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            row = 4
            rows = [
                ("enabled", "Discord Bot", "Enabled" if enabled else "Disabled"),
                ("token",   "Bot Token",
                 (token[:20] + "...") if len(token) > 20 else (token or "(not set)")),
                ("users",   "Auth Users",    users if users else "(anyone)"),
                ("back",    "<- Back",        "Save and return"),
            ]

            for i, (key, label, value) in enumerate(rows):
                y = row + i * 3
                if y >= max_y - 3:
                    break
                sel = (i == field)
                line = f"  {'>' if sel else ' '} {label}: {value}"
                attr = (curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD
                        if curses.has_colors() else curses.A_REVERSE) if sel else \
                       (curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)
                stdscr.addstr(y, 0, line[:max_x-1], attr)

            footer_y = max_y - 2
            if footer_y > row:
                stdscr.addstr(footer_y, 0, sep,
                              curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)
            stdscr.refresh()

            ch = stdscr.getch()
            if ch == curses.KEY_UP:
                field = max(0, field - 1)
            elif ch == curses.KEY_DOWN or ch == 9:
                field = min(len(rows) - 1, field + 1)
            elif ch in (ord('q'), ord('Q'), 27):
                self._dc_enabled = enabled
                self._dc_token = token
                self._dc_users = users
                self._save_config()
                return
            elif ch in (10, 13):
                if field == 0:
                    enabled = not enabled
                elif field == 1:
                    token = self._inline_input(stdscr, 8, 4, "Bot Token: ", token, mask=True)
                    stdscr.clear()
                elif field == 2:
                    users = self._inline_input(stdscr, 11, 4, "User IDs (comma-sep): ", users)
                    stdscr.clear()
                elif field == 3:
                    self._dc_enabled = enabled
                    self._dc_token = token
                    self._dc_users = users
                    self._save_config()
                    return

    def run(self, stdscr) -> Optional[str]:
        curses.curs_set(0)
        stdscr.clear()

        if curses.has_colors():
            curses.start_color()
            curses.init_pair(COLOR_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(COLOR_HIGHLIGHT, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(COLOR_NORMAL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(COLOR_FOOTER, curses.COLOR_YELLOW, curses.COLOR_BLACK)

        idx = 0
        total = len(self.PLATFORM_OPTIONS) + 1  # +1 for Back

        while True:
            stdscr.clear()
            max_y, max_x = stdscr.getmaxyx()

            title = "Messaging Settings"
            stdscr.addstr(0, 0, title[:max_x-1],
                          curses.A_BOLD | curses.color_pair(COLOR_TITLE)
                          if curses.has_colors() else curses.A_BOLD)

            sep = "=" * min(50, max_x - 1)
            stdscr.addstr(1, 0, sep,
                          curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)

            stdscr.addstr(2, 0, "Configure bot messaging platforms:"[:max_x-1],
                          curses.color_pair(COLOR_NORMAL) if curses.has_colors() else curses.A_NORMAL)

            stdscr.addstr(4, 0,
                          "UP/DOWN:Navigate | Enter:Configure | Q/ESC:Back"[:max_x-1],
                          curses.color_pair(COLOR_FOOTER) if curses.has_colors() else curses.A_DIM)

            start_y = 6
            for i, (key, name, desc) in enumerate(self.PLATFORM_OPTIONS):
                y = start_y + i * 3
                if y >= max_y - 3:
                    break
                is_sel = (i == idx)
                line1 = f"  {'>' if is_sel else ' '} {name}"
                line2 = f"     {desc}"
                if is_sel:
                    stdscr.addstr(y, 0, line1[:max_x-1],
                                  curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD
                                  if curses.has_colors() else curses.A_REVERSE)
                    stdscr.addstr(y + 1, 0, line2[:max_x-1],
                                  curses.color_pair(COLOR_HIGHLIGHT)
                                  if curses.has_colors() else curses.A_REVERSE)
                else:
                    stdscr.addstr(y, 0, line1[:max_x-1],
                                  curses.color_pair(COLOR_NORMAL)
                                  if curses.has_colors() else curses.A_NORMAL)
                    stdscr.addstr(y + 1, 0, line2[:max_x-1],
                                  curses.color_pair(COLOR_NORMAL)
                                  if curses.has_colors() else curses.A_DIM)

            back_y = start_y + len(self.PLATFORM_OPTIONS) * 3 + 1
            if back_y < max_y - 3:
                is_sel = (idx == len(self.PLATFORM_OPTIONS))
                line = f"  {'>' if is_sel else ' '} <- Back"
                if is_sel:
                    stdscr.addstr(back_y, 0, line[:max_x-1],
                                  curses.color_pair(COLOR_HIGHLIGHT) | curses.A_BOLD
                                  if curses.has_colors() else curses.A_REVERSE)
                else:
                    stdscr.addstr(back_y, 0, line[:max_x-1],
                                  curses.color_pair(COLOR_NORMAL)
                                  if curses.has_colors() else curses.A_NORMAL)

            footer_y = max_y - 2
            if footer_y > start_y:
                stdscr.addstr(footer_y, 0, sep,
                              curses.color_pair(COLOR_TITLE) if curses.has_colors() else curses.A_DIM)
            stdscr.refresh()

            ch = stdscr.getch()
            if ch == curses.KEY_UP:
                idx = max(0, idx - 1)
            elif ch == curses.KEY_DOWN:
                idx = min(total - 1, idx + 1)
            elif ch in (10, 13):
                if idx == 0:
                    self._edit_telegram(stdscr)
                elif idx == 1:
                    self._edit_discord(stdscr)
                elif idx == len(self.PLATFORM_OPTIONS):
                    return None
            elif ch in (ord('q'), ord('Q'), 27):
                return None

    def show(self) -> Optional[str]:
        return curses.wrapper(self.run)


def _get_config_path() -> Path:
    """Resolve config.yaml path, trying multiple locations."""
    # Try the project root relative to this file
    candidate = Path(__file__).resolve().parents[3] / "config.yaml"
    if candidate.exists():
        return candidate
    # Try the current working directory
    candidate = Path.cwd() / "config.yaml"
    if candidate.exists():
        return candidate
    # Fall back to project root (where run.py lives)
    return Path(__file__).resolve().parents[3] / "config.yaml"


def load_config() -> Dict[str, Any]:
    """Load configuration from config.yaml"""
    config_path = _get_config_path()
    if config_path.exists():
        with open(config_path, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config(config: Dict[str, Any]):
    """Save configuration to config.yaml"""
    config_path = _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, 'w') as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def sync_selection_to_config(provider: str, model: str):
    """Sync the selected provider and model to config.yaml"""
    config = load_config()
    
    if "api" not in config:
        config["api"] = {}
    
    config["api"]["preferred_provider"] = provider
    
    if "models" not in config["api"]:
        config["api"]["models"] = {}
    
    config["api"]["models"][provider] = model
    
    save_config(config)


def get_current_config() -> Tuple[Optional[str], Optional[str]]:
    """Get current provider and model from config.yaml"""
    config = load_config()
    api_config = config.get("api", {})
    provider = api_config.get("preferred_provider")
    models = api_config.get("models", {})
    model = models.get(provider) if provider else None
    return provider, model


def select_provider_and_model() -> Tuple[Optional[str], Optional[str]]:
    """
    Main entry point for provider and model selection.
    Returns (provider, model) tuple or (None, None) if cancelled.
    Reads current config to pre-highlight the saved model.

    The provider selector includes two extra items at the bottom:
      - Advanced Settings → configure idle behavior etc.
      - Exit Settings     → save current config and start the agent
    """
    # Read current config to pre-select
    current_provider, current_model = get_current_config()

    while True:
        # Select provider (pre-select if config has one)
        provider_selector = ProviderSelector()
        if current_provider:
            for i, (key, _) in enumerate(provider_selector.providers):
                if key == current_provider:
                    provider_selector.current_index = i
                    break
        selected = provider_selector.show()

        if not selected:
            return None, None

        if selected == ProviderSelector.MESSAGING_SETTINGS:
            # Open messaging settings sub-menu, then loop back to provider selector
            messaging = MessagingSettingsMenu()
            messaging.show()
            continue

        if selected == ProviderSelector.ADVANCED_SETTINGS:
            # Open advanced settings sub-menu, then loop back to provider selector
            advanced = AdvancedSettingsMenu()
            advanced.show()
            # After returning, re-enter the provider selector loop
            # so the user can still pick a provider or exit
            continue

        if selected == ProviderSelector.EXIT_SETTINGS:
            # User wants to exit settings and start the agent with current config
            return current_provider, current_model

        # Normal provider selected — proceed to model selection
        selected_provider = selected
        break

    # Select model for the chosen provider, pre-selecting the saved model
    preselect_model = current_model if selected_provider == current_provider else None
    model_selector = ScrollingModelSelector(selected_provider, preselect_model=preselect_model)
    selected_model = model_selector.show()

    if not selected_model:
        return None, None

    # Sync to config.yaml
    sync_selection_to_config(selected_provider, selected_model)

    return selected_provider, selected_model


def get_available_models(provider: str) -> List[Dict[str, str]]:
    """Get list of available models for a provider"""
    provider_info = PROVIDER_MODELS.get(provider, {})
    return provider_info.get("models", [])


def get_available_providers() -> Dict[str, Dict[str, Any]]:
    """Get all available providers"""
    return PROVIDER_MODELS


if __name__ == "__main__":
    provider, model = select_provider_and_model()
    if provider and model:
        print(f"Selected: {provider} / {model}")
    else:
        print("Selection cancelled")
