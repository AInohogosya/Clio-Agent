"""
Unified Model Definitions for VEXIS-CLI Ollama Integration
Verified against official Ollama library as of 2025
Single source of truth for all model classifications - WITH ICONS
"""

# Essential model families - drastically reduced for simplicity and performance
MODEL_FAMILIES = {
    "meta": {
        "name": "Meta",
        "description": "Meta's Llama family models - Most popular open source foundation models",
        "icon": "🦙",
        "priority": 1,
        "subfamilies": {
            "llama3.1": {
                "name": "Llama 3.1",
                "description": "Enhanced Llama 3.1 models with 128K context",
                "icon": "🚀",
                "models": {
                    "llama3.1:8b": {"name": "Llama 3.1 8B", "desc": "8B parameters • Enhanced • 128K context", "icon": "⚡"},
                    "llama3.1:70b": {"name": "Llama 3.1 70B", "desc": "70B parameters • Enhanced • 128K context", "icon": "🧠"},
                    "llama3.1:latest": {"name": "Llama 3.1 Latest", "desc": "8B parameters • Enhanced • 128K context", "icon": "⭐"},
                }
            },
            "llama3.2": {
                "name": "Llama 3.2",
                "description": "Lightweight Llama 3.2 models for efficiency",
                "icon": "🕊️",
                "models": {
                    "llama3.2:3b": {"name": "Llama 3.2 3B", "desc": "3B parameters • Lightweight • 128K context", "icon": "🕊️"},
                    "llama3.2:1b": {"name": "Llama 3.2 1B", "desc": "1B parameters • Ultra lightweight • 128K context", "icon": "🪶"},
                    "llama3.2:latest": {"name": "Llama 3.2 Latest", "desc": "3B parameters • Lightweight • 128K context", "icon": "⭐"},
                }
            },
            "llama4": {
                "name": "Llama 4",
                "description": "Next generation Llama models with advanced reasoning",
                "icon": "🚀",
                "models": {
                    "llama4:latest": {"name": "Llama 4 Latest", "desc": "Latest Llama 4 • Advanced reasoning • Text", "icon": "⭐"},
                    "llama4:16x17b": {"name": "Llama 4 16x17B", "desc": "272B total • 16x17B MoE • Advanced reasoning", "icon": "🧠"},
                    "llama4:128x17b": {"name": "Llama 4 128x17B", "desc": "2.18T total • 128x17B MoE • Frontier performance", "icon": "👑"},
                }
            }
        }
    },
    "google": {
        "name": "Google",
        "description": "Google's Gemma family models - Efficient high-quality open models",
        "icon": "💎",
        "priority": 2,
        "subfamilies": {
            "gemma2": {
                "name": "Gemma 2",
                "description": "Efficient Gemma 2 models",
                "icon": "💎",
                "models": {
                    "gemma2:2b": {"name": "Gemma 2 2B", "desc": "2B parameters • High-performing • 8K context", "icon": "⚡"},
                    "gemma2:9b": {"name": "Gemma 2 9B", "desc": "9B parameters • High-performing • 8K context", "icon": "🧠"},
                    "gemma2:27b": {"name": "Gemma 2 27B", "desc": "27B parameters • High-performing • 8K context", "icon": "💪"},
                }
            },
            "gemma3": {
                "name": "Gemma 3",
                "description": "Latest generation Gemma models",
                "icon": "🔮",
                "models": {
                    "gemma3:27b": {"name": "Gemma 3 27B", "desc": "27B parameters • Text only • 128K context", "icon": "👑"},
                    "gemma3:12b": {"name": "Gemma 3 12B", "desc": "12B parameters • Text only • 128K context", "icon": "🧠"},
                    "gemma3:4b": {"name": "Gemma 3 4B", "desc": "4B parameters • Text only • 128K context", "icon": "💪"},
                    "gemma3:1b": {"name": "Gemma 3 1B", "desc": "1B parameters • Text only • 32K context", "icon": "🪶"},
                    "gemma3:latest": {"name": "Gemma 3 Latest", "desc": "4B parameters • Text only • 128K context", "icon": "⭐"},
                }
            },
            "gemma3n": {
                "name": "Gemma 3n",
                "description": "Mobile-first architecture optimized for on-device performance and efficiency (text-only)",
                "icon": "📱",
                "models": {
                    "gemma3n:e4b": {"name": "Gemma 3n E4B", "desc": "4B effective • Text only • 32K context", "icon": "🧠"},
                    "gemma3n:e2b": {"name": "Gemma 3n E2B", "desc": "2B effective • Text only • 32K context", "icon": "⚡"},
                    "gemma3n:latest": {"name": "Gemma 3n Latest", "desc": "4B effective • Text only • 32K context", "icon": "⭐"},
                }
            },
            "gemma4": {
                "name": "Gemma 4 (New)",
                "description": "Latest generation Gemma models with frontier-level performance",
                "icon": "💎",
                "models": {
                    "gemma4:e4b": {"name": "Gemma 4 E4B (New)", "desc": "4B effective • Text only • Frontier performance", "icon": "⚡"},
                    "gemma4:e2b": {"name": "Gemma 4 E2B (New)", "desc": "2B effective • Text only • Efficient", "icon": "🪶"},
                    "gemma4:26b": {"name": "Gemma 4 26B (New)", "desc": "26B parameters • Text only • High performance", "icon": "💪"},
                    "gemma4:31b": {"name": "Gemma 4 31B (New)", "desc": "31B parameters • Text only • Advanced", "icon": "🧠"},
                    "gemma4:latest": {"name": "Gemma 4 Latest (New)", "desc": "31B parameters • Text only • Frontier performance", "icon": "⭐"},
                }
            }
        }
    },
    "deepseek": {
        "name": "DeepSeek",
        "description": "DeepSeek's advanced reasoning models - Exceptional reasoning performance",
        "icon": "🔬",
        "priority": 3,
        "subfamilies": {
            "deepseek-r1": {
                "name": "DeepSeek R1",
                "description": "Advanced reasoning models with exceptional performance",
                "models": {
                    "deepseek-r1:32b": {"name": "DeepSeek R1 32B", "desc": "32B parameters • Reasoning • 128K context", "icon": "💪"},
                    "deepseek-r1:14b": {"name": "DeepSeek R1 14B", "desc": "14B parameters • Reasoning • 128K context", "icon": "🌟"},
                    "deepseek-r1:8b": {"name": "DeepSeek R1 8B", "desc": "8B parameters • Reasoning • 128K context", "icon": "⚡"},
                    "deepseek-r1:7b": {"name": "DeepSeek R1 7B", "desc": "7B parameters • Reasoning • 128K context", "icon": "🔹"},
                    "deepseek-r1:1.5b": {"name": "DeepSeek R1 1.5B", "desc": "1.5B parameters • Reasoning • 128K context", "icon": "✨"},
                    "deepseek-r1:latest": {"name": "DeepSeek R1 Latest", "desc": "8B parameters • Reasoning • 128K context", "icon": "⭐"},
                    "deepseek-r1:70b": {"name": "DeepSeek R1 70B", "desc": "70B parameters • Reasoning • 128K context", "icon": "🧠"},
                    "deepseek-r1:671b": {"name": "DeepSeek R1 671B", "desc": "671B parameters • Reasoning • 160K context", "icon": "👑"},
                }
            },
            "deepseek-v3": {
                "name": "DeepSeek V3",
                "description": "Strong Mixture-of-Experts language model with breakthrough inference speed",
                "icon": "🚀",
                "models": {
                    "deepseek-v3:latest": {"name": "DeepSeek V3 Latest", "desc": "671B parameters • 160K context • MoE • Fast inference", "icon": "⭐"},
                    "deepseek-v3:671b": {"name": "DeepSeek V3 671B", "desc": "671B parameters • 160K context • MoE • State-of-the-art", "icon": "👑"},
                }
            },
            "deepseek-v3.1": {
                "name": "DeepSeek V3.1",
                "description": "Enhanced version of DeepSeek V3 with improved capabilities",
                "icon": "🌟",
                "models": {
                    "deepseek-v3.1:latest": {"name": "DeepSeek V3.1 Latest", "desc": "671B parameters • Enhanced • 160K context", "icon": "⭐"},
                    "deepseek-v3.1:671b": {"name": "DeepSeek V3.1 671B", "desc": "671B parameters • Enhanced • 160K context", "icon": "👑"},
                    "deepseek-v3.1:671b-cloud": {"name": "DeepSeek V3.1 671B Cloud", "desc": "671B parameters • Enhanced • Cloud only", "icon": "☁️"},
                }
            },
            "deepseek-v3.2": {
                "name": "DeepSeek V3.2",
                "description": "Latest generation DeepSeek V3 with advanced capabilities and optimizations (cloud only)",
                "icon": "🔥",
                "models": {
                    "deepseek-v3.2:cloud": {"name": "DeepSeek V3.2 Cloud", "desc": "Advanced capabilities • Optimized performance • Cloud only", "icon": "🌟"},
                }
            },
            "deepseek-coder-v2": {
                "name": "DeepSeek Coder V2",
                "description": "Open-source MoE code language model comparable to GPT4-Turbo",
                "icon": "💻",
                "models": {
                    "deepseek-coder-v2:latest": {"name": "DeepSeek Coder V2 Latest", "desc": "16B parameters • 160K context • MoE coding", "icon": "⭐"},
                    "deepseek-coder-v2:16b": {"name": "DeepSeek Coder V2 16B", "desc": "16B parameters • 160K context • Efficient coding", "icon": "💪"},
                    "deepseek-coder-v2:236b": {"name": "DeepSeek Coder V2 236B", "desc": "236B parameters • 4K context • Advanced coding", "icon": "🧠"},
                }
            }
        }
    },
    "microsoft": {
        "name": "Microsoft",
        "description": "Microsoft's Phi family models - Efficient Small Language Models",
        "icon": "🔷",
        "priority": 4,
        "subfamilies": {
            "phi3": {
                "name": "Phi-3",
                "description": "Lightweight state-of-the-art open models",
                "icon": "🧠",
                "models": {
                    "phi3:14b": {"name": "Phi-3 Medium 14B", "desc": "14B parameters • Medium • 128K context", "icon": "💪"},
                    "phi3:3.8b": {"name": "Phi-3 Mini 3.8B", "desc": "3.8B parameters • Mini • 128K context", "icon": "🪶"},
                    "phi3:mini": {"name": "Phi-3 Mini", "desc": "3.8B parameters • Mini • 4K context", "icon": "⚡"},
                    "phi3:medium": {"name": "Phi-3 Medium", "desc": "14B parameters • Medium • 4K context", "icon": "🧠"},
                    "phi3:medium-128k": {"name": "Phi-3 Medium 128K", "desc": "14B parameters • Medium • 128K context", "icon": "💪"},
                    "phi3:latest": {"name": "Phi-3 Latest", "desc": "3.8B parameters • Mini • 128K context", "icon": "⭐"},
                }
            },
            "phi4": {
                "name": "Phi-4",
                "description": "State-of-the-art 14B parameter model",
                "icon": "🚀",
                "models": {
                    "phi4:14b": {"name": "Phi-4 14B", "desc": "14B parameters • State-of-the-art • 16K context", "icon": "🧠"},
                    "phi4:latest": {"name": "Phi-4 Latest", "desc": "14B parameters • State-of-the-art • 16K context", "icon": "⭐"},
                }
            }
        }
    },
    "mistral": {
        "name": "Mistral",
        "description": "Mistral's high-performance models - European open-source AI leader",
        "icon": "🌪️",
        "priority": 5,
        "subfamilies": {
            "mistral": {
                "name": "Mistral",
                "description": "Popular Mistral 7B models",
                "models": {
                    "mistral:7b": {"name": "Mistral 7B", "desc": "7B parameters • Latest • 32K context", "icon": "⚡"},
                    "mistral:latest": {"name": "Mistral 7B Latest", "desc": "7B parameters • Latest • 32K context", "icon": "⭐"}
                }
            },
            "mistral-large": {
                "name": "Mistral Large 2",
                "description": "Mistral's flagship Large 2 model with advanced reasoning",
                "models": {
                    "mistral-large:123b": {"name": "Mistral Large 2 123B", "desc": "123B parameters • 128K context • Advanced reasoning", "icon": "👑"},
                    "mistral-large:latest": {"name": "Mistral Large 2 Latest", "desc": "123B parameters • 128K context • Advanced reasoning", "icon": "⭐"},
                }
            },
            "mistral-large-3": {
                "name": "Mistral Large 3",
                "description": "State-of-the-art mixture-of-experts model for enterprise workloads",
                "models": {
                    "mistral-large-3:675b-cloud": {"name": "Mistral Large 3 675B", "desc": "675B parameters • 256K context • Text • Cloud only", "icon": "🌟"},
                }
            },
            "ministral-3": {
                "name": "Ministral 3",
                "description": "Family designed for edge deployment with wide hardware compatibility",
                "icon": "🪶",
                "models": {
                    "ministral-3:3b": {"name": "Ministral 3 3B", "desc": "3B parameters • Edge • Text only • 256K context", "icon": "🪶"},
                    "ministral-3:8b": {"name": "Ministral 3 8B", "desc": "8B parameters • Edge • Text only • 256K context", "icon": "💪"},
                    "ministral-3:14b": {"name": "Ministral 3 14B", "desc": "14B parameters • Edge • Text only • 256K context", "icon": "🧠"},
                    "ministral-3:latest": {"name": "Ministral 3 Latest", "desc": "8B parameters • Edge • Text only • 256K context", "icon": "⭐"},
                    "ministral-3:3b-cloud": {"name": "Ministral 3 3B Cloud", "desc": "3B parameters • Text only • Cloud only", "icon": "☁️"},
                    "ministral-3:8b-cloud": {"name": "Ministral 3 8B Cloud", "desc": "8B parameters • Text only • Cloud only", "icon": "☁️"},
                    "ministral-3:14b-cloud": {"name": "Ministral 3 14B Cloud", "desc": "14B parameters • Text only • Cloud only", "icon": "☁️"},
                }
            }
        }
    },
    "alibaba": {
        "name": "Alibaba",
        "description": "Alibaba's Qwen family models - Multilingual text models with exceptional performance",
        "icon": "🌟",
        "priority": 6,
        "subfamilies": {
            "qwen2.5": {
                "name": "Qwen 2.5",
                "description": "Multilingual models with 128K context and 18T token training",
                "icon": "🌍",
                "models": {
                    "qwen2.5:32b": {"name": "Qwen 2.5 32B", "desc": "32B parameters • Multilingual • 128K context", "icon": "🧠"},
                    "qwen2.5:14b": {"name": "Qwen 2.5 14B", "desc": "14B parameters • Multilingual • 128K context", "icon": "💪"},
                    "qwen2.5:7b": {"name": "Qwen 2.5 7B", "desc": "7B parameters • Multilingual • 128K context", "icon": "⚡"},
                    "qwen2.5:latest": {"name": "Qwen 2.5 Latest", "desc": "7B parameters • Multilingual • 128K context", "icon": "⭐"},
                    "qwen2.5-coder": {"name": "Qwen 2.5 Coder", "desc": "7B parameters • Code-focused • 128K context", "icon": "💻"},
                    "qwen2.5:3b": {"name": "Qwen 2.5 3B", "desc": "3B parameters • Multilingual • 128K context", "icon": "🪶"},
                    "qwen2.5:1.5b": {"name": "Qwen 2.5 1.5B", "desc": "1.5B parameters • Multilingual • 128K context", "icon": "✨"},
                    "qwen2.5:0.5b": {"name": "Qwen 2.5 0.5B", "desc": "0.5B parameters • Multilingual • 128K context", "icon": "🔹"},
                }
            },
            "qwen3": {
                "name": "Qwen 3",
                "description": "Latest generation dense and MoE models with exceptional performance",
                "icon": "🚀",
                "models": {
                    "qwen3:235b": {"name": "Qwen 3 235B", "desc": "235B parameters • MoE • 256K context", "icon": "👑"},
                    "qwen3:32b": {"name": "Qwen 3 32B", "desc": "32B parameters • Dense • 40K context", "icon": "🧠"},
                    "qwen3:30b": {"name": "Qwen 3 30B", "desc": "30B parameters • MoE • 256K context", "icon": "💪"},
                    "qwen3:14b": {"name": "Qwen 3 14B", "desc": "14B parameters • Dense • 40K context", "icon": "💪"},
                    "qwen3:8b": {"name": "Qwen 3 8B", "desc": "8B parameters • Dense • 40K context", "icon": "⚡"},
                    "qwen3:4b": {"name": "Qwen 3 4B", "desc": "4B parameters • Dense • 256K context", "icon": "🪶"},
                    "qwen3:1.7b": {"name": "Qwen 3 1.7B", "desc": "1.7B parameters • Dense • 40K context", "icon": "✨"},
                    "qwen3:0.6b": {"name": "Qwen 3 0.6B", "desc": "0.6B parameters • Dense • 40K context", "icon": "🔹"},
                    "qwen3:latest": {"name": "Qwen 3 Latest", "desc": "8B parameters • Dense • 40K context", "icon": "⭐"},
                }
            },
            "qwen3-coder": {
                "name": "Qwen 3 Coder",
                "description": "Advanced agentic coding models",
                "icon": "💻",
                "models": {
                    "qwen3-coder:30b": {"name": "Qwen 3 Coder 30B", "desc": "30B parameters • Agentic coding • 256K context", "icon": "🧠"},
                    "qwen3-coder:480b": {"name": "Qwen 3 Coder 480B", "desc": "480B parameters • 256K context • Advanced coding", "icon": "👑"},
                    "qwen3-coder:480b-cloud": {"name": "Qwen 3 Coder 480B Cloud", "desc": "480B parameters • 256K context • Cloud only", "icon": "☁️"},
                    "qwen3-coder:latest": {"name": "Qwen 3 Coder Latest", "desc": "30B parameters • Agentic coding • 256K context", "icon": "⭐"},
                }
            },
            "qwen3.5": {
                "name": "Qwen 3.5 (New)",
                "description": "Open-source text models with exceptional utility and performance",
                "icon": "🌟",
                "models": {
                    "qwen3.5:122b": {"name": "Qwen 3.5 122B (New)", "desc": "122B parameters • Text only • Frontier performance", "icon": "👑"},
                    "qwen3.5:35b": {"name": "Qwen 3.5 35B (New)", "desc": "35B parameters • Text only • Advanced performance", "icon": "🧠"},
                    "qwen3.5:27b": {"name": "Qwen 3.5 27B (New)", "desc": "27B parameters • Text only • High performance", "icon": "💪"},
                    "qwen3.5:9b": {"name": "Qwen 3.5 9B (New)", "desc": "9B parameters • Text only • Exceptional performance", "icon": "🧠"},
                    "qwen3.5:4b": {"name": "Qwen 3.5 4B (New)", "desc": "4B parameters • Text only • Efficient performance", "icon": "💪"},
                    "qwen3.5:2b": {"name": "Qwen 3.5 2B (New)", "desc": "2B parameters • Text only • Lightweight", "icon": "⚡"},
                    "qwen3.5:0.8b": {"name": "Qwen 3.5 0.8B (New)", "desc": "0.8B parameters • Text only • Ultra lightweight", "icon": "🪶"},
                    "qwen3.5:cloud": {"name": "Qwen 3.5 Cloud (New)", "desc": "Text only • Exceptional performance • Cloud only", "icon": "🌟"},
                    "qwen3.5:latest": {"name": "Qwen 3.5 Latest (New)", "desc": "9B parameters • Text only • Exceptional performance", "icon": "⭐"},
                }
            },
            "qwen3-next": {
                "name": "Qwen 3 Next",
                "description": "First installment in Qwen3-Next series with strong parameter efficiency",
                "icon": "🚀",
                "models": {
                    "qwen3-next:80b": {"name": "Qwen 3 Next 80B", "desc": "80B parameters • High efficiency • 256K context", "icon": "🧠"},
                    "qwen3-next:80b-cloud": {"name": "Qwen 3 Next 80B Cloud", "desc": "80B parameters • High efficiency • Cloud only", "icon": "☁️"},
                }
            }
        }
    },
    "bigcode": {
        "name": "BigCode",
        "description": "BigCode's StarCoder family - Open source code generation models",
        "icon": "⭐",
        "priority": 7,
        "subfamilies": {
            "starcoder2": {
                "name": "StarCoder 2",
                "description": "Next generation transparently trained open code LLMs",
                "icon": "🌟",
                "models": {
                    "starcoder2:15b": {"name": "StarCoder 2 15B", "desc": "15B parameters • 600+ languages • 16K context", "icon": "🧠"},
                    "starcoder2:7b": {"name": "StarCoder 2 7B", "desc": "7B parameters • 17 languages • 16K context", "icon": "💪"},
                    "starcoder2:3b": {"name": "StarCoder 2 3B", "desc": "3B parameters • 17 languages • 16K context", "icon": "⚡"},
                    "starcoder2:latest": {"name": "StarCoder 2 Latest", "desc": "3B parameters • 17 languages • 16K context", "icon": "⭐"},
                    "starcoder2:instruct": {"name": "StarCoder 2 Instruct", "desc": "15B parameters • Instruction-tuned • 16K context", "icon": "🎯"},
                }
            }
        }
    },
    "ibm": {
        "name": "IBM",
        "description": "IBM's Granite family - Enterprise-grade open models",
        "icon": "🔷",
        "priority": 8,
        "subfamilies": {
            "granite-code": {
                "name": "Granite Code",
                "description": "Specialized code generation models",
                "icon": "💻",
                "models": {
                    "granite-code:34b": {"name": "Granite Code 34B", "desc": "34B parameters • Code generation • 8K context", "icon": "👑"},
                    "granite-code:20b": {"name": "Granite Code 20B", "desc": "20B parameters • Code generation • 8K context", "icon": "🧠"},
                    "granite-code:8b": {"name": "Granite Code 8B", "desc": "8B parameters • Code generation • 125K context", "icon": "💪"},
                    "granite-code:3b": {"name": "Granite Code 3B", "desc": "3B parameters • Code generation • 125K context", "icon": "⚡"},
                }
            },
            "nemotron-3-nano": {
                "name": "Nemotron 3 Nano",
                "description": "Efficient, open, and intelligent agentic models",
                "icon": "🧠",
                "models": {
                    "nemotron-3-nano:4b": {"name": "Nemotron 3 Nano 4B (New)", "desc": "4B parameters • Text only • Edge optimized", "icon": "⚡"},
                    "nemotron-3-nano:30b": {"name": "Nemotron 3 Nano 30B", "desc": "30B parameters • Text only • 1M context", "icon": "🌟"},
                    "nemotron-3-nano:latest": {"name": "Nemotron 3 Nano Latest", "desc": "30B parameters • Text only • 1M context", "icon": "⭐"},
                    "nemotron-3-nano:30b-cloud": {"name": "Nemotron 3 Nano 30B Cloud", "desc": "30B parameters • Text only • Cloud only", "icon": "☁️"},
                }
            },
            "granite4": {
                "name": "Granite 4",
                "description": "Latest generation with improved instruction following and tool-calling",
                "icon": "🚀",
                "models": {
                    "granite4:latest": {"name": "Granite 4 Latest", "desc": "3B parameters • 128K context • Enterprise-grade", "icon": "⭐"},
                    "granite4:350m": {"name": "Granite 4 350M", "desc": "350M parameters • 32K context • Efficient", "icon": "🪶"},
                    "granite4:1b": {"name": "Granite 4 1B", "desc": "1B parameters • 128K context • Compact", "icon": "✨"},
                    "granite4:3b": {"name": "Granite 4 3B", "desc": "3B parameters • 128K context • Balanced", "icon": "💪"},
                }
            }
        }
    },
    "cohere": {
        "name": "Cohere",
        "description": "Cohere's Command family - Enterprise-ready language models",
        "icon": "🎯",
        "priority": 9,
        "subfamilies": {
            "command-r": {
                "name": "Command R",
                "description": "Retrieval-augmented generation models",
                "icon": "🔍",
                "models": {
                    "command-r:35b": {"name": "Command R 35B", "desc": "35B parameters • RAG capabilities • 128K context", "icon": "🧠"},
                    "command-r:latest": {"name": "Command R Latest", "desc": "35B parameters • RAG capabilities • 128K context", "icon": "⭐"},
                }
            },
            "command-r7b": {
                "name": "Command R7B",
                "description": "Compact 7B parameter models for efficient deployment",
                "icon": "⚡",
                "models": {
                    "command-r7b:7b": {"name": "Command R7B 7B", "desc": "7B parameters • Efficient • 8K context • 23 languages", "icon": "💪"},
                    "command-r7b:latest": {"name": "Command R7B Latest", "desc": "7B parameters • Efficient • 8K context • 23 languages", "icon": "⭐"}
                }
            }
        }
    },
    "01ai": {
        "name": "01.AI",
        "description": "01.AI's Yi family - High-performance bilingual models",
        "icon": "🎭",
        "priority": 10,
        "subfamilies": {
            "yi": {
                "name": "Yi",
                "description": "Bilingual models with strong reasoning capabilities",
                "icon": "🧠",
                "models": {
                    "yi:34b": {"name": "Yi 34B", "desc": "34B parameters • Bilingual • 4K context", "icon": "👑"},
                    "yi:9b": {"name": "Yi 9B", "desc": "9B parameters • Bilingual • 4K context", "icon": "💪"},
                    "yi:6b": {"name": "Yi 6B", "desc": "6B parameters • Bilingual • 4K context", "icon": "⚡"},
                    "yi:latest": {"name": "Yi Latest", "desc": "6B parameters • Bilingual • 4K context", "icon": "⭐"}
                }
            },
            "yi-coder": {
                "name": "Yi Coder",
                "description": "Specialized coding models with long context",
                "icon": "💻",
                "models": {
                    "yi-coder:9b": {"name": "Yi Coder 9B", "desc": "9B parameters • Code-focused • 128K context", "icon": "💪"},
                    "yi-coder:latest": {"name": "Yi Coder Latest", "desc": "9B parameters • Code-focused • 128K context", "icon": "⭐"},
                    "yi-coder:1.5b": {"name": "Yi Coder 1.5B", "desc": "1.5B parameters • Code-focused • 128K context", "icon": "🪶"},
                }
            }
        }
    },
    "specialized": {
        "name": "Specialized Models",
        "description": "Specialized models for specific tasks and capabilities",
        "icon": "🔧",
        "priority": 11,
        "subfamilies": {
            "codestral": {
                "name": "Codestral",
                "description": "Mistral's specialized coding model",
                "icon": "💻",
                "models": {
                    "codestral:22b": {"name": "Codestral 22B", "desc": "22B parameters • Code generation • 32K context", "icon": "🧠"},
                    "codestral:latest": {"name": "Codestral Latest", "desc": "22B parameters • Code generation • 32K context", "icon": "⭐"}
                }
            },
            "hermes3": {
                "name": "Hermes 3",
                "description": "Latest version of flagship Hermes series by Nous Research",
                "icon": "🧠",
                "models": {
                    "hermes3:latest": {"name": "Hermes 3 Latest", "desc": "8B parameters • 128K context • Advanced agentic", "icon": "⭐"},
                    "hermes3:3b": {"name": "Hermes 3 3B", "desc": "3B parameters • 128K context • Efficient", "icon": "🪶"},
                    "hermes3:8b": {"name": "Hermes 3 8B", "desc": "8B parameters • 128K context • Balanced", "icon": "💪"},
                    "hermes3:70b": {"name": "Hermes 3 70B", "desc": "70B parameters • 128K context • Powerful", "icon": "🧠"},
                    "hermes3:405b": {"name": "Hermes 3 405B", "desc": "405B parameters • 128K context • Frontier", "icon": "👑"},
                }
            },
            "wizardlm2": {
                "name": "WizardLM 2",
                "description": "State of the art large language model from Microsoft AI",
                "icon": "🧙‍♂️",
                "models": {
                    "wizardlm2:latest": {"name": "WizardLM 2 Latest", "desc": "7B parameters • 32K context • Fast", "icon": "⭐"},
                    "wizardlm2:7b": {"name": "WizardLM 2 7B", "desc": "7B parameters • 32K context • Efficient", "icon": "⚡"},
                    "wizardlm2:8x22b": {"name": "WizardLM 2 8x22B", "desc": "176B parameters • 64K context • Advanced", "icon": "👑"},
                }
            },
            "reflection": {
                "name": "Reflection",
                "description": "High-performing model trained with Reflection-tuning technique",
                "icon": "🔮",
                "models": {
                    "reflection:latest": {"name": "Reflection Latest", "desc": "70B parameters • 128K context • Self-correcting", "icon": "⭐"},
                    "reflection:70b": {"name": "Reflection 70B", "desc": "70B parameters • 128K context • Advanced reasoning", "icon": "🧠"},
                }
            },
            "devstral-small-2": {
                "name": "Devstral Small 2",
                "description": "24B agentic model for software engineering tasks",
                "icon": "🛠️",
                "models": {
                    "devstral-small-2:latest": {"name": "Devstral Small 2 Latest", "desc": "24B parameters • 384K context • Text • Agentic coding", "icon": "⭐"},
                    "devstral-small-2:24b": {"name": "Devstral Small 2 24B", "desc": "24B parameters • 384K context • Text • Agentic coding", "icon": "💪"},
                    "devstral-small-2:24b-cloud": {"name": "Devstral Small 2 24B Cloud", "desc": "24B parameters • 256K context • Text • Cloud only", "icon": "🌟"},
                }
            }
        }
    },
    "zhipuai": {
        "name": "Zhipu AI",
        "description": "Zhipu AI's GLM family - Advanced Chinese language models with strong reasoning",
        "icon": "🎯",
        "priority": 13,
        "subfamilies": {
            "glm4": {
                "name": "GLM-4",
                "description": "Advanced multilingual models with exceptional reasoning capabilities",
                "icon": "🧠",
                "models": {
                    "glm4:latest": {"name": "GLM-4 Latest", "desc": "9B parameters • 128K context • Text only", "icon": "⭐"},
                    "glm4:9b": {"name": "GLM-4 9B", "desc": "9B parameters • 128K context • Text only", "icon": "🧠"},
                }
            },
            "glm-4.7": {
                "name": "GLM-4.7",
                "description": "Advanced coding capabilities model",
                "icon": "💻",
                "models": {
                    "glm-4.7:cloud": {"name": "GLM-4.7 Cloud", "desc": "Advanced coding • Cloud only", "icon": "🌟"},
                }
            },
            "glm-5": {
                "name": "GLM-5 (New)",
                "description": "Strong reasoning and agentic model with 744B total parameters",
                "icon": "🚀",
                "models": {
                    "glm-5:cloud": {"name": "GLM-5 Cloud (New)", "desc": "744B total parameters • 40B active • Cloud only", "icon": "☁️"},
                }
            },
        }
    },
    "openai": {
        "name": "OpenAI",
        "description": "OpenAI's GPT-OSS family - Open source models with frontier performance",
        "icon": "🤖",
        "priority": 14,
        "subfamilies": {
            "gpt-oss": {
                "name": "GPT-OSS",
                "description": "Open source models with frontier-level performance",
                "icon": "🚀",
                "models": {
                    "gpt-oss:120b": {"name": "GPT-OSS 120B", "desc": "120B parameters • Frontier performance • 128K context", "icon": "👑"},
                    "gpt-oss:20b": {"name": "GPT-OSS 20B", "desc": "20B parameters • High performance • 128K context", "icon": "🧠"},
                    "gpt-oss:120b-cloud": {"name": "GPT-OSS 120B Cloud", "desc": "120B parameters • Frontier performance • Cloud only", "icon": "☁️"},
                    "gpt-oss:20b-cloud": {"name": "GPT-OSS 20B Cloud", "desc": "20B parameters • High performance • Cloud only", "icon": "🌟"},
                    "gpt-oss:latest": {"name": "GPT-OSS Latest", "desc": "20B parameters • High performance • 128K context", "icon": "⭐"},
                }
            }
        }
    },
    "minimax": {
        "name": "MiniMax",
        "description": "MiniMax large language models for productivity and coding",
        "icon": "🚀",
        "priority": 15,
        "subfamilies": {
            "minimax-latest": {
                "name": "Latest Models (New)",
                "description": "Most recent MiniMax models with cutting-edge capabilities",
                "icon": "🔥",
                "models": {
                    "minimax-m2.7:cloud": {"name": "MiniMax M2.7 (Latest) (New)", "desc": "First M2-series model • Agent teams • Complex skills • 200K context • Cloud only", "icon": "🔥"},
                }
            },
            "minimax-productivity": {
                "name": "Productivity Models (New)",
                "description": "High-performance models for productivity and coding tasks",
                "icon": "⚡",
                "models": {
                    "minimax-m2.5:cloud": {"name": "MiniMax M2.5 (New)", "desc": "State-of-the-art • Productivity & coding • Cloud only", "icon": "🌟"},
                }
            },
            "minimax-legacy": {
                "name": "Legacy Models",
                "description": "Previous generation MiniMax models",
                "icon": "📚",
                "models": {
                    "minimax-m2:cloud": {"name": "MiniMax M2 (Legacy)", "desc": "High efficiency • Coding & agentic • Cloud only • Previous generation", "icon": "🧠"},
                }
            }
        }
    },
    "kimi": {
        "name": "Kimi",
        "description": "Kimi text agentic models with advanced capabilities",
        "icon": "🎭",
        "priority": 16,
        "subfamilies": {
            "kimi": {
                "name": "Kimi",
                "description": "Kimi text agentic models with advanced capabilities",
                "icon": "🎭",
                "models": {
                    "kimi-k2.5:cloud": {"name": "Kimi K2.5 Cloud", "desc": "Text agentic • Cloud only", "icon": "🌟"},
                }
            }
        }
    },
    "gemini": {
        "name": "Gemini",
        "description": "Google's Gemini models with frontier intelligence",
        "icon": "💎",
        "priority": 17,
        "subfamilies": {
            "gemini": {
                "name": "Gemini",
                "description": "Google's Gemini models with frontier intelligence",
                "icon": "💎",
                "models": {
                    "gemini-3-flash-preview:cloud": {"name": "Gemini 3 Flash Preview Cloud", "desc": "Frontier intelligence • Built for speed • Cloud only", "icon": "🚀"},
                }
            }
        }
    },
    "rnj": {
        "name": "RNJ",
        "description": "Essential AI's models optimized for code and STEM",
        "icon": "🧪",
        "priority": 18,
        "subfamilies": {
            "rnj": {
                "name": "RNJ",
                "description": "Essential AI's models optimized for code and STEM",
                "icon": "🧪",
                "models": {
                    "rnj-1:8b": {"name": "RNJ-1 8B", "desc": "8B parameters • Code & STEM optimized • 32K context", "icon": "🧠"},
                    "rnj-1:latest": {"name": "RNJ-1 Latest", "desc": "8B parameters • Code & STEM optimized • 32K context", "icon": "⭐"},
                    "rnj-1:8b-cloud": {"name": "RNJ-1 8B Cloud", "desc": "8B parameters • Code & STEM optimized • Cloud only", "icon": "☁️"},
                }
            }
        }
    },
    "cogito": {
        "name": "Cogito",
        "description": "Instruction-tuned generative models with MIT license",
        "icon": "🧠",
        "priority": 19,
        "subfamilies": {
            "cogito": {
                "name": "Cogito",
                "description": "Instruction-tuned generative models with MIT license",
                "icon": "🧠",
                "models": {
                    "cogito-2.1:671b-cloud": {"name": "Cogito 2.1 671B Cloud", "desc": "671B parameters • Instruction-tuned • Cloud only", "icon": "👑"},
                }
            }
        }
    },
    "other": {
        "name": "Other Models",
        "description": "Enter custom model name or search Ollama library",
        "icon": "🔧",
        "priority": 20,
        "subfamilies": {
            "custom": {
                "name": "Custom Model",
                "description": "Enter any valid Ollama model name",
                "models": {
                    "custom-input": {"name": "Enter Model Name", "desc": "Type any valid Ollama model name"},
                }
            }
        }
    }
}

# Flatten all models for backward compatibility
PREDEFINED_MODELS = {}
for family_key, family_data in MODEL_FAMILIES.items():
    for subfamily_key, subfamily_data in family_data["subfamilies"].items():
        for model_key, model_data in subfamily_data["models"].items():
            PREDEFINED_MODELS[model_key] = model_data["desc"]

# Helper functions for accessing model data
def get_model_families():
    """Get model families sorted by priority"""
    return dict(sorted(MODEL_FAMILIES.items(), key=lambda x: x[1]["priority"]))

def get_subfamilies(family_key):
    """Get subfamilies for a specific model family"""
    if family_key in MODEL_FAMILIES:
        return MODEL_FAMILIES[family_key]["subfamilies"]
    return None

def get_models_in_subfamily(family_key, subfamily_key):
    """Get models in a specific subfamily"""
    if (family_key in MODEL_FAMILIES and 
        subfamily_key in MODEL_FAMILIES[family_key]["subfamilies"]):
        return MODEL_FAMILIES[family_key]["subfamilies"][subfamily_key]["models"]
    return None

def get_model_hierarchy_path(model_name):
    """Get hierarchy path for a specific model"""
    for family_key, family_data in MODEL_FAMILIES.items():
        for subfamily_key, subfamily_data in family_data["subfamilies"].items():
            if model_name in subfamily_data["models"]:
                return {
                    "family": family_key,
                    "family_name": family_data["name"],
                    "subfamily": subfamily_key,
                    "subfamily_name": subfamily_data["name"],
                    "model": model_name,
                    "description": subfamily_data["models"][model_name]["desc"]
                }
    return None

def get_predefined_models():
    """Get predefined models with descriptions"""
    return PREDEFINED_MODELS.copy()
