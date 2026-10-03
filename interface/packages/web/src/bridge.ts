import {
  MAX_PROMPT_LENGTH,
  normalizeSharedState,
  providerFor,
  SHARED_STATE_VERSION,
  type ChatMessage,
  type CompletionMessage,
  type DuplexTransport,
  type ModelDiscoveryResult,
  type Settings,
} from '@project-phone/core';
import { bridgePath, BRIDGE_PREFIX, TOKEN_HEADER, type BridgeRoute, type CompletionFrame } from './bridge-protocol';

declare global {
  interface Window {
    __PHONE_BRIDGE__?: { prefix: string; token: string };
  }
}

export type BridgeStatus = 'connected' | 'offline' | 'blocked';

export interface BridgeSnapshot {
  version: number;
  revision: number;
  updatedAt: number;
  origin: 'cli' | 'web' | null;
  transport: 'none' | 'file' | 'bridge';
  settings: Settings;
  credential: { present: boolean; source: 'none' | 'file' | 'environment'; hint: string };
  messages: ChatMessage[];
  configFile: string;
  credentialSource: 'none' | 'file' | 'environment';
}

export interface PushState {
  settings: Settings;
  messages: ChatMessage[];
}

export interface Bridge {
  status: () => BridgeStatus;
  snapshot: () => BridgeSnapshot | null;
  onChange: (listener: (snapshot: BridgeSnapshot) => void) => () => void;
  onStatus: (listener: (status: BridgeStatus) => void) => () => void;
  push: (state: PushState) => Promise<BridgeSnapshot | null>;
  clear: () => Promise<BridgeSnapshot | null>;
  discoverModels: () => Promise<ModelDiscoveryResult | null>;
  /**
   * Lets the shared client reach a provider using the key held in the file, or
   * `null` while the bridge has not been found yet. A surface must be able to
   * ask again, because the answer changes from `null` to a transport a moment
   * after the page starts.
   */
  transport: () => DuplexTransport | null;
  /** Re-resolves the endpoint after `close`. Safe to call when already open. */
  open: () => void;
  close: () => void;
}

function injected(): { prefix: string; token: string } | null {
  if (typeof window === 'undefined') return null;
  const value = window.__PHONE_BRIDGE__;
  return value && value.prefix && value.token ? value : null;
}

/**
 * Resolves the bridge endpoint, and the state to start from.
 *
 * The dev server injects both into the document. `vite preview` serves the
 * built file unchanged, so the page asks the bridge for them instead; the server
 * only answers a loopback peer with a same-machine origin. When neither works
 * the interface runs on its own for the length of the page — there is no second
 * copy of anything, so nothing outlives the tab that made it.
 */
async function resolveEndpoint(): Promise<{ prefix: string; token: string; session: unknown } | null> {
  const embedded = injected();
  if (embedded) return { prefix: embedded.prefix, token: embedded.token, session: null };
  if (typeof window === 'undefined' || typeof fetch !== 'function') return null;
  try {
    const response = await fetch(bridgePath('session'), {
      credentials: 'same-origin',
      headers: { accept: 'application/json' },
    });
    if (!response.ok) return null;
    const payload = await response.json() as { token?: unknown; state?: unknown };
    if (typeof payload?.token !== 'string' || !payload.token) return null;
    return { prefix: BRIDGE_PREFIX, token: payload.token, session: payload.state ?? null };
  } catch {
    return null;
  }
}

/**
 * Talks to the local bridge served alongside the app.
 *
 * The bridge is the page's only route to durable state: the shared
 * configuration file is the one store, and this is how a browser reaches it. A
 * page with no bridge — a static build, or a server started without the plugin —
 * still works for as long as it is open, but every call below resolves to a null
 * result and the state it holds lives only in memory. That is deliberate: a
 * copy kept somewhere the user cannot inspect, sync, or delete is the problem
 * this design exists to avoid.
 *
 * `open` and `close` are a real lifecycle rather than a one-way door. React
 * mounts, unmounts and remounts a component in development; a bridge that could
 * only ever be closed left the page permanently offline after the first remount,
 * which is the kind of fault that looks like a server problem and is not.
 */
export function createBridge(): Bridge {
  const listeners = new Set<(snapshot: BridgeSnapshot) => void>();
  const statusListeners = new Set<(status: BridgeStatus) => void>();
  let status: BridgeStatus = 'offline';
  let current: BridgeSnapshot | null = null;
  let source: EventSource | null = null;
  let endpoint: { prefix: string; token: string } | null = null;
  let closed = false;
  let opening = false;
  let pushing = false;
  let queued: (() => void) | null = null;

  const setStatus = (next: BridgeStatus) => {
    if (status === next) return;
    status = next;
    for (const listener of [...statusListeners]) listener(status);
  };

  const adopt = (snapshot: BridgeSnapshot) => {
    current = snapshot;
    for (const listener of [...listeners]) listener(snapshot);
  };

  // The prefix and the route are the same constants the server matched on.
  const url = (route: BridgeRoute): string => `${endpoint?.prefix ?? ''}${bridgePath(route)}`;

  const request = async (route: BridgeRoute, init: RequestInit = {}): Promise<Response | null> => {
    if (!endpoint || closed) return null;
    try {
      const response = await fetch(url(route), {
        ...init,
        credentials: 'same-origin',
        headers: { 'content-type': 'application/json', [TOKEN_HEADER]: endpoint.token, ...(init.headers ?? {}) },
      });
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) setStatus('blocked');
        return null;
      }
      return response;
    } catch {
      setStatus('offline');
      return null;
    }
  };

  const readSnapshot = async (response: Response): Promise<BridgeSnapshot | null> => {
    try {
      const payload = await response.json() as { state?: unknown };
      if (!payload || typeof payload !== 'object' || !payload.state) return null;
      return normalizeBridgeSnapshot(payload.state);
    } catch {
      return null;
    }
  };

  // `EventSource` cannot attach a request header, so the stream carries the same
  // per-process token in its query string. The bridge compares it in constant
  // time and accepts it on this route only, and the document is served with
  // `Referrer-Policy: no-referrer` so it cannot travel any further.
  const openStream = () => {
    if (!endpoint || closed || typeof EventSource === 'undefined' || source) return;
    source = new EventSource(`${url('events')}?token=${encodeURIComponent(endpoint.token)}`, { withCredentials: true });
    source.onopen = () => setStatus('connected');
    source.onerror = () => setStatus('offline');
    source.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as { type?: string; state?: unknown };
        if (payload.type !== 'state' || !payload.state) return;
        adopt(normalizeBridgeSnapshot(payload.state));
        setStatus('connected');
      } catch {
        // Ignore a malformed frame rather than tearing down the stream.
      }
    };
  };

  const connect = () => {
    if (closed || opening) return;
    opening = true;
    void (async () => {
      try {
        const resolved = await resolveEndpoint();
        if (closed) return;
        if (!resolved) {
          setStatus('offline');
          return;
        }
        endpoint = { prefix: resolved.prefix, token: resolved.token };
        if (resolved.session) {
          adopt(normalizeBridgeSnapshot(resolved.session));
          setStatus('connected');
          openStream();
          return;
        }
        const response = await request('state');
        const snapshot = response ? await readSnapshot(response) : null;
        if (snapshot) {
          adopt(snapshot);
          setStatus('connected');
        }
        openStream();
      } finally {
        opening = false;
      }
    })();
  };

  const push = async (state: PushState): Promise<BridgeSnapshot | null> => {
    if (!endpoint || closed) return null;
    if (pushing) {
      // Coalesce: a burst of edits is one write, carrying the newest state.
      queued = () => {
        queued = null;
        void push(state);
      };
      return current;
    }
    pushing = true;
    try {
      const response = await request('state', { method: 'POST', body: JSON.stringify(state) });
      const snapshot = response ? await readSnapshot(response) : null;
      if (snapshot) {
        adopt(snapshot);
        setStatus('connected');
      }
      return snapshot;
    } finally {
      pushing = false;
      queued?.();
    }
  };

  const discoverModels = async (): Promise<ModelDiscoveryResult | null> => {
    const response = await request('models');
    if (!response) return null;
    try {
      return await response.json() as ModelDiscoveryResult;
    } catch {
      return null;
    }
  };

  /**
   * A completion, delivered as frames so the page can draw the answer as it is
   * written. The read is aborted with the signal, so an interrupt in the browser
   * actually stops the provider request rather than just ignoring the answer.
   */
  const complete = async (
    prompt: string,
    history: CompletionMessage[],
    signal: AbortSignal,
    onDelta: (text: string) => void,
  ): Promise<string> => {
    if (signal.aborted) throw new Error('aborted');
    const response = await request('complete', {
      method: 'POST',
      body: JSON.stringify({ prompt: prompt.slice(0, MAX_PROMPT_LENGTH), history }),
      signal,
    });
    if (!response) throw new Error('bridge_unavailable');
    const reader = response.body?.getReader();
    if (!reader) {
      const payload = await response.json() as { text?: string };
      if (typeof payload.text !== 'string') throw new Error('bridge_unavailable');
      onDelta(payload.text);
      return payload.text;
    }
    const decoder = new TextDecoder();
    let buffer = '';
    let answer = '';
    try {
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        buffer += decoder.decode(result.value, { stream: true });
        let newline = buffer.indexOf('\n');
        while (newline !== -1) {
          const line = buffer.slice(0, newline).trim();
          buffer = buffer.slice(newline + 1);
          if (line) answer = applyFrame(line, answer, onDelta);
          newline = buffer.indexOf('\n');
        }
      }
    } catch (error) {
      if (signal.aborted) throw new Error('aborted');
      throw error instanceof Error ? error : new Error('bridge_unavailable');
    } finally {
      reader.releaseLock();
    }
    if (!answer) throw new Error('empty_reply');
    return answer;
  };

  const transport = (): DuplexTransport | null => {
    if (!endpoint || closed) return null;
    return {
      get authenticated() {
        const snapshot = current;
        const provider = snapshot?.settings.provider ?? 'openai';
        // A keyless provider needs no credential at all, so asking whether one
        // is present would wrongly report a local endpoint as unusable.
        if (!providerFor(provider).requiresApiKey) return true;
        return snapshot?.credential.present === true || snapshot?.credentialSource === 'environment';
      },
      complete,
      async discoverModels() {
        const result = await discoverModels();
        if (result) return result;
        const provider = current?.settings.provider ?? 'openai';
        return { models: providerFor(provider).staticModels, source: 'offline' as const, error: 'bridge_unavailable' };
      },
    };
  };

  connect();

  return {
    status: () => status,
    snapshot: () => current,
    onChange: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    onStatus: (listener) => {
      statusListeners.add(listener);
      return () => statusListeners.delete(listener);
    },
    push,
    clear: async () => {
      const response = await request('clear', { method: 'POST', body: '{}' });
      const snapshot = response ? await readSnapshot(response) : null;
      if (snapshot) adopt(snapshot);
      return snapshot;
    },
    discoverModels,
    transport,
    open: () => {
      if (!closed) {
        connect();
        return;
      }
      closed = false;
      connect();
    },
    close: () => {
      closed = true;
      source?.close();
      source = null;
      endpoint = null;
      current = null;
      listeners.clear();
      statusListeners.clear();
    },
  };
}

function applyFrame(line: string, answer: string, onDelta: (text: string) => void): string {
  let frame: CompletionFrame;
  try {
    frame = JSON.parse(line) as CompletionFrame;
  } catch {
    return answer;
  }
  if (frame.type === 'delta' && typeof frame.text === 'string') {
    onDelta(frame.text);
    return answer + frame.text;
  }
  if (frame.type === 'error') {
    throw new Error(typeof frame.error === 'string' && frame.error ? frame.error : 'provider_error');
  }
  return answer;
}

export function normalizeBridgeSnapshot(value: unknown): BridgeSnapshot {
  const record = (value && typeof value === 'object' && !Array.isArray(value)) ? value as Record<string, unknown> : {};
  const state = normalizeSharedState({ ...record, transport: 'bridge' }, { defaultTransport: 'bridge' });
  return {
    version: SHARED_STATE_VERSION,
    revision: state.revision,
    updatedAt: state.updatedAt,
    origin: state.origin,
    transport: 'bridge',
    settings: state.settings,
    credential: state.credential,
    messages: state.messages,
    configFile: typeof record.configFile === 'string' ? record.configFile : '',
    credentialSource: record.credentialSource === 'file' || record.credentialSource === 'environment'
      ? record.credentialSource
      : 'none',
  };
}
