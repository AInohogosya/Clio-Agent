import {
  AgentClient,
  createAgentTransport,
  createSettings,
  DuplexClient,
  type AgentTransport,
  type SharedState,
} from '@project-phone/core';

/**
 * The terminal's view of the shared configuration file. Every rule about
 * ownership, permissions, atomic writes and revisions lives in
 * `@project-phone/core/store`, which the local web bridge also uses, so the
 * two interfaces can never disagree about the file format.
 */
export type PhoneConfig = SharedState;

export type { ChatMessage, Settings, SharedState } from '@project-phone/core';
export {
  configPath,
  constantTimeEquals,
  emptyConfig,
  hasEnvironmentApiKey,
  readConfig,
  saveConfig,
  watchConfig,
  writeConfig,
} from '@project-phone/core/store';
export type { SaveConfigOptions } from '@project-phone/core/store';

export interface PhoneSession {
  client: DuplexClient;
  /** The agent link, or `null` when this session is talking to a provider. */
  agent: AgentClient | null;
}

/**
 * Where this session's messages go.
 *
 * The door and the address both belong to the session rather than to the settings
 * file, for one reason: they are chosen per command, and a terminal that had
 * moved itself to Telegram to ask one question would leave the next one going
 * there too. `phone send --channel telegram` is one question in one place.
 */
export interface PhoneDestination {
  channel?: string;
  /** `null` and absent mean the same thing: ask the deployment's contact book. */
  address?: string | null;
}

/**
 * A one-shot session: one client, and the link behind it if there is one.
 *
 * The transport follows the settings, so `phone send` talks to the agent when the
 * interface says agent and to the provider when it says direct — a separate
 * command for each would mean two ways to do one thing and one of them quietly
 * doing the other.
 *
 * The destination is carried on the agent link rather than threaded through the
 * turn, because the turn goes out through the shared transport and the transport
 * is the whole session. A surface that wanted a per-message door instead of a
 * per-session one would have to change every caller; a per-session door is the
 * thing both callers actually mean.
 */
export function createPhoneClient(
  config: PhoneConfig,
  destination: PhoneDestination = {},
): PhoneSession {
  const settings = createSettings(config.settings);
  if (settings.interface !== 'agent') {
    return {
      client: new DuplexClient({ settings, initialMessages: config.messages }),
      agent: null,
    };
  }
  const agent = new AgentClient({
    url: settings.agentUrl,
    personId: settings.agentPerson,
    ...(destination.channel ? { sendChannel: destination.channel } : {}),
    ...(destination.address ? { sendAddress: destination.address } : {}),
  });
  const transport: AgentTransport = createAgentTransport({ resolve: () => agent });
  return {
    client: new DuplexClient({ settings, initialMessages: config.messages, transport }),
    agent,
  };
}
