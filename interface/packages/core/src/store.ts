import { timingSafeEqual } from 'node:crypto';
import {
  configPath,
  ensureSecureParent,
  isConfigObject,
  isTransientFailure,
  MAX_CONFIG_BYTES,
  quarantineConfigFile,
  readTrustedConfigFile,
  watchConfigFile,
  writeConfigFile,
} from './config-file.js';
import { isSafeCredential } from './security.js';
import {
  describeCredential,
  nextRevision,
  normalizeSharedState,
  SHARED_STATE_VERSION,
  type SharedState,
  type SurfaceId,
} from './shared-state.js';
import { createSettings, type Settings } from './types.js';

/**
 * What the shared configuration file *means*: which settings are safe to keep,
 * where the credential may come from, which revision a write carries, and what
 * a reader is told when the file cannot be read. The rules about the bytes
 * themselves — ownership, permissions, atomicity — live in `config-file.ts`.
 *
 * Both the terminal client and the local web bridge come through this module,
 * so the two interfaces cannot drift apart on permissions, atomicity, or
 * revisions.
 *
 * This module is exposed as `@project-phone/core/store` and is deliberately
 * kept out of the browser barrel in `index.ts`.
 */

export { MAX_CONFIG_BYTES, configPath, defaultConfigPath } from './config-file.js';
// Re-exported for callers that reach for the revision cap here; the value is
// defined once, next to the state it bounds.
export { MAX_REVISION } from './shared-state.js';

export function environmentApiKey(env: NodeJS.ProcessEnv = process.env): string {
  const value = env.PROJECT_PHONE_API_KEY;
  return isSafeCredential(value) ? value.trim() : '';
}

export function hasEnvironmentApiKey(env: NodeJS.ProcessEnv = process.env): boolean {
  return environmentApiKey(env).length > 0;
}

export function withEnvironmentKey(settings: Settings, env: NodeJS.ProcessEnv = process.env): Settings {
  const environmentKey = environmentApiKey(env);
  return createSettings({ ...settings, apiKey: environmentKey || settings.apiKey });
}

/**
 * The reverse of `withEnvironmentKey`: a session credential belongs to the
 * session, not to the file, so it is taken back out before anything is written.
 * A key that is merely the caller's own is left alone — only the exact value the
 * environment supplied is stripped.
 */
function withoutEnvironmentKey(settings: Settings, env: NodeJS.ProcessEnv = process.env): Settings {
  const environmentKey = environmentApiKey(env);
  return environmentKey && environmentKey === settings.apiKey.trim()
    ? createSettings({ ...settings, apiKey: '' })
    : settings;
}

/** A first run: defaults, an environment credential if there is one, no history. */
export function emptyConfig(env: NodeJS.ProcessEnv = process.env): SharedState {
  const settings = withEnvironmentKey(createSettings(), env);
  return {
    version: SHARED_STATE_VERSION,
    revision: 0,
    updatedAt: 0,
    origin: null,
    transport: 'file',
    settings,
    messages: [],
    credential: describeCredential(settings.apiKey, environmentApiKey(env)),
  };
}

/**
 * Turns whatever was on disk into a state. A file written by an older build is
 * accepted and normalised upwards; the fields this version no longer has are
 * dropped rather than carried.
 */
export function normalizeConfigFile(
  value: unknown,
  env: NodeJS.ProcessEnv = process.env,
): SharedState {
  const state = normalizeSharedState(value, {
    defaultTransport: 'file',
    environmentKey: environmentApiKey(env),
  });
  // The stored credential *status* is never believed. It is a summary of a key
  // that the file does not even hold — the environment key is stripped before
  // every write — so a hand-edited or stale summary would report a key that is
  // not there, or deny one that is. This file is the authority, so the answer
  // is recomputed from the settings and the live environment.
  return { ...state, credential: describeCredential(state.settings.apiKey, environmentApiKey(env)) };
}

/**
 * The state as it is allowed to reach the disk.
 *
 * The environment key is deliberately not threaded into normalisation here:
 * that would re-apply it after `withoutEnvironmentKey` stripped it, putting a
 * session credential on disk. The credential *status* is still recomputed from
 * the live settings, so the in-memory answer about the key stays honest.
 */
function forDisk(config: SharedState, env: NodeJS.ProcessEnv = process.env): SharedState {
  const normalized = normalizeSharedState({
    version: SHARED_STATE_VERSION,
    revision: config.revision,
    updatedAt: config.updatedAt,
    origin: config.origin,
    transport: 'file',
    settings: withoutEnvironmentKey(config.settings, env),
    messages: config.messages,
    credential: config.credential,
  }, { defaultTransport: 'file', environmentKey: '' });
  return {
    ...normalized,
    credential: describeCredential(config.settings.apiKey, environmentApiKey(env)),
  };
}

function parseJsonOrNull(raw: string): unknown {
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/**
 * Reads the shared file.
 *
 * A read only ever narrows what it trusts; it never decides the file is
 * worthless. Unparseable contents are quarantined so they can be recovered, a
 * transient failure leaves the file untouched for the next attempt, and a
 * merely-loose permission is repaired in place. The settings survive all three.
 */
export function readConfig(env: NodeJS.ProcessEnv = process.env): SharedState {
  let path: string;
  try {
    path = configPath();
  } catch (error) {
    if (!isTransientFailure(error)) throw error;
    return emptyConfig(env);
  }

  const verdict = readTrustedConfigFile(path);
  if (verdict.outcome === 'read') {
    const parsed = parseJsonOrNull(verdict.raw);
    if (isConfigObject(parsed)) return normalizeConfigFile(parsed, env);
    // Readable, but not a state file. The bytes may still be somebody's
    // settings, so they are moved aside rather than overwritten.
    quarantineConfigFile(path);
  }
  return emptyConfig(env);
}

/** Atomic, owner-only write. A credential supplied by the environment never reaches disk. */
export function writeConfig(config: SharedState, env: NodeJS.ProcessEnv = process.env): SharedState {
  const safeConfig = forDisk(config, env);
  const serialized = `${JSON.stringify(safeConfig, null, 2)}\n`;
  // Refused before anything is created on disk: an over-large state is a bug in
  // the caller, not a reason to leave a directory behind.
  if (Buffer.byteLength(serialized, 'utf8') > MAX_CONFIG_BYTES) throw new Error('config_too_large');
  writeConfigFile(configPath(), serialized);
  return safeConfig;
}

export interface SaveConfigOptions {
  origin?: SurfaceId;
  now?: number;
  env?: NodeJS.ProcessEnv;
  bumpRevision?: boolean;
}

export function saveConfig(config: SharedState, options: SaveConfigOptions = {}): SharedState {
  const env = options.env ?? process.env;
  const now = typeof options.now === 'number' && Number.isFinite(options.now) ? options.now : Date.now();
  const bump = options.bumpRevision !== false;
  return writeConfig({
    ...config,
    origin: options.origin ?? config.origin ?? 'cli',
    transport: 'file',
    revision: bump ? nextRevision(config) : config.revision,
    updatedAt: now,
  }, env);
}

/**
 * Watches the shared file so a change made by the browser, or by a second
 * phone process, is adopted instead of silently overwritten.
 *
 * A read that fails is swallowed: a concurrent writer can be mid-rename, and
 * the next event settles it. The watch itself never throws — an always-on
 * process that died because one `stat` failed would take the transcript with it.
 */
export function watchConfig(
  onChange: (config: SharedState) => void,
  env: NodeJS.ProcessEnv = process.env,
): () => void {
  const path = configPath();
  try {
    ensureSecureParent(path);
  } catch {
    return () => undefined;
  }
  const watch = watchConfigFile(path, () => {
    try {
      onChange(readConfig(env));
    } catch {
      // A concurrent writer can be mid-rename; the next event settles it.
    }
  });
  return () => watch.close();
}

export function constantTimeEquals(left: string, right: string): boolean {
  const a = Buffer.from(left, 'utf8');
  const b = Buffer.from(right, 'utf8');
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}
