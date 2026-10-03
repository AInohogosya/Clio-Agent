import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AgentClient,
  baseModelFailureKey,
  bindAgentConversation,
  channelLabel,
  createAgentTransport,
  createTranslator,
  DuplexClient,
  identityFailureKey,
  isProviderId,
  PROVIDER_DEFINITIONS,
  publicSettingsEqual,
  sanitizeTerminalText,
  toChatMessages,
  type AgentControlAction,
  type AgentDoor,
  type AgentLinkStatus,
  type AgentTransport,
  type AgentView,
  type ChatMessage,
  type ClientSnapshot,
  type Settings,
} from '@project-phone/core';
import { doorRefusalText, doorSummary, resolveDoor } from './channels.js';
import { saveConfig, watchConfig, type PhoneConfig } from './config.js';
import { controlLabel } from './agent-screen.js';
import type { Tone } from './palette.js';

/**
 * The state half of the terminal interface when it is talking to an agent.
 *
 * The rules are the ones the chat session already keeps, plus one that only
 * matters here:
 *
 *   - the agent's own store is the record of the conversation, so the transcript
 *     is a union of what the agent wrote and the turn this terminal wrote itself
 *     the instant Enter was pressed — never a replacement of one by the other;
 *   - a revision the terminal itself wrote is never read back as somebody else's
 *     change, so saving cannot trigger the reload it just caused;
 *   - appearance and interface settings are saved; the transcript is not, because
 *     the agent keeps it and this terminal has no business keeping a second copy
 *     of a record it is only reading.
 */

const PERSIST_DEBOUNCE_MS = 220;
const DEBUG_LINE_LIMIT = 6;
const DEBUG_LINE_LENGTH = 90;

export interface AgentToast {
  text: string;
  tone: Tone;
  token: number;
}

export interface AgentSession {
  client: DuplexClient;
  agent: AgentClient;
  transport: AgentTransport;
  snapshot: ClientSnapshot;
  view: AgentView;
  link: AgentLinkStatus;
  streaming: boolean;
  config: PhoneConfig;
  debugLines: string[];
  toast: AgentToast | null;
  /**
   * The doors the agent has open, and which one replies are going out of.
   *
   * Three values rather than one, because they are three different questions and
   * the difference between them is the whole feature: a door the deployment has
   * open, a door this terminal is talking through, and a door the transcript holds
   * that the deployment no longer has. A terminal that could only name its own
   * door would be able to send and not to look — and a conversation happening on
   * Telegram is the case that matters.
   */
  doors: AgentDoor[];
  channel: string;
  closedChannels: string[];
  say: (text: string, tone?: Tone) => void;
  dismissToast: () => void;
  interrupt: () => void;
  patchSettings: (patch: Partial<Settings>) => void;
  control: (action: AgentControlAction) => void;
  undoAction: (id: string, tool: string) => void;
  closeIntention: (id: string, title: string) => void;
  /** Shows the agent's base model, or writes the one named by `argument`. */
  setBaseModel: (argument: string, say: (text: string, tone?: Tone) => void) => void;
  /** Reports what the agent is called, or names it the word `argument` holds. */
  setAgentName: (argument: string, say: (text: string, tone?: Tone) => void) => void;
  /**
   * Moves this terminal onto another door, or says why it cannot.
   *
   * Answers rather than throws, because a terminal has nowhere to put a thrown
   * error: the composer clears the line and the reader is left with a message
   * that vanished. Every refusal names the doors that *would* have worked, which
   * is the only part of "unknown channel" anybody can act on.
   */
  setChannel: (name: string) => void;
  /** The door this terminal is on, if the deployment has it open. */
  channelDoor: () => AgentDoor | undefined;
  reconnect: () => void;
}

/** Two histories are the same when the newest entry of each is the same entry. */
export function sameHistory(a: ChatMessage[], b: ChatMessage[]): boolean {
  if (a.length !== b.length) return false;
  return a.at(-1)?.id === b.at(-1)?.id;
}

function appendDebug(lines: string[], line: string): string[] {
  return [...lines.slice(-(DEBUG_LINE_LIMIT - 1)), line];
}

/**
 * The live settings, taken off the client rather than off a prop.
 *
 * They have to come from the client because the client is what a change from the
 * browser is written into: reading a captured `Settings` object would leave the
 * link pointed at the endpoint it started with while the rest of the terminal
 * showed the new one — an agent link to one agent and an address for another.
 */
export function useAgentSession(
  initialConfig: PhoneConfig,
  debug: boolean,
  onSettled: () => void,
): AgentSession {
  const agentRef = useRef<AgentClient | null>(null);
  if (agentRef.current === null) {
    agentRef.current = new AgentClient({
      url: initialConfig.settings.agentUrl,
      personId: initialConfig.settings.agentPerson,
    });
  }
  const agent = agentRef.current;

  const transportRef = useRef<AgentTransport | null>(null);
  if (transportRef.current === null) {
    transportRef.current = createAgentTransport({ resolve: () => agentRef.current });
  }
  const transport = transportRef.current;

  const clientRef = useRef<DuplexClient | null>(null);
  if (clientRef.current === null) {
    clientRef.current = new DuplexClient({
      settings: initialConfig.settings,
      initialMessages: initialConfig.messages,
      transport: transportRef.current,
    });
  }
  const client = clientRef.current;

  const [snapshot, setSnapshot] = useState<ClientSnapshot>(() => client.getSnapshot());
  const [view, setView] = useState<AgentView>(() => agent.getView());
  const [link, setLink] = useState<AgentLinkStatus>(() => agent.getStatus());
  const [streaming, setStreaming] = useState<boolean>(() => agent.isStreaming());
  const [config, setConfig] = useState<PhoneConfig>(initialConfig);
  const [debugLines, setDebugLines] = useState<string[]>([]);
  const [toast, setToast] = useState<AgentToast | null>(null);
  /**
   * The doors, and the one this terminal talks through.
   *
   * `doors` is `[]` until the bridge answers, which is why the selected channel
   * is a separate piece of state: the answer can arrive, change and arrive again
   * (the agent was repointed), and a selection that lived inside the door list
   * would be lost every time the list was replaced.
   */
  const [doors, setDoors] = useState<AgentDoor[]>([]);
  const [channel, setChannelState] = useState<string>('web');

  const configRef = useRef<PhoneConfig>(initialConfig);
  const lastWriteRef = useRef<number>(-1);
  const toastToken = useRef(0);
  const mounted = useRef(true);

  const t = useMemo(() => createTranslator(snapshot.settings.language), [snapshot.settings.language]);

  const say = useCallback((text: string, tone: Tone = 'accent') => {
    toastToken.current += 1;
    setToast({ text, tone, token: toastToken.current });
  }, []);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => {
      if (mounted.current) setToast(null);
    }, 3_200);
    return () => clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    const unsubscribe = client.subscribe((event) => {
      if (!mounted.current) return;
      if (event.type === 'snapshot') {
        setSnapshot(event.snapshot);
        // A finished turn is the newest thing in the pane, so the view follows
        // it — unless the reader has scrolled away, which is the caller's
        // `onSettled` to make, not this one's.
        if (event.snapshot.pending === false) onSettled();
      }
      if (!debug) return;
      if (event.type === 'message') {
        setDebugLines((lines) => appendDebug(lines, `sent    ${event.message.role}`));
      }
      if (event.type === 'status') {
        setDebugLines((lines) => appendDebug(lines, `status  ${event.status}`));
      }
      if (event.type === 'error') {
        const detail = sanitizeTerminalText(event.message, DEBUG_LINE_LENGTH);
        setDebugLines((lines) => appendDebug(lines, `error   ${detail}`));
      }
    });
    client.connect();
    return () => {
      unsubscribe();
      client.disconnect();
    };
  }, [client, debug, onSettled]);

  useEffect(() => agent.subscribe((event) => {
    if (!mounted.current) return;
    if (event.type === 'view') {
      setView(event.view);
      setStreaming(agent.isStreaming());
    } else if (event.type === 'status') {
      setLink(event.status);
      setStreaming(agent.isStreaming());
    } else if (event.type === 'agent' && debug) {
      setDebugLines((lines) => appendDebug(lines, `event   ${event.kind}`));
    }
  }), [agent, debug]);

  useEffect(() => {
    agent.setUrl(snapshot.settings.agentUrl);
    void agent.connect();
  }, [agent, snapshot.settings.agentUrl]);

  // The doors belong to the agent, not to this page, and they change with it: a
  // repointed agent is a different deployment with different channels open. So
  // they are re-read whenever the link is repointed rather than once at mount,
  // which is the difference between a terminal that offers Telegram on one agent
  // and offers it on both.
  //
  // `doors()` never rejects — a bridge that is down, or an older one with no such
  // route, answers the local line alone — so there is no failure path to handle
  // and no way for this to leave the composer with a door it cannot resolve.
  useEffect(() => {
    let cancelled = false;
    void agent.doors().then((found) => {
      if (!cancelled) setDoors(found);
    });
    return () => {
      cancelled = true;
    };
  }, [agent, snapshot.settings.agentUrl]);

  const channelDoor = useCallback(
    () => doors.find((door) => door.id === channel),
    [doors, channel],
  );

  /**
   * Moves this terminal onto another door, or says why it cannot.
   *
   * The refusals are the point. `/channel telegram` on a deployment that has not
   * opened Telegram, on one that has opened it with an empty allowlist, and on one
   * where nobody has told the contact book how to reach the reader are three
   * different problems with three different fixes, and the terminal is the only
   * place a reader is going to be told about any of them. Saying nothing — or
   * switching anyway — turns all three into a question that is deliberated on and
   * never answered.
   */
  const setChannel = useCallback((name: string) => {
    const wanted = name.trim();
    if (!wanted) {
      // A bare `/channel` reports, the way a bare `/name` reports the name and a
      // bare `/person` reports the person. It is the form a reader reaches for
      // when they want to know where their words are going, and answering it
      // with silence would make the reporting form a no-op that looks like a
      // broken command.
      say(t('channelViaLabel', { channel: channelLabel(channel) }), 'accent');
      return;
    }
    const choice = resolveDoor(doors, client.getSnapshot().settings.agentPerson, wanted);
    if (!choice.ok) {
      say(doorRefusalText(choice, client.getSnapshot().settings.language), 'warning');
      return;
    }
    setChannelState(choice.channel);
    agent.setChannel(choice.channel);
    // The address follows the door. A terminal that moved to Telegram and left a
    // Discord channel id in place would be refused by the bridge for a reason that
    // has nothing to do with Telegram, and the reader would have no way to see
    // that the stale value was there.
    agent.setAddress(choice.address);
    // The label rather than the id, for the same reason the one-shot send does:
    // `slack` is a word a person says and `channel` is the word the config uses,
    // and a toast reading `via slack` is indistinguishable from one reading
    // `via web`. The id is in `/channels` for anybody who needs to type it.
    say(t('channelViaLabel', { channel: channelLabel(choice.channel) }), 'accent');
  }, [agent, channel, client, doors, say, t]);

  useEffect(() => bindAgentConversation(client, agent), [agent, client]);

  // Appearance and interface settings are shared with the web page; the
  // transcript is not, because the agent's store is where it lives.
  useEffect(() => {
    const timer = setTimeout(() => {
      const current = configRef.current;
      const next: PhoneConfig = { ...current, settings: snapshot.settings };
      if (publicSettingsEqual(current.settings, next.settings)
        && current.settings.apiKey.trim() === next.settings.apiKey.trim()) return;
      const saved = saveConfig(next, { origin: 'cli' });
      lastWriteRef.current = saved.revision;
      configRef.current = saved;
      setConfig(saved);
    }, PERSIST_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [snapshot.settings]);

  useEffect(() => watchConfig((remote) => {
    if (remote.revision === lastWriteRef.current) return;
    const previous = configRef.current;
    if (remote.revision <= previous.revision && remote.origin === 'cli') return;
    configRef.current = remote;
    setConfig(remote);
    if (!publicSettingsEqual(previous.settings, remote.settings)) {
      client.setSettings(remote.settings);
      say(t('cliReloaded'), 'success');
    }
  }), [client, say, t]);

  useEffect(() => () => {
    agent.dispose();
    client.dispose();
  }, [agent, client]);

  const patchSettings = useCallback((patch: Partial<Settings>) => {
    client.setSettings({ ...client.getSnapshot().settings, ...patch });
  }, [client]);

  const interrupt = useCallback(() => {
    client.interrupt();
    say(t('cliInterruptDone'), 'warning');
  }, [client, say, t]);

  const control = useCallback((action: AgentControlAction) => {
    void transport
      .control(action)
      .then(() => {
        // The label, not the enum: the one-shot `phone agent pause` reports
        // "Queued: Hold everything", and pressing `/hold` in here is the same
        // action, which deserves the same words.
        if (mounted.current) say(t('cliAgentQueued', { action: controlLabel(action, t) }), 'success');
      })
      .catch((error: unknown) => {
        if (mounted.current) {
          say(sanitizeTerminalText(error instanceof Error ? error.message : String(error), 80), 'danger');
        }
      });
  }, [say, t, transport]);

  const undoAction = useCallback((id: string, tool: string) => {
    void transport
      .undoAction(id)
      .then(() => {
        if (mounted.current) say(t('cliAgentUndoQueued', { tool }), 'success');
      })
      .catch(() => {
        if (mounted.current) say(t('cliAgentNoUndo'), 'warning');
      });
  }, [say, t, transport]);

  const closeIntention = useCallback((id: string, title: string) => {
    void transport
      .closeIntention(id)
      .then(() => {
        if (mounted.current) say(t('cliAgentCloseQueued', { title }), 'success');
      })
      .catch(() => {
        if (mounted.current) say(t('agentRefused'), 'warning');
      });
  }, [say, t, transport]);

  const reconnect = useCallback(() => {
    void agent.connect();
  }, [agent]);

  /**
   * `/model` — reports the agent's base model, or writes one.
   *
   * Two words, `<provider> <model>`, because a terminal has no form and the
   * shortest honest spelling of "which vendor, which model" is two nouns. The
   * endpoint is left as the provider's own: a base model with somebody else's
   * address on it is a mistake with a long fuse, and `phone config set baseUrl`
   * is there for the case where it is deliberate.
   *
   * A key is not an argument. The agent holds it, the command line is the wrong
   * place for one, and a model can be pointed at a new id without it.
   */
  const setBaseModel = useCallback((argument: string, say: (text: string, tone?: Tone) => void) => {
    const [provider, ...rest] = argument.trim().split(/\s+/).filter(Boolean);
    if (!provider) {
      void agent.readBaseModel()
        .then((found) => {
          if (!found) {
            say(t('agentBaseModelUnreachable'), 'warning');
            return;
          }
          if (!found.configured) {
            say(t('agentBaseModelCatalogue', { count: view.catalogue }), 'accent');
            return;
          }
          // A base model with no literal key is a normal state when it names the
          // variable to read one from, so the line says which rather than
          // reporting a working model as unreachable.
          const fromEnv = !found.keyPresent && Boolean(found.keyEnv);
          const key = found.keyPresent
            ? found.keyHint
            : fromEnv
              ? t('envKeyFromFile', { variable: found.keyEnv })
              : t('agentBaseModelKeyMissing', { provider: found.provider });
          say(
            `${found.provider} · ${found.model} · ${key}`,
            found.keyPresent || fromEnv ? 'accent' : 'warning',
          );
        })
        .catch(() => say(t('agentBaseModelUnreachable'), 'warning'));
      return;
    }
    const model = rest.join(' ');
    if (!model) {
      say(t('baseModelInvalidModel'), 'warning');
      return;
    }
    const definition = isProviderId(provider) ? PROVIDER_DEFINITIONS[provider] : null;
    if (!definition) {
      say(t('baseModelUnknownProvider'), 'warning');
      return;
    }
    void agent.saveBaseModel({
      provider,
      model,
      baseUrl: definition.defaultBaseUrl,
    })
      .then(() => say(t('agentBaseModelSaved'), 'success'))
      .catch((error: unknown) => say(t(baseModelFailureKey(error)), 'warning'));
  }, [agent, t, view.catalogue]);

  /**
   * `/name` — reports what the agent is called, or names it.
   *
   * One word, because that is the whole setting. The name travels as typed apart from
   * the trim every command does, and the agent refuses one with a line break in it
   * rather than this second-guessing what somebody meant — a name it cannot use is a
   * sentence the person can act on, and a name this quietly shortened is a name they
   * did not choose.
   *
   * "Not named yet" is reported as itself rather than as a failure, because it is not
   * one: the agent has whatever its config calls it, and no person has said
   * otherwise.
   *
   * There is deliberately no way to take a name back from here. A bare `/name`
   * reports, exactly as `/person` and `/link` report their own values, and neither of
   * those has an un-set form either — so the gesture that removes a name is the
   * settings screen's emptied field, which reads as "no name" because that is what an
   * empty field means. A magic word on a command line would be the second spelling of
   * one idea, and the two would drift.
   */
  const setAgentName = useCallback((argument: string, say: (text: string, tone?: Tone) => void) => {
    const wanted = argument.trim();
    if (!wanted) {
      void agent.readIdentity()
        .then((found) => {
          if (!found) {
            say(t('agentNameUnreachable'), 'warning');
            return;
          }
          if (!found.configured) {
            say(t('agentNameUnnamed'), 'warning');
            return;
          }
          say(found.selfName, 'accent');
        })
        .catch(() => say(t('agentNameUnreachable'), 'warning'));
      return;
    }
    void agent.saveIdentity(wanted)
      .then(() => say(`${t('agentNameSaved', { name: wanted })} ${t('agentNameRestart')}`, 'success'))
      .catch((error: unknown) => say(t(identityFailureKey(error)), 'warning'));
  }, [agent, t]);

  const dismissToast = useCallback(() => setToast(null), []);

  // The doors the transcript holds that the deployment no longer has. Derived
  // rather than stored, because it is a fact about two things that both change on
  // their own: the conversation moves and the configuration moves, and a stored
  // copy of their difference is wrong within a minute.
  const closedChannels = useMemo(() => {
    const messages: ChatMessage[] = toChatMessages(view.messages);
    return doorSummary(doors, messages).closed;
  }, [doors, view.messages]);

  return {
    client,
    agent,
    transport,
    snapshot,
    view,
    link,
    streaming,
    config,
    debugLines,
    toast,
    doors,
    channel,
    closedChannels,
    say,
    dismissToast,
    interrupt,
    patchSettings,
    control,
    undoAction,
    closeIntention,
    setBaseModel,
    setAgentName,
    setChannel,
    channelDoor,
    reconnect,
  };
}
