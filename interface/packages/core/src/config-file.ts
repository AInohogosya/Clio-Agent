import { randomBytes } from 'node:crypto';
import {
  chmodSync,
  closeSync,
  fchmodSync,
  fsyncSync,
  lstatSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  watch,
  writeFileSync,
} from 'node:fs';
import type { Stats } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';

/**
 * Everything about the *bytes on disk*: where the file lives, who is allowed to
 * own it, how a write is made atomic, and what happens when a read cannot be
 * trusted. What goes into the file — normalisation, revisions, the credential
 * status — belongs to `store.ts`.
 *
 * The rule this module exists to enforce is that a file which cannot be read
 * for a moment is never mistaken for a file that is empty, and is never
 * overwritten because of it.
 */

export const MAX_CONFIG_BYTES = 16_000_000;
export const DEFAULT_CONFIG_DIRECTORY = '.config/project-phone';
export const DEFAULT_CONFIG_BASENAME = 'config.json';

export function defaultConfigPath(): string {
  return join(homedir(), DEFAULT_CONFIG_DIRECTORY, DEFAULT_CONFIG_BASENAME);
}

export function configPath(): string {
  const configured = process.env.PROJECT_PHONE_CONFIG;
  return configured ? resolve(configured) : defaultConfigPath();
}

/** `lstat` so a symlink is seen as a symlink rather than followed to its target. */
export function existingStat(path: string): Stats | null {
  try {
    return lstatSync(path);
  } catch (error) {
    if (error instanceof Error && 'code' in error && error.code === 'ENOENT') return null;
    throw error;
  }
}

/**
 * A stamp that changes whenever the bytes could have changed: modification
 * time, size, and inode — the last because an atomic write replaces the inode
 * even when everything else lands in the same millisecond.
 *
 * Returns null when the file cannot be stat-ed at all, which is a "not now"
 * rather than an "absent": a caller that cannot read the stamp must not treat
 * that as a change, because the bytes may be exactly as they were.
 */
export function readStamp(path: string): string | null {
  try {
    const stat = existingStat(path);
    if (!stat) return 'absent';
    return `${stat.mtimeMs}:${stat.size}:${stat.ino}`;
  } catch {
    return null;
  }
}

function assertOwned(stat: Stats): void {
  if (typeof process.getuid === 'function' && stat.uid !== process.getuid()) {
    throw new Error('config_not_owned_by_user');
  }
}

/**
 * Restores the owner-only mode on a path we already know is ours.
 *
 * A config that has been through an archive, a checkout or a backup restore
 * routinely comes back 0644. That is a problem with the mode, not with the
 * settings inside, so it is tightened rather than treated as damage — losing
 * an API key, a language, a model and a theme to a permission bit would be a
 * far worse outcome than the one being fixed.
 */
function tightenMode(path: string, mode: number): boolean {
  try {
    chmodSync(path, mode);
    return true;
  } catch {
    return false;
  }
}

function isUnsafeTarget(stat: Stats): boolean {
  return stat.isSymbolicLink() || !stat.isFile() || stat.nlink > 1;
}

/**
 * Failures that mean the file is momentarily out of reach rather than damaged.
 * The distinction decides whether the bytes on disk are still worth anything:
 * a file we merely could not read this once is left exactly where it is.
 */
const TRANSIENT_CODES = new Set([
  'EACCES', 'EPERM', 'EBUSY', 'EAGAIN', 'ETXTBSY', 'EMFILE', 'ENFILE',
  'EIO', 'ENOTDIR', 'ELOOP', 'ENAMETOOLONG', 'ENOENT',
]);

export function isTransientFailure(error: unknown): boolean {
  if (!(error instanceof Error) || !('code' in error)) return false;
  return TRANSIENT_CODES.has(String((error as { code?: unknown }).code));
}

function assertSecureParent(path: string): void {
  const parent = dirname(path);
  const stat = existingStat(parent);
  if (!stat) throw new Error('insecure_config_directory_permissions');
  if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error('unsafe_config_directory');
  assertOwned(stat);
  if ((stat.mode & 0o077) !== 0 && !tightenMode(parent, 0o700)) {
    throw new Error('insecure_config_directory_permissions');
  }
}

export function ensureSecureParent(path: string): void {
  const parent = dirname(path);
  if (existingStat(parent)) {
    assertSecureParent(path);
    return;
  }
  mkdirSync(parent, { recursive: true, mode: 0o700 });
  const stat = lstatSync(parent);
  if (stat.isSymbolicLink() || !stat.isDirectory()) throw new Error('unsafe_config_directory');
  assertOwned(stat);
  chmodSync(parent, 0o700);
}

/** The verdict on a file we are about to read: use it, or explain why we cannot. */
export type ConfigFileVerdict =
  | { outcome: 'read'; raw: string }
  | { outcome: 'absent' | 'unreadable' | 'quarantined' };

/**
 * Reads a file we are allowed to trust, or explains why we are not.
 *
 * A symlink, a hard link, or a file belonging to somebody else is a hard
 * failure: it is never followed, never repaired, and never treated as empty.
 * Anything larger than the budget, or anything that will not parse, is moved
 * aside rather than truncated so the settings inside can still be recovered. A
 * read that merely failed this once leaves the file for the next attempt.
 */
export function readTrustedConfigFile(path: string): ConfigFileVerdict {
  let stat: Stats | null;
  try {
    stat = existingStat(path);
    if (!stat) return { outcome: 'absent' };
    if (isUnsafeTarget(stat)) throw new Error('unsafe_config_target');
    assertOwned(stat);
    if ((stat.mode & 0o077) !== 0 && !tightenMode(path, 0o600)) {
      throw new Error('insecure_config_permissions');
    }
    assertSecureParent(path);
    if (stat.size > MAX_CONFIG_BYTES) {
      quarantineConfigFile(path);
      return { outcome: 'quarantined' };
    }
    return { outcome: 'read', raw: readFileSync(path, 'utf8') };
  } catch (error) {
    // Stated explicitly rather than left to fall through: an untrusted target is
    // never a transient failure, and must stay a hard failure even if its code
    // is ever added to the set above.
    if (error instanceof Error && error.message === 'unsafe_config_target') throw error;
    if (isTransientFailure(error)) return { outcome: 'unreadable' };
    throw error;
  }
}

/**
 * Moves an unusable configuration aside instead of overwriting it.
 *
 * A file that cannot be parsed holds settings somebody may still want back, so
 * it is renamed rather than truncated: a corrupt write can then no longer take
 * the API key, language, model and accent with it, and the old contents stay
 * recoverable. If even the rename fails the file is left untouched — refusing
 * to run is better than destroying data on the way to giving up.
 */
export function quarantineConfigFile(path: string): string | null {
  // The random suffix keeps two quarantines in the same millisecond from
  // overwriting each other, which would destroy the very bytes being rescued.
  const target = `${path}.corrupt-${Date.now()}.${randomBytes(4).toString('hex')}`;
  try {
    renameSync(path, target);
    tightenMode(target, 0o600);
    return target;
  } catch {
    return null;
  }
}

export function isConfigObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function removeTemporary(path: string): void {
  try {
    unlinkSync(path);
  } catch (error) {
    // Best effort: the temporary file is already gone after a successful
    // rename, and a file we cannot remove must not fail a write that landed.
    if (!(error instanceof Error) || !('code' in error)) throw error;
  }
}

/** Atomic, owner-only write. The caller has already decided what may be stored. */
export function writeConfigFile(path: string, serialized: string): void {
  ensureSecureParent(path);
  const target = existingStat(path);
  if (target) {
    if (isUnsafeTarget(target)) throw new Error('unsafe_config_target');
    assertOwned(target);
  }
  const temporaryPath = join(dirname(path), `.${basename(path)}.${process.pid}.${randomBytes(8).toString('hex')}.tmp`);
  let fileDescriptor: number | null = null;
  try {
    fileDescriptor = openSync(temporaryPath, 'wx', 0o600);
    writeFileSync(fileDescriptor, serialized, { encoding: 'utf8' });
    fsyncSync(fileDescriptor);
    fchmodSync(fileDescriptor, 0o600);
    closeSync(fileDescriptor);
    fileDescriptor = null;
    renameSync(temporaryPath, path);
    chmodSync(path, 0o600);
  } finally {
    if (fileDescriptor !== null) closeSync(fileDescriptor);
    removeTemporary(temporaryPath);
  }
}

export interface ConfigFileWatch {
  /** Stops watching. Safe to call more than once. */
  close: () => void;
}

/**
 * Watches the directory rather than the file, and reports only real changes.
 *
 * Writes are atomic (write a temporary file, then rename over the target), which
 * replaces the inode, so a watch bound to the old inode would go deaf after the
 * first save. Every stamp is read defensively: a stat that fails is "not now",
 * never "changed", because reporting a change we cannot substantiate would hand
 * the caller a state that was never written.
 */
export function watchConfigFile(path: string, onStamp: (stamp: string) => void): ConfigFileWatch {
  const directory = dirname(path);
  const file = basename(path);
  let stamp = readStamp(path) ?? 'unknown';
  let debounce: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const emit = () => {
    if (closed) return;
    const next = readStamp(path);
    if (next === null || next === stamp) return;
    stamp = next;
    onStamp(stamp);
  };

  const schedule = (name: string | Buffer | null) => {
    if (name !== null && String(name) !== file) return;
    if (debounce) clearTimeout(debounce);
    debounce = setTimeout(emit, 30);
  };

  let watcher: ReturnType<typeof watch> | null = null;
  try {
    watcher = watch(directory, { persistent: false }, (_event, name) => schedule(name));
    watcher.unref?.();
  } catch {
    watcher = null;
  }

  return {
    close: () => {
      closed = true;
      if (debounce) clearTimeout(debounce);
      watcher?.close();
    },
  };
}
