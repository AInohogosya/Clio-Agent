import { AgentRequestError } from './types.js';

/**
 * How a surface reaches the agent.
 *
 * Two shapes exist because two runtimes do. A browser has `WebSocket`; a Node
 * process on a supported version has it too, but a terminal must not depend on a
 * global that only exists in some of the versions the kit claims to support, so
 * the socket is injectable and polling is the floor.
 *
 * Polling is not a degraded mode bolted on afterwards — it is the reason the
 * agent interface works at all over a socket the agent has not opened. Commands
 * still go over HTTP; only the observation of new state falls back.
 */

export interface AgentChannelHandlers {
  onOpen(): void;
  onFrame(frame: unknown): void;
  onClose(): void;
}

export interface AgentChannel {
  readonly kind: 'socket' | 'poll';
  /** Absolute `ws:` URL of the agent's event stream. */
  readonly url: string;
  open(handlers: AgentChannelHandlers): void;
  send(payload: string): boolean;
  close(): void;
}

/** The subset of the WebSocket API this kit uses, so a fake socket can stand in. */
export interface MinimalSocket {
  send(data: string): void;
  close(): void;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data?: unknown }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: ((event: unknown) => void) | null;
}

export type SocketFactory = (url: string) => MinimalSocket;

const defaultSocketFactory: SocketFactory = (url) => {
  const ctor = (globalThis as { WebSocket?: new (url: string) => MinimalSocket }).WebSocket;
  if (typeof ctor !== 'function') {
    throw new AgentRequestError('agent_unreachable', 'no_websocket');
  }
  return new ctor(url);
};

/** True when this runtime can hold a socket open at all. */
export function hasSocketSupport(factory: SocketFactory = defaultSocketFactory): boolean {
  try {
    const ctor = (globalThis as { WebSocket?: unknown }).WebSocket;
    if (typeof ctor === 'function') return true;
    // A caller-supplied factory is a promise of support; there is nothing to probe.
    return factory !== defaultSocketFactory;
  } catch {
    return false;
  }
}

class SocketChannel implements AgentChannel {
  readonly kind = 'socket';
  private socket: MinimalSocket | null = null;
  private closed = false;

  constructor(
    readonly url: string,
    private readonly factory: SocketFactory,
  ) {}

  open(handlers: AgentChannelHandlers): void {
    if (this.closed) return;
    let socket: MinimalSocket;
    try {
      socket = this.factory(this.url);
    } catch {
      handlers.onClose();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      if (!this.closed) handlers.onOpen();
    };
    socket.onmessage = (event) => {
      if (this.closed) return;
      const data = event?.data;
      // A binary frame is not this protocol. Ignoring it is better than handing
      // a reader a byte array it has to recognise and reject.
      if (typeof data !== 'string') return;
      try {
        handlers.onFrame(JSON.parse(data));
      } catch {
        /* a frame that is not JSON is not an event */
      }
    };
    socket.onerror = () => {
      if (this.closed) return;
      handlers.onClose();
    };
    socket.onclose = () => {
      if (this.closed) return;
      handlers.onClose();
    };
  }

  send(payload: string): boolean {
    const socket = this.socket;
    if (!socket || this.closed) return false;
    try {
      socket.send(payload);
      return true;
    } catch {
      return false;
    }
  }

  close(): void {
    this.closed = true;
    const socket = this.socket;
    this.socket = null;
    if (!socket) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    try {
      socket.close();
    } catch {
      /* already gone */
    }
  }
}

export function createSocketChannel(url: string, factory: SocketFactory = defaultSocketFactory): AgentChannel {
  return new SocketChannel(url, factory);
}

export interface AgentEndpoints {
  /** Base `http(s)` origin of the agent's interface service. */
  http: string;
  /** Absolute `ws:`/`wss:` URL of its event stream. */
  events: string;
}

/**
 * Turns a configured base into the two URLs the client needs.
 *
 * The base is already validated to be a loopback http(s) origin, so this only
 * has to pick the event-stream scheme. Rejecting rather than guessing is
 * deliberate: a wrong base must surface as one clear error rather than as a
 * client that quietly polls a service that was never there.
 */
export function resolveAgentEndpoints(base: string): AgentEndpoints {
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    throw new AgentRequestError('agent_invalid_endpoint', base.slice(0, 64));
  }
  const http = `${url.protocol}//${url.host}`;
  const events = `${url.protocol === 'https:' ? 'wss:' : 'ws:'}//${url.host}/events`;
  return { http, events };
}
