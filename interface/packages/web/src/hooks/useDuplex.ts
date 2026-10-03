import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AgentRequestError,
  bindAgentConversation,
  createSettings,
  DuplexClient,
  publicSettingsEqual,
  type AgentBaseModel,
  type AgentBaseModelInput,
  type AgentChannelSetup,
  type AgentDoorWrite,
  type AgentIdentity,
  type ChatMessage,
  type ClientSnapshot,
  type DuplexTransport,
  type EnvApiKey,
  type ModelDiscoveryResult,
  type Settings,
} from '@project-phone/core';
import { createBridge, type Bridge, type BridgeSnapshot, type BridgeStatus } from '../bridge';
import { useAgentLink, type AgentLinkController } from './useAgentLink';

/** `local` is memory for this tab, not a store: nothing of it is written down. */
export type Surface = 'bridge' | 'local';

export interface DuplexController extends AgentLinkController {
  client: DuplexClient;
  snapshot: ClientSnapshot;
  surface: Surface;
  bridgeStatus: BridgeStatus;
  configFile: string;
  credential: BridgeSnapshot['credential'];
  credentialSource: BridgeSnapshot['credentialSource'];
  lastSyncedAt: number | null;
  sendMessage: (text: string) => Promise<void>;
  interrupt: () => void;
  clearConversation: () => void;
  updateSettings: (patch: Partial<Settings>) => void;
  /**
   * A catalogue, fetched with the credential the draft is holding.
   *
   * `candidate` lets the settings screen probe a draft the client has not
   * adopted, and `useEnv` says to spend the provider's environment variable
   * rather than a typed key.
   */
  discoverModels: (
    signal?: AbortSignal,
    candidate?: Settings,
    options?: { useEnv?: boolean },
  ) => Promise<ModelDiscoveryResult>;
  /**
   * Which providers have a key in the agent's environment, as names and hints.
   *
   * A page cannot read a process's environment, so this is a question asked of
   * the only party that can answer it. An empty list is the ordinary answer when
   * nothing is exported, and it is never a failure: the form simply offers no
   * button.
   */
  readEnvApiKeys: () => Promise<EnvApiKey[]>;
  /**
   * The agent's own base model, read and written over the agent link.
   *
   * Deliberately not part of `Settings`: a base model belongs to the agent's
   * configuration, is written into its own home, and is read back from the agent
   * rather than from this tab's memory. Putting it in the shared settings would
   * mean the terminal and this page both believed they owned it, and neither
   * would be right.
   */
  readBaseModel: () => Promise<AgentBaseModel | null>;
  saveBaseModel: (input: AgentBaseModelInput) => Promise<AgentBaseModel>;
  /**
   * The name the agent answers to, read and written over the agent link.
   *
   * On the same footing as the base model, and for a stronger reason: a name kept in
   * this tab's settings would be a name the browser knows and the terminal does not,
   * and the agent would be answering to whichever surface happened to be open. It
   * belongs in the agent's own home, next to what it calls itself, and nowhere else.
   *
   * `null` from `readIdentity` is "could not be asked", which is not the same as an
   * answer saying nobody has named it — the second is `{ configured: false }`, and a
   * form that could not tell them apart would offer to write a name into a machine
   * it had not reached.
   */
  readIdentity: () => Promise<AgentIdentity | null>;
  saveIdentity: (selfName: string) => Promise<AgentIdentity>;
  clearIdentity: () => Promise<AgentIdentity>;
  /**
   * The doors the agent can hear on, and how each one is set up.
   *
   * On the same footing as the base model and the name, and for the same reason
   * in each case: these belong to the agent's own configuration and to nobody
   * else's. A credential kept in this tab's settings would be a credential the
   * browser knew and the terminal did not — and a bot token that a tab's storage
   * holds is a bot token any script on the page can read.
   *
   * `null` is "could not be asked", which is not the same as a machine with no
   * doors: the second is a setup with an empty list, and a form that could not
   * tell them apart would offer to write a token into an agent it never reached.
   */
  readDoors: () => Promise<AgentChannelSetup | null>;
  saveDoor: (write: AgentDoorWrite) => Promise<AgentChannelSetup>;
}

function sameEntries<T extends { id: string }>(left: readonly T[], right: readonly T[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((item, index) => item.id === right[index]?.id);
}

const PUSH_DEBOUNCE_MS = 300;

/**
 * The answers that mean "nobody was asked", as opposed to "the provider said no".
 *
 * Kept apart on purpose: a service that is down, and a service running a build
 * from before `/api/models` existed, are both facts about this machine and are
 * worth answering for ourselves. A 401, an empty catalogue, a provider that could
 * not be reached or a refused endpoint all came from the party that holds the
 * key, and re-asking the provider from the page would not improve them — it would
 * replace a real answer with a guess, which is how a reachable provider ends up
 * looking like a broken form.
 */
const NOT_ASKED = new Set(['agent_unreachable', 'http_404', 'http_405', 'http_501']);

/**
 * Drives the shared client from the one store that outlives the page.
 *
 * There are two ways to be reached and one client. In `direct` mode the client
 * talks to a model provider, through the local bridge so a key on disk can serve
 * a page that never holds it. In `agent` mode it talks to the agent instead, and
 * the local bridge is not involved at all — there is no credential to hold and
 * no catalogue to fetch. The client, its transcript and its turn rules are the
 * same in both, which is the point: an agent turn and a provider turn are the
 * same kind of event with different answers.
 *
 * The shared configuration file remains the single source of truth for both
 * surfaces, so the terminal and this page share settings and history.
 */
export function useDuplex(): DuplexController {
  const bridgeRef = useRef<Bridge | null>(null);
  if (bridgeRef.current === null) bridgeRef.current = createBridge();
  const bridge = bridgeRef.current;

  /**
   * Where the next question goes, kept here and read by the client at the moment
   * it is asked.
   *
   * A ref rather than a value passed in, because the client is built before the
   * agent link that chooses the destination: the two are made in the same render
   * and each needs the other, and only one of those directions is a render away.
   */
  const destinationRef = useRef<{ channel?: string; person?: string }>({});

  const clientRef = useRef<DuplexClient | null>(null);
  // The bridge is asked for its transport here *and again* when it connects.
  // Reading it once, in the first render, always found nothing: the endpoint is
  // resolved in a later microtask, so the page permanently fell back to
  // answering for itself while the panel insisted the key on file was in use.
  if (clientRef.current === null) {
    clientRef.current = new DuplexClient({
      settings: createSettings(),
      transport: bridge.transport() ?? undefined,
      destination: () => destinationRef.current,
    });
  }
  const client = clientRef.current;

  const [snapshot, setSnapshot] = useState<ClientSnapshot>(() => client.getSnapshot());

  // The agent link reads the settings off the client rather than holding a copy,
  // so the two cannot disagree about which endpoint or which name is current.
  // The transcript is this page's own state and is handed over as it is.
  const agentLink = useAgentLink(
    () => clientRef.current?.getSnapshot().settings ?? createSettings(),
    snapshot.messages,
  );

  const [remote, setRemote] = useState<BridgeSnapshot | null>(null);
  const [bridgeStatus, setBridgeStatus] = useState<BridgeStatus>(() => bridge.status());
  const [lastSyncedAt, setLastSyncedAt] = useState<number | null>(null);
  const pushingRef = useRef(false);

  const agentMode = snapshot.settings.interface === 'agent';

  // The question the surface writes for itself goes into the conversation it is
  // being sent into, which is the one the reader is looking at. Only in agent
  // mode: a direct provider turn has no door, and giving it one would write a
  // label into a transcript that has never had any.
  useEffect(() => {
    destinationRef.current = agentMode && agentLink.conversation
      ? {
        channel: agentLink.conversation.channel,
        person: agentLink.conversation.person,
      }
      : {};
  }, [agentMode, agentLink.conversation]);

  useEffect(() => {
    const unsubscribe = client.subscribe((event) => {
      if (event.type === 'snapshot') setSnapshot(event.snapshot);
    });
    client.connect();
    return () => {
      unsubscribe();
      client.disconnect();
    };
  }, [client]);

  useEffect(() => {
    bridge.open();
    const offStatus = bridge.onStatus(setBridgeStatus);
    const offChange = bridge.onChange((next) => {
      setRemote(next);
      setLastSyncedAt(Date.now());
    });
    setBridgeStatus(bridge.status());
    const initial = bridge.snapshot();
    if (initial) {
      setRemote(initial);
      setLastSyncedAt(Date.now());
    }
    return () => {
      offStatus();
      offChange();
    };
  }, [bridge]);

  useEffect(() => () => bridge.close(), [bridge]);

  // The transport arrives with the connection, not with the render — and which
  // transport is meant is a setting, not a fixed fact about the page.
  useEffect(() => {
    const attach = () => {
      if (agentMode) {
        client.setTransport(agentLink.transport);
        return;
      }
      const transport: DuplexTransport | null = bridge.transport();
      if (transport) client.setTransport(transport);
    };
    attach();
    return bridge.onStatus(attach);
  }, [agentLink.transport, agentMode, bridge, client]);

  // In agent mode the transcript is the agent's store, unioned with the turn the
  // surface wrote itself so a question appears before the agent has seen it.
  useEffect(() => {
    if (!agentMode) return;
    return bindAgentConversation(client, agentLink.agent);
  }, [agentLink.agent, agentMode, client]);

  // Adopt settings and transcript written by the terminal.
  const lastRemoteRevision = useRef(-1);
  useEffect(() => {
    if (!remote || remote.revision === lastRemoteRevision.current) return;
    lastRemoteRevision.current = remote.revision;
    if (!publicSettingsEqual(snapshot.settings, remote.settings)) {
      const adopted = { ...remote.settings, apiKey: snapshot.settings.apiKey };
      // The browser owns the interface choice while it is the one being used; a
      // terminal that never heard of the agent must not switch this page out
      // from under the reader by writing a default into the shared file.
      client.setSettings(createSettings({
        ...adopted,
        interface: snapshot.settings.interface,
        agentUrl: snapshot.settings.agentUrl,
        agentPerson: snapshot.settings.agentPerson,
      }));
    }
    if (!agentMode && !sameEntries(snapshot.messages, remote.messages)) {
      client.adoptHistory(remote.messages);
    }
  }, [agentMode, client, remote, snapshot.messages, snapshot.settings]);

  // Publish local changes back to the shared file.
  useEffect(() => {
    // Unpaired, there is nowhere to write. The state stays in memory and the
    // pairing panel says so; a browser-owned copy of the transcript would be
    // invisible to `phone`, survive a second browser unreconciled, and outlive
    // any way the user has of removing it.
    if (bridge.status() !== 'connected') return;
    if (pushingRef.current) return;
    const timer = setTimeout(() => {
      pushingRef.current = true;
      void bridge
        .push({ settings: snapshot.settings, messages: snapshot.messages })
        .then((result) => {
          if (result) {
            lastRemoteRevision.current = result.revision;
            setLastSyncedAt(Date.now());
          }
        })
        .finally(() => {
          pushingRef.current = false;
        });
    }, PUSH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [bridge, snapshot.messages, snapshot.settings]);

  const sendMessage = useCallback(async (text: string) => {
    await client.sendMessage(text);
  }, [client]);

  const interrupt = useCallback(() => client.interrupt(), [client]);
  const clearConversation = useCallback(() => client.clearConversation(), [client]);
  const updateSettings = useCallback((patch: Partial<Settings>) => {
    client.updateSettings(patch);
  }, [client]);

  const discoverModels = useCallback(async (
    signal?: AbortSignal,
    candidate?: Settings,
    options?: { useEnv?: boolean },
  ) => {
    // Somebody has to hold the credential to list a keyed provider's catalogue,
    // and on this machine that is the process which serves the agent's interface
    // — the same one the base-model form reads and writes over. Both interfaces
    // ask it, because the alternative is the page fetching the provider itself,
    // which two things forbid: a key typed into a form would have to cross into
    // the page to be sent, and the document policy this page is served under
    // allows a request to its own origin and no further, so such a request is
    // refused before it leaves. That refusal is silent, and a silent refusal is
    // what "Discover models does nothing" looked like.
    const draft = candidate ?? snapshot.settings;
    const asked = await agentLink.agent.discoverBaseModels({
      provider: draft.provider,
      baseUrl: draft.baseUrl,
      apiKey: draft.apiKey,
      useEnv: options?.useEnv,
    });
    // A party that could not be reached, or a build of it from before the route
    // existed, is not an answer about the provider — it is an answer about where
    // to ask. Then this page answers for itself, which is all it ever did, and
    // says so through the same reason field.
    if (!NOT_ASKED.has(asked.error ?? '')) return asked;
    return client.discoverModels(signal, candidate);
  }, [agentLink.agent, client, snapshot.settings]);

  const readEnvApiKeys = useCallback(
    () => agentLink.agent.readEnvApiKeys(),
    [agentLink.agent],
  );

  const surface: Surface = bridge.status() === 'connected' ? 'bridge' : 'local';

  // The agent's base model, over the same link everything else about the agent
  // travels on. The link is the authority rather than a convenience here: a base
  // model written anywhere but the agent's own configuration is one the gateway
  // will not read, so a page that kept its own copy would be showing a setting
  // that does nothing.
  const agent = agentLink.agent;

  const readBaseModel = useCallback(
    () => (agent ? agent.readBaseModel() : Promise.resolve(null)),
    [agent],
  );

  const saveBaseModel = useCallback(
    async (input: AgentBaseModelInput): Promise<AgentBaseModel> => {
      if (!agent) throw new AgentRequestError('agent_offline', 'not_configured');
      return agent.saveBaseModel(input);
    },
    [agent],
  );

  const readIdentity = useCallback(
    () => (agent ? agent.readIdentity() : Promise.resolve(null)),
    [agent],
  );

  const saveIdentity = useCallback(
    async (selfName: string): Promise<AgentIdentity> => {
      if (!agent) throw new AgentRequestError('agent_offline', 'not_configured');
      return agent.saveIdentity(selfName);
    },
    [agent],
  );

  const clearIdentity = useCallback(
    async (): Promise<AgentIdentity> => {
      if (!agent) throw new AgentRequestError('agent_offline', 'not_configured');
      return agent.clearIdentity();
    },
    [agent],
  );

  // The doors, over the same link. A credential is never held here: the page
  // sends it once and is told afterwards whether one is on file, which is the
  // only arrangement in which a form can offer a field for a secret it does not
  // keep.
  const readDoors = useCallback(
    () => (agent ? agent.channelSetup() : Promise.resolve(null)),
    [agent],
  );

  const saveDoor = useCallback(
    async (write: AgentDoorWrite): Promise<AgentChannelSetup> => {
      if (!agent) throw new AgentRequestError('agent_offline', 'not_configured');
      const written = await agent.saveDoor(write);
      // The filter over the transcript is a list read on the way in, so it is
      // asked for again here: a person who has just opened Telegram and closed
      // the settings is otherwise left looking at a page with no Telegram in it,
      // and a reload as the only way to make the door they opened appear.
      await agentLink.refreshDoors();
      return written;
    },
    [agent, agentLink],
  );

  return useMemo(() => ({
    ...agentLink,
    client,
    snapshot,
    surface,
    bridgeStatus,
    configFile: remote?.configFile ?? '',
    credential: remote?.credential ?? { present: false, source: 'none', hint: '' },
    credentialSource: remote?.credentialSource ?? 'none',
    lastSyncedAt,
    sendMessage,
    interrupt,
    clearConversation,
    updateSettings,
    discoverModels,
    readEnvApiKeys,
    readBaseModel,
    saveBaseModel,
    readIdentity,
    saveIdentity,
    clearIdentity,
    readDoors,
    saveDoor,
  }), [
    agentLink, client, snapshot, surface, bridgeStatus, remote, lastSyncedAt,
    sendMessage, interrupt, clearConversation, updateSettings, discoverModels,
    readEnvApiKeys, readBaseModel, saveBaseModel, readIdentity, saveIdentity, clearIdentity,
    readDoors, saveDoor,
  ]);
}

export type { ChatMessage };
