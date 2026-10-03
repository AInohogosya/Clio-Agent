import { randomBytes } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Connect, Plugin } from 'vite';
import {
  createSettings,
  discoverProviderModels,
  isLoopbackHost,
  isSafeStoredText,
  isValidProviderEndpoint,
  MAX_PROMPT_LENGTH,
  mergeMessages,
  normalizeSharedState,
  providerFor,
  publicSettingsEqual,
  redactCredential,
  redactSettings,
  requestProviderCompletion,
  SHARED_STATE_VERSION,
  type ChatMessage,
  type CompletionMessage,
  type Settings,
  type SharedState,
} from '@project-phone/core';
import {
  configPath,
  constantTimeEquals,
  environmentApiKey,
  MAX_CONFIG_BYTES,
  hasEnvironmentApiKey,
  readConfig,
  saveConfig,
  watchConfig,
} from '@project-phone/core/store';
import { BRIDGE_PREFIX, bridgePath, TOKEN_HEADER, TOKEN_QUERY_KEY } from './src/bridge-protocol';

/**
 * The local bridge: the only thing that lets a page in a browser reach the same
 * configuration file, transcript and provider credential as the terminal.
 *
 * It lives outside `vite.config.ts` because it is the security boundary of the
 * whole browser surface, and that boundary deserves to be one file a reader can
 * hold in their head: who is answered (loopback peer, loopback authority,
 * same-machine origin), what proves the request came from the served document
 * (a per-process token), and what is never sent back (the credential itself).
 *
 * Everything else here exists so that the browser is not a second-class
 * surface. A local provider needs no key and must not be refused for lacking
 * one; changing provider must be allowed, because refusing it locked anyone with
 * a hosted key on file out of their own machine's models; and a completion must
 * stream, because a page that cannot stream is a page that watches a spinner.
 */

const MAX_BODY_MS = 15_000;
const MAX_HISTORY_ENTRIES = 24;
const MAX_HISTORY_ENTRY_LENGTH = 8_000;
const MAX_EVENT_CLIENTS = 16;
const MAX_CONFIG_PATH_LENGTH = 512;
const MAX_CATALOGUE_ENTRIES = 400;

/**
 * The body budget, matched to what the file itself accepts.
 *
 * The cap used to be a fraction of `MAX_CONFIG_BYTES`, which meant a long
 * conversation synced for a while and then silently stopped: the page kept
 * working against its own copy and the two interfaces quietly diverged.
 */
const MAX_BODY_BYTES = MAX_CONFIG_BYTES;

interface BridgeState {
  token: string;
  state: SharedState;
  clients: Set<ServerResponse>;
  stopWatching: () => void;
}

/**
 * Judges a peer's address, unwrapping the IPv4-mapped IPv6 form that a dual
 * stack socket reports for an IPv4 peer.
 */
export function isLoopbackRemoteAddress(address: string | undefined): boolean {
  return isLoopbackAddress(address ?? '');
}

function isLoopbackRequest(request: IncomingMessage): boolean {
  return isLoopbackRemoteAddress(request.socket?.remoteAddress);
}

/** Delegates to the shared definition, including the IPv4-mapped IPv6 forms. */
export function isLoopbackAddress(host: string): boolean {
  const candidate = host.trim().toLowerCase().replace(/^\[|\]$/g, '');
  if (!candidate) return false;
  return isLoopbackHost(candidate);
}

/**
 * The authority a request claims to be for, e.g. `127.0.0.1:3000`.
 *
 * This is a security boundary, not a routing hint. `Host` is supplied by the
 * client, so a page on another origin can point a request at the loopback
 * server under a name it controls — the DNS-rebinding shape. Vite's own host
 * check runs *after* plugin middleware, so this bridge cannot rely on it: a
 * request is only answered when the claimed authority is this machine.
 */
export function isLoopbackAuthority(authority: string | undefined): boolean {
  if (!authority) return false;
  const trimmed = authority.trim();
  if (!trimmed) return false;
  if (trimmed.startsWith('[')) {
    const end = trimmed.indexOf(']');
    if (end < 0) return false;
    const rest = trimmed.slice(end + 1);
    if (rest && !/^:\d{1,5}$/.test(rest)) return false;
    return isLoopbackAddress(trimmed.slice(1, end));
  }
  const colon = trimmed.indexOf(':');
  if (colon === -1) return isLoopbackAddress(trimmed);
  const port = trimmed.slice(colon);
  if (!/^:\d{1,5}$/.test(port)) return false;
  return isLoopbackAddress(trimmed.slice(0, colon));
}

/**
 * A cross-site request is only tolerated when it is a same-machine one. The
 * origin is never compared against `Host`, because both are client-supplied;
 * requiring each to name loopback is strictly stronger and also accepts the
 * legitimate `localhost` / `127.0.0.1` mix of a single browser.
 */
export function isTrustedOrigin(request: IncomingMessage): boolean {
  const origin = request.headers.origin;
  if (origin === undefined) return true;
  if (typeof origin !== 'string' || !origin) return false;
  let parsed: URL;
  try {
    parsed = new URL(origin);
  } catch {
    return false;
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
  if (parsed.username || parsed.password) return false;
  return isLoopbackAuthority(parsed.host);
}

function splitPath(rawUrl: string): { path: string; query: URLSearchParams } {
  const separator = rawUrl.indexOf('?');
  if (separator === -1) return { path: rawUrl, query: new URLSearchParams() };
  return { path: rawUrl.slice(0, separator), query: new URLSearchParams(rawUrl.slice(separator + 1)) };
}

function readToken(request: IncomingMessage, query: URLSearchParams, allowQuery: boolean): string {
  const header = request.headers[TOKEN_HEADER];
  const fromHeader = (Array.isArray(header) ? header[0] : header) ?? '';
  if (typeof fromHeader === 'string' && fromHeader) return fromHeader;
  if (!allowQuery) return '';
  // `EventSource` cannot attach a header, so the stream is the one route that
  // accepts the token in the query string. `no-referrer` keeps it out of logs.
  return query.get(TOKEN_QUERY_KEY) ?? '';
}

class BodyTooLargeError extends Error {}
class BodyTimeoutError extends Error {}

/**
 * Reads a bounded request body. The declared length is rejected before a byte
 * is read, the stream is abandoned rather than destroyed (destroying first
 * truncates the 413 we are about to write), and a stalled upload cannot hold a
 * socket and a promise open forever.
 */
function readBody(request: IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => {
    const declared = Number(request.headers['content-length']);
    if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) {
      request.resume();
      reject(new BodyTooLargeError('body_too_large'));
      return;
    }
    let size = 0;
    let settled = false;
    const chunks: Buffer[] = [];
    const finish = (error: Error | null, value = ''): void => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      request.off('data', onData);
      request.off('end', onEnd);
      request.off('error', onError);
      request.off('aborted', onAborted);
      if (error) reject(error);
      else resolve(value);
    };
    const onData = (chunk: Buffer): void => {
      size += chunk.byteLength;
      if (size > MAX_BODY_BYTES) {
        request.pause();
        finish(new BodyTooLargeError('body_too_large'));
        return;
      }
      chunks.push(chunk);
    };
    const onEnd = (): void => finish(null, Buffer.concat(chunks).toString('utf8'));
    const onError = (): void => finish(new BodyTimeoutError('body_failed'));
    const onAborted = (): void => finish(new BodyTimeoutError('body_aborted'));
    const timer = setTimeout(() => {
      request.pause();
      finish(new BodyTimeoutError('body_timeout'));
    }, MAX_BODY_MS);
    request.on('data', onData);
    request.on('end', onEnd);
    request.on('error', onError);
    request.on('aborted', onAborted);
  });
}

/** A response is written at most once, and never after the socket is gone. */
function respond(response: ServerResponse, status: number, payload: unknown, close = false): void {
  if (response.writableEnded || response.destroyed) return;
  const headers: Record<string, string> = {
    'cache-control': 'no-store',
    'content-security-policy': "default-src 'none'; frame-ancestors 'none'",
    'content-type': 'application/json; charset=utf-8',
    'referrer-policy': 'no-referrer',
    'x-content-type-options': 'nosniff',
    'x-frame-options': 'DENY',
  };
  if (close) headers.connection = 'close';
  response.writeHead(status, headers);
  response.end(JSON.stringify(payload));
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function readString(source: Record<string, unknown>, key: string, maxLength: number): string {
  const value = source[key];
  return typeof value === 'string' && value.length <= maxLength ? value : '';
}

function boundedConfigPath(): string {
  const path = configPath();
  return path.length > MAX_CONFIG_PATH_LENGTH ? `${path.slice(0, MAX_CONFIG_PATH_LENGTH)}…` : path;
}

function publicSnapshot(state: SharedState): unknown {
  return {
    version: SHARED_STATE_VERSION,
    revision: state.revision,
    updatedAt: state.updatedAt,
    origin: state.origin,
    // From the page's point of view the bridge is always the transport.
    transport: 'bridge',
    settings: redactSettings(state.settings),
    credential: redactCredential(state.credential),
    messages: state.messages,
    configFile: boundedConfigPath(),
    credentialSource: hasEnvironmentApiKey() ? 'environment' : state.credential.present ? 'file' : 'none',
  };
}

function broadcast(bridge: BridgeState, payload: unknown): void {
  const frame = `data: ${JSON.stringify(payload)}\n\n`;
  for (const client of [...bridge.clients]) {
    try {
      client.write(frame);
    } catch {
      bridge.clients.delete(client);
    }
  }
}

type MergeOutcome =
  | { ok: true; state: SharedState; changed: boolean }
  | { ok: false; error: string };

/**
 * Folds a snapshot authored by the browser into the shared file.
 *
 * Non-secret settings and the transcript are synchronised; the transcript is
 * merged as an append-only union so nothing is lost when both surfaces write.
 * A credential the browser supplies is written to the owner-only file for the
 * terminal to use, and is never returned in a read. A browser cannot clear a
 * stored key by omission, because a redacted snapshot also sends an empty one;
 * `phone config unset apiKey` is the deliberate way to remove it.
 *
 * The one invariant worth protecting is that a key is never posted to an
 * endpoint it was not issued for. Changing provider therefore *drops* the key
 * unless the browser supplies the one it wants, which is safe and unblocks
 * switching to a local endpoint — where the previous rule simply refused, since
 * a keyless provider can never satisfy "supply the credential that goes with
 * it". The page is never handed the key, so it also may not choose to keep
 * pointing the file's key at an address of its own choosing.
 */
export function mergeIncoming(current: SharedState, incoming: unknown): MergeOutcome {
  const record = asRecord(incoming) ?? {};
  const settingsSource = asRecord(record.settings) ?? {};
  const fromBrowser = normalizeSharedState({ settings: settingsSource }, { environmentKey: '' }).settings;
  const suppliedKey = readString(settingsSource, 'apiKey', 512);
  const providerChanged = fromBrowser.provider !== current.settings.provider;
  const apiKey = providerChanged ? suppliedKey : (suppliedKey || current.settings.apiKey);
  const settings = createSettings({ ...fromBrowser, apiKey });

  const messages = Array.isArray(record.messages) ? record.messages as unknown[] : current.messages;
  const merged = {
    ...current,
    origin: 'web' as const,
    transport: 'bridge' as const,
    settings,
    messages: Array.isArray(record.messages)
      ? mergeMessages(current.messages, messages as ChatMessage[])
      : current.messages,
  };
  const unchanged = !providerChanged
    && publicSettingsEqual(current.settings, merged.settings)
    && current.settings.apiKey.trim() === merged.settings.apiKey.trim()
    && sameIds(current.messages, merged.messages);
  return { ok: true, state: merged, changed: !unchanged };
}

function sameIds(left: ChatMessage[], right: ChatMessage[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((item, index) => item.id === right[index]?.id);
}

function readHistory(record: Record<string, unknown>): CompletionMessage[] {
  if (!Array.isArray(record.history)) return [];
  return record.history.slice(-MAX_HISTORY_ENTRIES).flatMap((item) => {
    const entry = asRecord(item);
    const role = entry?.role;
    const content = typeof entry?.content === 'string' ? entry.content : '';
    if (!content || (role !== 'user' && role !== 'assistant' && role !== 'system')) return [];
    return [{
      role,
      content: isSafeStoredText(content, MAX_PROMPT_LENGTH) ? content.slice(0, MAX_HISTORY_ENTRY_LENGTH) : '',
    }];
  });
}

/**
 * The browser is never handed the key, so a completion is signed with the copy
 * that only ever lived in the owner-only file (or the environment, which the
 * file never receives).
 */
function withStoredCredential(settings: Settings): Settings {
  const environmentKey = environmentApiKey();
  if (environmentKey) return createSettings({ ...settings, apiKey: environmentKey });
  if (settings.apiKey.trim()) return settings;
  const stored = readConfig();
  return stored.settings.apiKey.trim()
    ? createSettings({ ...settings, apiKey: stored.settings.apiKey })
    : settings;
}

/**
 * Whether a completion can be attempted at all.
 *
 * The credential test is conditional on the provider needing one. Ollama and LM
 * Studio are the point of running this locally, and answering them with
 * `missing_credentials` made the browser surface useless for exactly the
 * providers that need no account.
 */
function canComplete(settings: Settings): boolean {
  return !providerFor(settings.provider).requiresApiKey || hasEnvironmentApiKey();
}

/**
 * Bridges the browser to the same configuration file the terminal uses.
 *
 * Security posture: only loopback clients under a loopback authority are
 * answered, every request must carry a per-process token that is injected into
 * the served document, and no response ever contains the API key.
 */
export function phoneBridge(): Plugin {
  let bridge: BridgeState | null = null;
  // Minted up front so the token can be compared before anything else happens:
  // an unauthenticated request must not be able to make the bridge touch the
  // configuration file.
  const token = randomBytes(32).toString('base64url');

  const ensure = (): BridgeState => {
    if (bridge) return bridge;
    const created: BridgeState = {
      token,
      state: readConfig(),
      clients: new Set(),
      stopWatching: () => undefined,
    };
    created.stopWatching = watchConfig((remote) => {
      const previous = created.state;
      const next: SharedState = {
        ...remote,
        transport: 'bridge',
        messages: mergeMessages(previous.messages, remote.messages),
      };
      if (next.revision === previous.revision && next.updatedAt === previous.updatedAt) return;
      created.state = next;
      broadcast(created, { type: 'state', state: publicSnapshot(next) });
    });
    bridge = created;
    return created;
  };

  const publish = (active: BridgeState, state: SharedState): SharedState => {
    active.state = state;
    broadcast(active, { type: 'state', state: publicSnapshot(state) });
    return state;
  };

  const middleware: Connect.NextHandleFunction = (request, response, next) => {
    const rawUrl = request.url ?? '';
    if (!rawUrl.startsWith(BRIDGE_PREFIX)) {
      next();
      return;
    }
    const { path, query } = splitPath(rawUrl);
    if (!isLoopbackRequest(request)
      || !isLoopbackAuthority(request.headers.host)
      || !isTrustedOrigin(request)) {
      respond(response, 403, { error: 'forbidden' }, true);
      return;
    }
    // Reachable before the token is known: the dev server injects it into the
    // document, but `vite preview` serves the built file unchanged, so the page
    // asks for it here. The guards above already demand a loopback peer, a
    // loopback authority and a same-machine origin, which a cross-site page
    // cannot satisfy, and a cross-origin script cannot read the answer.
    if (path === bridgePath('session') && request.method === 'GET') {
      const active = ensure();
      respond(response, 200, {
        token,
        revision: active.state.revision,
        configFile: boundedConfigPath(),
        credentialSource: hasEnvironmentApiKey() ? 'environment' : active.state.credential.present ? 'file' : 'none',
        state: publicSnapshot(active.state),
      });
      return;
    }

    const isEventStream = path === bridgePath('events');
    if (!constantTimeEquals(readToken(request, query, isEventStream), token)) {
      respond(response, 401, { error: 'unauthorized' }, true);
      return;
    }
    const active = ensure();

    if (path === bridgePath('state') && request.method === 'GET') {
      respond(response, 200, { state: publicSnapshot(active.state) });
      return;
    }

    if (path === bridgePath('state') && request.method === 'POST') {
      void readBody(request).then(
        (raw) => {
          let parsed: unknown = {};
          if (raw) {
            try {
              parsed = JSON.parse(raw) as unknown;
            } catch {
              respond(response, 400, { error: 'bad_json' });
              return;
            }
          }
          const merged = mergeIncoming(active.state, parsed);
          if (!merged.ok) {
            respond(response, 409, { error: merged.error });
            return;
          }
          // A push that changes nothing is answered without touching the disk.
          // Two surfaces each re-sending their view on every unrelated change
          // would otherwise churn the revision forever, and every write is a
          // rename plus a directory event the other surface has to read back.
          if (!merged.changed) {
            respond(response, 200, { state: publicSnapshot(active.state) });
            return;
          }
          respond(response, 200, { state: publicSnapshot(publish(active, saveConfig(merged.state, { origin: 'web' }))) });
        },
        (error: unknown) => {
          respond(
            response,
            error instanceof BodyTooLargeError ? 413 : 408,
            { error: error instanceof Error ? error.message : 'body_failed' },
            true,
          );
        },
      );
      return;
    }

    if (isEventStream && request.method === 'GET') {
      if (active.clients.size >= MAX_EVENT_CLIENTS) {
        respond(response, 503, { error: 'too_many_clients' });
        return;
      }
      response.writeHead(200, {
        'cache-control': 'no-store',
        connection: 'keep-alive',
        'content-type': 'text/event-stream; charset=utf-8',
        'referrer-policy': 'no-referrer',
        'x-accel-buffering': 'no',
        'x-content-type-options': 'nosniff',
        'x-frame-options': 'DENY',
      });
      response.write('retry: 2000\n\n');
      response.write(`data: ${JSON.stringify({ type: 'state', state: publicSnapshot(active.state) })}\n\n`);
      active.clients.add(response);
      request.on('close', () => active.clients.delete(response));
      return;
    }

    if (path === bridgePath('models') && request.method === 'GET') {
      const settings = withStoredCredential(active.state.settings);
      void discoverProviderModels(settings).then(
        (result) => respond(response, 200, {
          models: result.models.slice(0, MAX_CATALOGUE_ENTRIES),
          source: result.source,
          ...(result.error ? { error: result.error } : {}),
        }),
        () => respond(response, 200, {
          models: providerFor(settings.provider).staticModels,
          source: 'offline',
        }),
      );
      return;
    }

    if (path === bridgePath('complete') && request.method === 'POST') {
      void readBody(request).then(
        async (raw) => {
          let record: Record<string, unknown> = {};
          if (raw) {
            try {
              record = asRecord(JSON.parse(raw) as unknown) ?? {};
            } catch {
              respond(response, 400, { error: 'bad_json' });
              return;
            }
          }
          const prompt = readString(record, 'prompt', MAX_PROMPT_LENGTH);
          if (!prompt.trim()) {
            respond(response, 400, { error: 'missing_prompt' });
            return;
          }
          const base = withStoredCredential(active.state.settings);
          if (!isValidProviderEndpoint(base.baseUrl, base.provider)) {
            respond(response, 400, { error: 'invalid_endpoint' });
            return;
          }
          if (!canComplete(base)) {
            respond(response, 409, { error: 'missing_credentials' });
            return;
          }
          // The reply is streamed as newline-delimited JSON, so the page draws
          // the answer as it is written and an abandoned request costs the
          // provider one aborted connection rather than a full answer nobody
          // will read.
          if (response.writableEnded || response.destroyed) return;
          response.writeHead(200, {
            'cache-control': 'no-store',
            'content-type': 'application/x-ndjson; charset=utf-8',
            'referrer-policy': 'no-referrer',
            'x-content-type-options': 'nosniff',
            'x-frame-options': 'DENY',
          });
          const controller = new AbortController();
          // If the page goes away mid-answer, the provider request goes with it.
          response.on('close', () => controller.abort());
          const frame = (payload: unknown): void => {
            if (response.writableEnded || response.destroyed) return;
            response.write(`${JSON.stringify(payload)}\n`);
          };
          try {
            const text = await requestProviderCompletion(
              base,
              prompt,
              fetch,
              controller.signal,
              readHistory(record),
              { onDelta: (increment) => frame({ type: 'delta', text: increment }) },
            );
            frame({ type: 'done', text });
          } catch (error) {
            frame({
              type: 'error',
              error: error instanceof Error ? error.message : 'provider_error',
            });
          } finally {
            if (!response.writableEnded) response.end();
          }
        },
        (error: unknown) => {
          respond(
            response,
            error instanceof BodyTooLargeError ? 413 : 408,
            { error: error instanceof Error ? error.message : 'body_failed' },
            true,
          );
        },
      );
      return;
    }

    if (path === bridgePath('clear') && request.method === 'POST') {
      respond(response, 200, {
        state: publicSnapshot(publish(active, saveConfig({ ...active.state, messages: [] }, { origin: 'web' }))),
      });
      return;
    }

    respond(response, 404, { error: 'not_found' });
  };

  return {
    name: 'project-phone-bridge',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use(middleware);
      // The bridge is part of the dev surface, so surface its own failures.
      server.config.logger.info('  ➜  phone bridge: ' + BRIDGE_PREFIX);
    },
    configurePreviewServer(server) {
      server.middlewares.use(middleware);
    },
    transformIndexHtml() {
      return [
        {
          tag: 'script',
          children: `window.__PHONE_BRIDGE__ = ${JSON.stringify({ prefix: BRIDGE_PREFIX, token: ensure().token })};`,
          injectTo: 'head-prepend' as const,
        },
      ];
    },
    closeBundle() {
      bridge?.stopWatching();
      for (const client of bridge?.clients ?? []) {
        try {
          client.end();
        } catch {
          // The socket is already gone.
        }
      }
      bridge = null;
    },
  };
}

export { BRIDGE_PREFIX, BRIDGE_ROUTES } from './src/bridge-protocol';
