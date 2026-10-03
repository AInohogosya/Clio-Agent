import { PROVIDER_DEFAULTS, PROVIDER_IDS, type Language, type ProviderDefinition, type ProviderId } from './types.js';

export const PROVIDER_DEFINITIONS: Record<ProviderId, ProviderDefinition> = {
  openai: {
    id: 'openai',
    label: 'OpenAI',
    description: 'Hosted OpenAI models with a simple chat API.',
    defaultBaseUrl: PROVIDER_DEFAULTS.openai.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.openai.model,
    requiresApiKey: true,
    staticModels: ['gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'o4-mini'],
  },
  anthropic: {
    id: 'anthropic',
    label: 'Anthropic',
    description: 'Claude models through the Anthropic Messages API.',
    defaultBaseUrl: PROVIDER_DEFAULTS.anthropic.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.anthropic.model,
    requiresApiKey: true,
    staticModels: ['claude-3-5-sonnet-latest', 'claude-3-5-haiku-latest', 'claude-3-opus-latest'],
  },
  gemini: {
    id: 'gemini',
    label: 'Google Gemini',
    description: 'Google generative models with a native API shape.',
    defaultBaseUrl: PROVIDER_DEFAULTS.gemini.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.gemini.model,
    requiresApiKey: true,
    staticModels: ['gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-1.5-pro'],
  },
  openrouter: {
    id: 'openrouter',
    label: 'OpenRouter',
    description: 'A single interface to many hosted model providers.',
    defaultBaseUrl: PROVIDER_DEFAULTS.openrouter.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.openrouter.model,
    requiresApiKey: true,
    staticModels: ['openai/gpt-4o-mini', 'anthropic/claude-3.5-sonnet', 'google/gemini-2.0-flash-001'],
  },
  deepseek: {
    id: 'deepseek',
    label: 'DeepSeek',
    description: 'DeepSeek chat and reasoning models, billed cheaply per token.',
    defaultBaseUrl: PROVIDER_DEFAULTS.deepseek.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.deepseek.model,
    requiresApiKey: true,
    staticModels: ['deepseek-chat', 'deepseek-reasoner'],
  },
  mistral: {
    id: 'mistral',
    label: 'Mistral',
    description: 'Mistral’s hosted open and code models over a chat API.',
    defaultBaseUrl: PROVIDER_DEFAULTS.mistral.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.mistral.model,
    requiresApiKey: true,
    staticModels: ['mistral-small-latest', 'mistral-large-latest', 'codestral-latest', 'open-mistral-nemo'],
  },
  groq: {
    id: 'groq',
    label: 'Groq',
    description: 'Very fast hosted inference on open-weight models.',
    defaultBaseUrl: PROVIDER_DEFAULTS.groq.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.groq.model,
    requiresApiKey: true,
    staticModels: ['llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'open-mixtral-8x7b-32768', 'gemma2-9b-it'],
  },
  xai: {
    id: 'xai',
    label: 'xAI',
    description: 'Grok models from xAI through an OpenAI-compatible API.',
    defaultBaseUrl: PROVIDER_DEFAULTS.xai.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.xai.model,
    requiresApiKey: true,
    staticModels: ['grok-2-latest', 'grok-2-mini', 'grok-beta'],
  },
  ollama: {
    id: 'ollama',
    label: 'Local / Ollama',
    description: 'A private endpoint on your machine or LAN.',
    defaultBaseUrl: PROVIDER_DEFAULTS.ollama.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.ollama.model,
    requiresApiKey: false,
    staticModels: ['llama3.2', 'llama3.1', 'mistral', 'qwen2.5'],
  },
  lmstudio: {
    id: 'lmstudio',
    label: 'Local / LM Studio',
    description: 'A local LM Studio server on your machine, with no key required.',
    defaultBaseUrl: PROVIDER_DEFAULTS.lmstudio.baseUrl,
    defaultModel: PROVIDER_DEFAULTS.lmstudio.model,
    requiresApiKey: false,
    staticModels: ['qwen2.5-7b-instruct', 'llama-3.2-3b-instruct', 'gemma-2-2b-it'],
  },
};

export const PROVIDER_LABELS: Record<ProviderId, string> = {
  openai: PROVIDER_DEFINITIONS.openai.label,
  anthropic: PROVIDER_DEFINITIONS.anthropic.label,
  gemini: PROVIDER_DEFINITIONS.gemini.label,
  openrouter: PROVIDER_DEFINITIONS.openrouter.label,
  deepseek: PROVIDER_DEFINITIONS.deepseek.label,
  mistral: PROVIDER_DEFINITIONS.mistral.label,
  groq: PROVIDER_DEFINITIONS.groq.label,
  xai: PROVIDER_DEFINITIONS.xai.label,
  ollama: PROVIDER_DEFINITIONS.ollama.label,
  lmstudio: PROVIDER_DEFINITIONS.lmstudio.label,
};

export function isProviderId(value: unknown): value is ProviderId {
  return typeof value === 'string' && PROVIDER_IDS.includes(value as ProviderId);
}

export function isLanguage(value: unknown): value is Language {
  return value === 'en' || value === 'ja' || value === 'zh';
}

export function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/+$/, '');
}

export function providerFor(provider: ProviderId): ProviderDefinition {
  return isProviderId(provider) ? PROVIDER_DEFINITIONS[provider] : PROVIDER_DEFINITIONS.openai;
}
