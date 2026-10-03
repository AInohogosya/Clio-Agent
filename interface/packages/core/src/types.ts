import {
  MAX_ENDPOINT_LENGTH,
  isBoundedString,
  isSafeCredential,
  isSafeIdentifier,
  isSafeModelName,
  isValidAgentEndpoint,
  isValidProviderEndpoint,
} from './security.js';

export const PROVIDER_IDS = [
  'openai',
  'anthropic',
  'gemini',
  'openrouter',
  'deepseek',
  'mistral',
  'groq',
  'xai',
  'ollama',
  'lmstudio',
] as const;
export type ProviderId = (typeof PROVIDER_IDS)[number];

export const PROVIDER_DEFAULTS: Record<ProviderId, { baseUrl: string; model: string }> = Object.freeze({
  openai: Object.freeze({ baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini' }),
  anthropic: Object.freeze({ baseUrl: 'https://api.anthropic.com/v1', model: 'claude-3-5-sonnet-latest' }),
  gemini: Object.freeze({ baseUrl: 'https://generativelanguage.googleapis.com/v1beta', model: 'gemini-2.0-flash' }),
  openrouter: Object.freeze({ baseUrl: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4o-mini' }),
  deepseek: Object.freeze({ baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-chat' }),
  mistral: Object.freeze({ baseUrl: 'https://api.mistral.ai/v1', model: 'mistral-small-latest' }),
  groq: Object.freeze({ baseUrl: 'https://api.groq.com/openai/v1', model: 'llama-3.3-70b-versatile' }),
  xai: Object.freeze({ baseUrl: 'https://api.x.ai/v1', model: 'grok-2-latest' }),
  ollama: Object.freeze({ baseUrl: 'http://localhost:11434/v1', model: 'llama3.2' }),
  lmstudio: Object.freeze({ baseUrl: 'http://localhost:1234/v1', model: 'qwen2.5-7b-instruct' }),
});

export const LANGUAGES = ['en', 'ja', 'zh'] as const;
export type Language = (typeof LANGUAGES)[number];

export const THEMES = ['dark', 'light'] as const;
export type Theme = (typeof THEMES)[number];

/**
 * What the surfaces are talking to.
 *
 * `agent` is an agent: a process with its own state, which decides for itself
 * whether to answer, and whose interface is served locally. `direct` is a model
 * provider: a request in, a completion out, chosen by the reader.
 *
 * The two are not the same program with two addresses. An agent thinks when
 * nobody is talking to it, spends money against caps, keeps a journal it can
 * undo, and may decline; a provider does none of that and must not appear to.
 * So the choice is explicit rather than inferred from whether a key is filled in.
 */
export const INTERFACES = ['agent', 'direct'] as const;
export type InterfaceId = (typeof INTERFACES)[number];

export const MAX_PERSON_LENGTH = 64;

/** Where the agent's local interface service listens by default. */
export const DEFAULT_AGENT_URL = 'http://127.0.0.1:8720';

export const DEFAULT_AGENT_PERSON = 'owner';

export type MessageRole = 'user' | 'assistant' | 'system';
export type ConnectionStatus = 'offline' | 'connecting' | 'online' | 'thinking';

export interface Settings {
  /** What this kit is talking to: an agent, or a model provider directly. */
  interface: InterfaceId;
  /** Base http(s) origin of the agent's interface service. Loopback only. */
  agentUrl: string;
  /** Who the reader is, in the agent's contact book. */
  agentPerson: string;
  provider: ProviderId;
  apiKey: string;
  baseUrl: string;
  model: string;
  language: Language;
  theme: Theme;
  accent: string;
}

/**
 * One line of the transcript.
 *
 * A message is something a person sent or a provider answered. There is no
 * third source, and no `status`: the two were only ever needed to label turns
 * the program invented for itself, and a message that reached the transcript
 * has already been received.
 */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  text: string;
  createdAt: number;
  /**
   * Which door this turn came through, or went out of.
   *
   * Absent means the local line, because a provider-direct turn has no other
   * origin and inventing a label for it would be a claim the program cannot
   * back. It is on this type rather than on a side table because the agent's
   * transcript interleaves channels: once Telegram and WhatsApp are two more
   * doors, a reader looking at one conversation has to be able to say which
   * bubbles belong to it, and a transcript that cannot name its own sources
   * cannot be filtered either.
   */
  channel?: string;
  /**
   * Who sent this, when the agent knows a name for them.
   *
   * Optional for the same reason `channel` is: a turn the agent did not receive
   * from a person has no sender to name, and inventing one is a claim about the
   * world this program cannot back. Where it is present it is a display name or
   * a handle as the channel spells it, never the channel's own id — the id is
   * already carried as the address the reply has to go to.
   */
  author?: string;
  /**
   * Whose conversation this turn belongs to, as the door addresses them.
   *
   * Carried on both directions, because a conversation is both halves of a
   * exchange: an answer with nobody on it is a message from nobody. Without it
   * a transcript of four correspondents is one list of bubbles in which every
   * arrival reads as the reader's own, and there is no way to ask the pane to
   * show one person rather than all of them.
   *
   * Absent means the turn has no addressable sender — an unlabelled local line,
   * or a row from a door that could not name anybody — and a surface groups by
   * the pair rather than by this alone, because the same person is a different
   * conversation on a different door.
   */
  person?: string;
}

export interface ProviderDefinition {
  id: ProviderId;
  label: string;
  description: string;
  defaultBaseUrl: string;
  defaultModel: string;
  requiresApiKey: boolean;
  staticModels: string[];
}

/**
 * A model catalogue somebody fetched, and whether it came from the vendor.
 *
 * One type for both interfaces, because a form that offered "a list of models"
 * had better not care which interface it was on — and because the two are
 * fetched by different parties for the same reason, each holding the credential
 * the other does not.
 *
 * `offline` does not mean empty: it means the list is a fallback, and a caller
 * that presents one as though it were the vendor's own is showing somebody a
 * guess in the shape of a fact. The reason travels with it so a form can say
 * which of "the network is down" and "that key is wrong" it was.
 *
 * `keySource` says which credential the request was made with — one somebody just
 * typed, the one on file, or the provider's environment variable — because
 * "discovered" and "discovered with the key you were already using" are different
 * answers and a person watching a list appear has no way to tell them apart.
 * A name, never a value.
 */
export interface ModelDiscoveryResult {
  models: string[];
  source: 'remote' | 'offline';
  error?: string;
  keySource?: 'typed' | 'stored' | 'environment' | 'none' | string;
}

export interface ClientSnapshot {
  status: ConnectionStatus;
  connected: boolean;
  settings: Settings;
  messages: ChatMessage[];
  lastActivityAt: number | null;
  pending: boolean;
  /** The reply being written right now, for a surface that renders it live. */
  streamingText: string;
  lastError: string | null;
}

export type ClientEvent =
  | { type: 'snapshot'; snapshot: ClientSnapshot }
  | { type: 'settings'; settings: Settings }
  | { type: 'message'; message: ChatMessage }
  | { type: 'delta'; text: string }
  | { type: 'status'; status: ConnectionStatus }
  | { type: 'error'; message: string };

export const DEFAULT_ACCENT = '#F97316';

export const DEFAULT_SETTINGS: Settings = {
  interface: 'agent',
  agentUrl: DEFAULT_AGENT_URL,
  agentPerson: DEFAULT_AGENT_PERSON,
  provider: 'openai',
  apiKey: '',
  baseUrl: PROVIDER_DEFAULTS.openai.baseUrl,
  model: PROVIDER_DEFAULTS.openai.model,
  language: 'en',
  theme: 'dark',
  accent: DEFAULT_ACCENT,
};

function ownValue<T extends object, K extends keyof T>(source: T, key: K): T[K] | undefined {
  return Object.prototype.hasOwnProperty.call(source, key) ? source[key] : undefined;
}

export function createSettings(overrides: Partial<Settings> = {}): Settings {
  const source = overrides && typeof overrides === 'object' && !Array.isArray(overrides) ? overrides : {};
  const interfaceValue = ownValue(source, 'interface');
  const agentUrlValue = ownValue(source, 'agentUrl');
  const agentPersonValue = ownValue(source, 'agentPerson');
  const providerValue = ownValue(source, 'provider');
  const languageValue = ownValue(source, 'language');
  const themeValue = ownValue(source, 'theme');
  const accentValue = ownValue(source, 'accent');
  const apiKeyValue = ownValue(source, 'apiKey');
  const baseUrlValue = ownValue(source, 'baseUrl');
  const modelValue = ownValue(source, 'model');
  const surface = INTERFACES.includes(interfaceValue as InterfaceId)
    ? interfaceValue as InterfaceId
    : DEFAULT_SETTINGS.interface;
  // The agent's address is bounded and loopback-only for the same reason a
  // provider endpoint is: this field is written by a config file and a browser
  // draft alike, and a typo that points at somebody else's machine must not
  // become a place this kit posts a conversation to.
  const requestedAgentUrl = isBoundedString(agentUrlValue, MAX_ENDPOINT_LENGTH)
    ? agentUrlValue.trim()
    : '';
  const agentUrl = isValidAgentEndpoint(requestedAgentUrl)
    ? requestedAgentUrl.replace(/\/+$/, '')
    : DEFAULT_SETTINGS.agentUrl;
  const agentPerson = isSafeIdentifier(agentPersonValue, MAX_PERSON_LENGTH)
    ? agentPersonValue.trim()
    : DEFAULT_SETTINGS.agentPerson;
  const provider = PROVIDER_IDS.includes(providerValue as ProviderId) ? providerValue as ProviderId : DEFAULT_SETTINGS.provider;
  const language = LANGUAGES.includes(languageValue as Language) ? languageValue as Language : DEFAULT_SETTINGS.language;
  const theme = THEMES.includes(themeValue as Theme) ? themeValue as Theme : DEFAULT_SETTINGS.theme;
  const accent = typeof accentValue === 'string' && /^#[0-9a-f]{6}$/i.test(accentValue) ? accentValue : DEFAULT_SETTINGS.accent;
  const apiKey = isSafeCredential(apiKeyValue) ? apiKeyValue.trim() : '';
  const providerDefaults = PROVIDER_DEFAULTS[provider];
  const requestedBaseUrl = isBoundedString(baseUrlValue, MAX_ENDPOINT_LENGTH) ? baseUrlValue.trim() : '';
  const baseUrl = isValidProviderEndpoint(requestedBaseUrl, provider)
    ? requestedBaseUrl.replace(/\/+$/, '')
    : providerDefaults.baseUrl;
  const model = isSafeModelName(modelValue) && modelValue.trim()
    ? modelValue.trim()
    : providerDefaults.model;
  return {
    interface: surface,
    agentUrl,
    agentPerson,
    provider,
    apiKey,
    baseUrl,
    model,
    language,
    theme,
    accent,
  };
}
