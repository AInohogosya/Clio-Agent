/**
 * The contract between the local bridge server (`bridge-plugin.ts`) and the page
 * that talks to it (`src/bridge.ts`). Both ends import these names, so a route
 * cannot be renamed on one side and left behind on the other — which would show
 * up as a page that silently falls back to browser-only storage.
 */
export const BRIDGE_PREFIX = '/__phone';

export const BRIDGE_ROUTES = {
  /** Asks for the token and the current state; the only unauthenticated route. */
  session: 'session',
  state: 'state',
  events: 'events',
  models: 'models',
  /** Streams a completion back as newline-delimited JSON frames. */
  complete: 'complete',
  clear: 'clear',
} as const;

export type BridgeRoute = keyof typeof BRIDGE_ROUTES;

/** One frame of a streamed completion. */
export type CompletionFrame =
  | { type: 'delta'; text: string }
  | { type: 'done'; text: string }
  | { type: 'error'; error: string };

export function bridgePath(route: BridgeRoute): string {
  return `${BRIDGE_PREFIX}/${BRIDGE_ROUTES[route]}`;
}

/** The token travels in a header on every request except the event stream. */
export const TOKEN_HEADER = 'x-phone-token';
export const TOKEN_QUERY_KEY = 'token';
