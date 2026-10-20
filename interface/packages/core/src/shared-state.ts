import { isSafeCredential, isSafeTimestamp, MAX_TIMESTAMP } from './security.js';
import { normalizeMessages, normalizeSettings } from './normalize.js';
import { createSettings, type ChatMessage, type Settings } from './types.js';

export const SHARED_STATE_VERSION = 2;
export const MAX_SHARED_MESSAGES = 100;
export const MAX_REVISION = 2_147_483_647;

export type SurfaceId = 'cli' | 'web';
export type CredentialSource = 'none' | 'file' | 'environment';
export type TransportId = 'none' | 'file' | 'bridge';

export interface CredentialStatus {
  present: boolean;
  source: CredentialSource;
  hint: string;
}

export interface SharedState {
  version: number;
  revision: number;
  updatedAt: number;
  origin: SurfaceId | null;
  transport: TransportId;
  settings: Settings;
  messages: ChatMessage[];
  credential: CredentialStatus;
}

function isSurfaceId(value: unknown): value is SurfaceId {
  return value === 'cli' || value === 'web';
}

function isTransportId(value: unknown): value is TransportId {
  return value === 'none' || value === 'file' || value === 'bridge';
}

function isCredentialSource(value: unknown): value is CredentialSource {
  return value === 'none' || value === 'file' || value === 'environment';
}

function safeNumber(value: unknown, fallback: number, max: number): number {
  return isSafeTimestamp(value) && value <= max ? value : fallback;
}

function safeRevision(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0;
  const floored = Math.floor(value);
  if (floored < 0) return 0;
  return floored > MAX_REVISION ? MAX_REVISION : floored;
}

export function credentialHint(value: string): string {
  if (!isSafeCredential(value)) return '';
  const secret = value.trim();
  if (!secret) return '';
  if (secret.length <= 8) return '•'.repeat(secret.length);
  return `••••${secret.slice(-4)}`;
}

export function describeCredential(apiKey: string, environmentKey = ''): CredentialStatus {
  const fromEnvironment = isSafeCredential(environmentKey) ? environmentKey.trim() : '';
  if (fromEnvironment) {
    return { present: true, source: 'environment', hint: credentialHint(fromEnvironment) };
  }
  const stored = isSafeCredential(apiKey) ? apiKey.trim() : '';
  if (stored) return { present: true, source: 'file', hint: credentialHint(stored) };
  return { present: false, source: 'none', hint: '' };
}

export function redactSettings(settings: Settings): Settings {
  return createSettings({ ...settings, apiKey: '' });
}

export function redactCredential(status: CredentialStatus | null | undefined): CredentialStatus {
  if (!status || typeof status !== 'object') return { present: false, source: 'none', hint: '' };
  const present = status.present === true;
  const source = isCredentialSource(status.source) ? status.source : 'none';
  const hint = typeof status.hint === 'string' ? status.hint.slice(0, 32) : '';
  return { present, source, hint };
}

/**
 * Merges two append-only histories. Entries are keyed by id and ordered by
 * creation time; when both sides carry the same id the second argument wins,
 * so callers pass the newer side last.
 */
function unionById<T extends { id: string; createdAt: number }>(local: T[], remote: T[], limit: number): T[] {
  const merged = new Map<string, T>();
  for (const item of local) if (item.id) merged.set(item.id, item);
  for (const item of remote) if (item.id) merged.set(item.id, item);
  return [...merged.values()].sort((left, right) => {
    if (left.createdAt !== right.createdAt) return left.createdAt - right.createdAt;
    return left.id < right.id ? -1 : left.id > right.id ? 1 : 0;
  }).slice(-limit);
}

export function mergeMessages(local: ChatMessage[], remote: ChatMessage[]): ChatMessage[] {
  return unionById(normalizeMessages(local), normalizeMessages(remote), MAX_SHARED_MESSAGES);
}

/**
 * Compares only the non-secret settings fields. The API key is excluded on
 * purpose: a redacted snapshot must never look like "the key was removed".
 */
export function publicSettingsEqual(left: Settings, right: Settings): boolean {
  const a = normalizeSettings(left);
  const b = normalizeSettings(right);
  return a.interface === b.interface
    && a.agentUrl === b.agentUrl
    && a.agentPerson === b.agentPerson
    && a.provider === b.provider
    && a.baseUrl === b.baseUrl
    && a.model === b.model
    && a.language === b.language
    && a.theme === b.theme
    && a.accent === b.accent;
}

export function nextRevision(state: Pick<SharedState, 'revision'>): number {
  return state.revision >= MAX_REVISION ? MAX_REVISION : state.revision + 1;
}

export function normalizeCredential(value: unknown): CredentialStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return { present: false, source: 'none', hint: '' };
  }
  const item = value as Partial<CredentialStatus>;
  return redactCredential({
    present: item.present === true,
    source: isCredentialSource(item.source) ? item.source : 'none',
    hint: typeof item.hint === 'string' ? item.hint : '',
  });
}

export interface SharedStateInput {
  version?: unknown;
  revision?: unknown;
  updatedAt?: unknown;
  origin?: unknown;
  transport?: unknown;
  settings?: unknown;
  messages?: unknown;
  credential?: unknown;
}

export interface NormalizeSharedStateOptions {
  /** A session credential, which wins over whatever the payload carries. */
  environmentKey?: string;
  /** The transport to record when the payload names none. */
  defaultTransport?: TransportId;
}

/**
 * Reads a state out of anything at all: a file on disk, a browser snapshot, a
 * payload from the bridge. Every field is bounded, and a field that is missing
 * or unreadable becomes its default rather than an error — a state that cannot
 * be understood still has to be a state, because the alternative is a surface
 * with nothing to render and nowhere to write.
 */
export function normalizeSharedState(
  value: unknown,
  options: NormalizeSharedStateOptions = {},
): SharedState {
  const source: SharedStateInput = value && typeof value === 'object' && !Array.isArray(value)
    ? (value as SharedStateInput)
    : {};
  const environmentKey = options.environmentKey ?? '';
  const stored = normalizeSettings(source.settings);
  const settings = createSettings({
    ...stored,
    apiKey: (isSafeCredential(environmentKey) ? environmentKey.trim() : '') || stored.apiKey,
  });
  const credential = source.credential !== undefined
    ? normalizeCredential(source.credential)
    : describeCredential(settings.apiKey, environmentKey);
  return {
    version: SHARED_STATE_VERSION,
    revision: safeRevision(source.revision),
    updatedAt: safeNumber(source.updatedAt, 0, MAX_TIMESTAMP),
    origin: isSurfaceId(source.origin) ? source.origin : null,
    transport: isTransportId(source.transport) ? source.transport : (options.defaultTransport ?? 'none'),
    settings,
    messages: normalizeMessages(source.messages).slice(-MAX_SHARED_MESSAGES),
    credential,
  };
}

export interface SharedStateOptions {
  origin: SurfaceId;
  transport?: TransportId;
  environmentKey?: string;
  now?: number;
}

export function createSharedState(
  state: {
    settings: Settings;
    messages?: ChatMessage[];
    revision?: number;
    updatedAt?: number;
  },
  options: SharedStateOptions,
): SharedState {
  const now = isSafeTimestamp(options.now) ? options.now : Date.now();
  const settings = normalizeSettings(state.settings);
  return {
    version: SHARED_STATE_VERSION,
    revision: safeRevision(state.revision),
    updatedAt: isSafeTimestamp(state.updatedAt) ? state.updatedAt : now,
    origin: options.origin,
    transport: options.transport ?? 'none',
    settings,
    messages: normalizeMessages(state.messages).slice(-MAX_SHARED_MESSAGES),
    credential: describeCredential(settings.apiKey, options.environmentKey ?? ''),
  };
}

export function toPublicState(state: SharedState): SharedState {
  return {
    ...state,
    settings: redactSettings(state.settings),
    credential: redactCredential(state.credential),
  };
}

/**
 * Credential status is derived from the one surface that owns the secret store
 * (the config file). A browser snapshot can report what it holds in memory, but
 * it must never overwrite the authoritative answer about the file.
 */
function authoritativeCredential(local: SharedState, remote: SharedState, remoteWins: boolean): CredentialStatus {
  if (local.transport === 'file' && remote.transport !== 'file') return redactCredential(local.credential);
  if (remote.transport === 'file' && local.transport !== 'file') return redactCredential(remote.credential);
  return remoteWins ? redactCredential(remote.credential) : redactCredential(local.credential);
}

export function mergeSharedState(local: SharedState, remote: SharedState): SharedState {
  const remoteWins = remote.revision > local.revision
    || (remote.revision === local.revision && remote.updatedAt > local.updatedAt);
  const winner = remoteWins ? remote : local;
  const settings = publicSettingsEqual(local.settings, remote.settings) ? local.settings : winner.settings;
  return {
    version: SHARED_STATE_VERSION,
    revision: Math.max(local.revision, remote.revision),
    updatedAt: Math.max(local.updatedAt, remote.updatedAt),
    origin: winner.origin,
    transport: local.transport === 'none' ? remote.transport : local.transport,
    settings,
    messages: mergeMessages(local.messages, remote.messages),
    credential: authoritativeCredential(local, remote, remoteWins),
  };
}

export interface SettingsDelta {
  changed: boolean;
  fields: Array<keyof Settings>;
}

export function diffSettings(next: Settings, current: Settings): SettingsDelta {
  const fields: Array<keyof Settings> = [];
  const keys: Array<keyof Settings> = [
    'interface', 'agentUrl', 'agentPerson',
    'provider', 'apiKey', 'baseUrl', 'model', 'language', 'theme', 'accent',
  ];
  for (const key of keys) {
    if (next[key] !== current[key]) fields.push(key);
  }
  return { changed: fields.length > 0, fields };
}
