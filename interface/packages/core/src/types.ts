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
  'together',
  'fireworks',
  'deepinfra',
  'cerebras',
  'sambanova',
  'cohere',
  'perplexity',
  'bedrock',
  'azure',
  'vertex',
  'huggingface',
  'replicate',
  'cloudflare',
  'nvidia',
  'siliconflow',
  'nebius',
  'baseten',
  'zhipu',
  'dashscope',
  'moonshot',
  'minimax',
  'lambda',
  'runpod',
  'vastai',
  'coreweave',
  'modal',
  'anyscale',
  'octoai',
  'lepton',
  'fluidstack',
  'jarvislabs',
  'paperspace',
  'watsonx',
  'oci',
  'qianfan',
  'tencent',
  'pangu',
  'volcengine',
  'sensenova',
  'ai21',
  'alephalpha',
  'sakana',
  'reka',
  'lighton',
  'upstage',
  'novita',
  'monsterapi',
  'predibase',
  'hyperbolic',
  'akash',
  'crusoe',
  'rendernetwork',
  'linode',
  'vultr',
  'scaleway',
  'ovhcloud',
  'hetzner',
  'genesis',
  'civo',
  'nimblebox',
  'gradient',
  'beam',
  'mystic',
  'fal',
  'replicateengine',
  'segmind',
  'runhouse',
  'skypilot',
  'foundry',
  'brev',
  'tensordock',
  'oblivious',
  'synthesia',
  'elevenlabs',
  'openaimarketplace',
  'stability',
  'midjourney',
  'leonardo',
  'runwayml',
  'pika',
  'heygen',
  'tavus',
  'hume',
  'assemblyai',
  'deepgram',
  'speechify',
  'coqui',
  'playht',
  'murf',
  'pinecone',
  'weaviate',
  'qdrant',
  'milvus',
  'langchain',
  'llamaindex',
  'voyage',
  'jina',
  'mixedbread',
  'nomic',
  'coherererank',
  'telnyx',
] as const;
export type ProviderId = (typeof PROVIDER_IDS)[number];

/**
 * Where each provider starts. Only OpenAI carries a default model — it is the
 * default provider, and a reader who never changes it should never have to pick
 * one. Every other provider has an endpoint and nothing else: choosing one is a
 * commitment to pick a model from it, and a default a person did not choose is
 * a claim about their intent. Endpoints that name a region, project or account
 * (`bedrock`, `azure`, `vertex`, `cloudflare`, …) start from a placeholder the
 * first request will refuse until the base URL is pointed at the real one.
 */
export const PROVIDER_DEFAULTS: Record<ProviderId, { baseUrl: string; model?: string }> = Object.freeze({
  openai: Object.freeze({ baseUrl: 'https://api.openai.com/v1', model: 'gpt-6.1-sol' }),
  anthropic: Object.freeze({ baseUrl: 'https://api.anthropic.com/v1' }),
  gemini: Object.freeze({ baseUrl: 'https://generativelanguage.googleapis.com/v1beta' }),
  openrouter: Object.freeze({ baseUrl: 'https://openrouter.ai/api/v1' }),
  deepseek: Object.freeze({ baseUrl: 'https://api.deepseek.com/v1' }),
  mistral: Object.freeze({ baseUrl: 'https://api.mistral.ai/v1' }),
  groq: Object.freeze({ baseUrl: 'https://api.groq.com/openai/v1' }),
  xai: Object.freeze({ baseUrl: 'https://api.x.ai/v1' }),
  ollama: Object.freeze({ baseUrl: 'http://localhost:11434/v1' }),
  lmstudio: Object.freeze({ baseUrl: 'http://localhost:1234/v1' }),
  together: Object.freeze({ baseUrl: 'https://api.together.xyz/v1' }),
  fireworks: Object.freeze({ baseUrl: 'https://api.fireworks.ai/inference/v1' }),
  deepinfra: Object.freeze({ baseUrl: 'https://api.deepinfra.com/v1/openai' }),
  cerebras: Object.freeze({ baseUrl: 'https://api.cerebras.ai/v1' }),
  sambanova: Object.freeze({ baseUrl: 'https://api.sambanova.ai/v1' }),
  cohere: Object.freeze({ baseUrl: 'https://api.cohere.ai/compatibility/v1' }),
  perplexity: Object.freeze({ baseUrl: 'https://api.perplexity.ai' }),
  bedrock: Object.freeze({ baseUrl: 'https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1' }),
  azure: Object.freeze({ baseUrl: 'https://YOUR-RESOURCE.openai.azure.com/openai/v1' }),
  vertex: Object.freeze({ baseUrl: 'https://aiplatform.googleapis.com/v1/projects/PROJECT/locations/us-central1/endpoints/openapi' }),
  huggingface: Object.freeze({ baseUrl: 'https://router.huggingface.co/v1' }),
  replicate: Object.freeze({ baseUrl: 'https://api.replicate.com/v1' }),
  cloudflare: Object.freeze({ baseUrl: 'https://api.cloudflare.com/client/v4/accounts/ACCOUNT/ai/v1' }),
  nvidia: Object.freeze({ baseUrl: 'https://integrate.api.nvidia.com/v1' }),
  siliconflow: Object.freeze({ baseUrl: 'https://api.siliconflow.cn/v1' }),
  nebius: Object.freeze({ baseUrl: 'https://api.studio.nebius.ai/v1' }),
  baseten: Object.freeze({ baseUrl: 'https://inference.baseten.co/v1' }),
  zhipu: Object.freeze({ baseUrl: 'https://open.bigmodel.cn/api/paas/v4' }),
  dashscope: Object.freeze({ baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' }),
  moonshot: Object.freeze({ baseUrl: 'https://api.moonshot.cn/v1' }),
  minimax: Object.freeze({ baseUrl: 'https://api.minimax.chat/v1' }),
  lambda: Object.freeze({ baseUrl: 'https://api.lambda.ai/v1' }),
  runpod: Object.freeze({ baseUrl: 'https://api.runpod.ai/v1' }),
  vastai: Object.freeze({ baseUrl: 'https://api.vast.ai/v1' }),
  coreweave: Object.freeze({ baseUrl: 'https://api.coreweave.com/v1' }),
  modal: Object.freeze({ baseUrl: 'https://api.modal.com/v1' }),
  anyscale: Object.freeze({ baseUrl: 'https://api.endpoints.anyscale.com/v1' }),
  octoai: Object.freeze({ baseUrl: 'https://text.octoai.run/v1' }),
  lepton: Object.freeze({ baseUrl: 'https://api.lepton.run/api/v1' }),
  fluidstack: Object.freeze({ baseUrl: 'https://api.fluidstack.io/v1' }),
  jarvislabs: Object.freeze({ baseUrl: 'https://api.jarvislabs.ai/v1' }),
  paperspace: Object.freeze({ baseUrl: 'https://api.paperspace.com/v1' }),
  watsonx: Object.freeze({ baseUrl: 'https://api.us-south.watsonx.ai/v1/ml/v1' }),
  oci: Object.freeze({ baseUrl: 'https://inference.generativeai.us-ashburn-1.oci.oraclecloud.com' }),
  qianfan: Object.freeze({ baseUrl: 'https://qianfan.baidubce.com/v2' }),
  tencent: Object.freeze({ baseUrl: 'https://api.hunyuan.cloud.tencent.com/v1' }),
  pangu: Object.freeze({ baseUrl: 'https://api.modelarts-maas.com/v1' }),
  volcengine: Object.freeze({ baseUrl: 'https://ark.cn-beijing.volces.com/api/v3' }),
  sensenova: Object.freeze({ baseUrl: 'https://api.sensenova.cn/compatible-mode/v1' }),
  ai21: Object.freeze({ baseUrl: 'https://api.ai21.com/studio/v1' }),
  alephalpha: Object.freeze({ baseUrl: 'https://api.aleph-alpha.com' }),
  sakana: Object.freeze({ baseUrl: 'https://api.sakana.ai/v1' }),
  reka: Object.freeze({ baseUrl: 'https://api.reka.ai/v1' }),
  lighton: Object.freeze({ baseUrl: 'https://api.lighton.ai/v1' }),
  upstage: Object.freeze({ baseUrl: 'https://api.upstage.ai/v1/solar' }),
  novita: Object.freeze({ baseUrl: 'https://api.novita.ai/v3/openai' }),
  monsterapi: Object.freeze({ baseUrl: 'https://api.monsterapi.ai/v1' }),
  predibase: Object.freeze({ baseUrl: 'https://serving.app.predibase.com' }),
  hyperbolic: Object.freeze({ baseUrl: 'https://api.hyperbolic.xyz/v1' }),
  akash: Object.freeze({ baseUrl: 'https://api.akash.network/v1' }),
  crusoe: Object.freeze({ baseUrl: 'https://api.crusoecloud.ai/v1' }),
  rendernetwork: Object.freeze({ baseUrl: 'https://api.rendernetwork.com/v1' }),
  linode: Object.freeze({ baseUrl: 'https://ai.linode.com/v1beta' }),
  vultr: Object.freeze({ baseUrl: 'https://api.vultrinference.com/v1' }),
  scaleway: Object.freeze({ baseUrl: 'https://api.scaleway.ai/v1' }),
  ovhcloud: Object.freeze({ baseUrl: 'https://oai.endpoints.kepler.ai.cloud.ovh.net/v1' }),
  hetzner: Object.freeze({ baseUrl: 'https://api.hetzner.cloud/v1' }),
  genesis: Object.freeze({ baseUrl: 'https://api.genesiscloud.com/v1' }),
  civo: Object.freeze({ baseUrl: 'https://api.civo.com/v1' }),
  nimblebox: Object.freeze({ baseUrl: 'https://api.nimblebox.ai/v1' }),
  gradient: Object.freeze({ baseUrl: 'https://api.gradient.ai/v1' }),
  beam: Object.freeze({ baseUrl: 'https://api.beam.cloud/v1' }),
  mystic: Object.freeze({ baseUrl: 'https://api.mystic.ai/v1' }),
  fal: Object.freeze({ baseUrl: 'https://fal.run/v1' }),
  replicateengine: Object.freeze({ baseUrl: 'https://engine.replicate.com/v1' }),
  segmind: Object.freeze({ baseUrl: 'https://api.segmind.com/v1' }),
  runhouse: Object.freeze({ baseUrl: 'https://api.run.house/v1' }),
  skypilot: Object.freeze({ baseUrl: 'https://api.skypilot.co/v1' }),
  foundry: Object.freeze({ baseUrl: 'https://api.foundry.ai/v1' }),
  brev: Object.freeze({ baseUrl: 'https://api.brev.dev/v1' }),
  tensordock: Object.freeze({ baseUrl: 'https://api.tensordock.com/v1' }),
  oblivious: Object.freeze({ baseUrl: 'https://api.oblivious.ai/v1' }),
  synthesia: Object.freeze({ baseUrl: 'https://api.synthesia.io/v1' }),
  elevenlabs: Object.freeze({ baseUrl: 'https://api.elevenlabs.io/v1' }),
  openaimarketplace: Object.freeze({ baseUrl: 'https://api.openai.com/v1' }),
  stability: Object.freeze({ baseUrl: 'https://api.stability.ai/v1' }),
  midjourney: Object.freeze({ baseUrl: 'https://api.midjourneyapi.xyz' }),
  leonardo: Object.freeze({ baseUrl: 'https://cloud.leonardo.ai/api/rest/v1' }),
  runwayml: Object.freeze({ baseUrl: 'https://api.dev.runwayml.com/v1' }),
  pika: Object.freeze({ baseUrl: 'https://api.pika.art/v1' }),
  heygen: Object.freeze({ baseUrl: 'https://api.heygen.com/v1' }),
  tavus: Object.freeze({ baseUrl: 'https://tavusapi.com/v1' }),
  hume: Object.freeze({ baseUrl: 'https://api.hume.ai/v1' }),
  assemblyai: Object.freeze({ baseUrl: 'https://api.assemblyai.com/v2' }),
  deepgram: Object.freeze({ baseUrl: 'https://api.deepgram.com/v1' }),
  speechify: Object.freeze({ baseUrl: 'https://api.speechify.com/v1' }),
  coqui: Object.freeze({ baseUrl: 'https://api.coqui.ai/v1' }),
  playht: Object.freeze({ baseUrl: 'https://api.play.ht/api/v2' }),
  murf: Object.freeze({ baseUrl: 'https://api.murf.ai/v1' }),
  pinecone: Object.freeze({ baseUrl: 'https://api.pinecone.io' }),
  weaviate: Object.freeze({ baseUrl: 'https://inference.weaviate.io' }),
  qdrant: Object.freeze({ baseUrl: 'https://api.qdrant.tech' }),
  milvus: Object.freeze({ baseUrl: 'https://controller.api.zillizcloud.com/v1' }),
  langchain: Object.freeze({ baseUrl: 'https://api.smith.langchain.com/v1' }),
  llamaindex: Object.freeze({ baseUrl: 'https://api.llamaindex.cloud/v1' }),
  voyage: Object.freeze({ baseUrl: 'https://api.voyageai.com/v1' }),
  jina: Object.freeze({ baseUrl: 'https://api.jina.ai/v1' }),
  mixedbread: Object.freeze({ baseUrl: 'https://api.mixedbread.com/v1' }),
  nomic: Object.freeze({ baseUrl: 'https://api.nomic.ai/v1' }),
  coherererank: Object.freeze({ baseUrl: 'https://api.cohere.com/v2' }),
  telnyx: Object.freeze({ baseUrl: 'https://api.telnyx.com/v2/ai' }),
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
  /** Only OpenAI has one: a provider that is not the default has no default model. */
  defaultModel?: string;
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
  model: PROVIDER_DEFAULTS.openai.model ?? '',
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
    : providerDefaults.model ?? '';
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
