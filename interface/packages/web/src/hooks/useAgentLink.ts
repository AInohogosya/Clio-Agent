import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AgentClient,
  channelLabel,
  conversationsIn,
  createAgentTransport,
  reconcileDoors,
  toChatTimeline,
  type AgentControlAction,
  type AgentConversation,
  type AgentDoor,
  type AgentLinkStatus,
  type AgentTransport,
  type AgentView,
  type ChatMessage,
  type Settings,
} from '@project-phone/core';

/**
 * Holds the link to the agent for one surface.
 *
 * The link is created once and then *repointed*, never rebuilt: a new client per
 * endpoint would drop an open event stream and start a fresh snapshot read on
 * every keystroke in the address field, which is a way of hammering a local
 * service that should cost nothing to talk to.
 *
 * Nothing here is the transcript. The conversation belongs to the shared client
 * (see `useDuplex`), which is the same client a direct provider uses — so an
 * agent turn and a provider turn obey one set of staleness rules rather than two.
 */

export interface AgentLinkController {
  agent: AgentClient;
  transport: AgentTransport;
  view: AgentView;
  link: AgentLinkStatus;
  /** True when state arrives over a socket; false means this surface polls. */
  streaming: boolean;
  /** The last lifecycle verb that was accepted, for a confirmation line. */
  notice: string | null;
  dismissNotice: () => void;
  control: (action: AgentControlAction) => Promise<void>;
  undoAction: (id: string) => Promise<void>;
  closeIntention: (id: string) => Promise<void>;
  refresh: () => Promise<void>;
  reconnect: () => void;
  /**
   * Everyone the agent is talking to, out of the transcript and the surface's
   * own line, most recently active first.
   *
   * Read rather than asked for, because a conversation is something that has to
   * have happened: the agent's own configuration says which doors it has, not
   * who is standing at them, and a list of people built from the config would be
   * a list of doors with names invented for them.
   */
  conversations: AgentConversation[];
  /**
   * The conversation this surface is on.
   *
   * The web line before anything else has been chosen — it is the conversation
   * this interface conducts, and it is the one on screen when the page opens.
   * Never `null` once the link has a transcript, and not nullable for that
   * reason: a pane with no conversation chosen has nothing to draw and nowhere
   * for a reply to go, so the reader's own is chosen rather than leaving them to
   * work out that they must pick something first.
   */
  conversation: AgentConversation | null;
  /**
   * Moves this surface to another conversation.
   *
   * A conversation rather than a door, because a door alone cannot say where the
   * reply goes: two people writing in on Telegram are one door and two
   * conversations, and choosing the door would answer both of them in one voice.
   */
  setConversation: (conversation: AgentConversation | null) => void;
  /**
   * Doors the agent has spoken on and does not have open.
   *
   * Kept beside the conversations rather than folded into them so the surface can
   * still show the history that came in through one while saying out loud that
   * it cannot be written to — hiding it would lose the conversation, and offering
   * it would type into a door with nothing behind it.
   */
  closedChannels: string[];
  /**
   * The doors the agent has open, as its configuration reported them.
   *
   * The bar of places a conversation can happen is built from these rather than
   * from the transcript: a door with a bot token on file is a place a
   * conversation can *start*, and a list derived from what has been said would
   * offer only the doors that already have traffic in them.
   */
  doors: AgentDoor[];
  /**
   * Asks the agent which doors it has, again.
   *
   * For a surface that has just changed the answer — a settings screen that
   * opened a door. Without it the list above is a list read on the way in, so a
   * person who opens Telegram and closes the settings is left looking at a
   * conversation the agent can no longer answer, and a page reload is the only
   * thing that makes the door they just opened appear.
   */
  refreshDoors: () => Promise<void>;
}

/**
 * `settings` is read, not captured: the settings live in the shared client, and
 * the shared client is created after this hook. A snapshot taken here would be
 * the default one, and a surface pointed at a default endpoint would look broken
 * while the rest of the page showed a perfectly good address.
 *
 * The transcript is passed rather than read, because it is this hook's input
 * rather than something it decides: it is the shared client's own list, and a
 * getter would be a new function on every render, which would make every
 * conversation below it a new object and repoint the agent at a door it is
 * already on.
 */
export function useAgentLink(
  settings: () => Settings,
  transcript: readonly ChatMessage[],
): AgentLinkController {
  const read = () => settings();
  const agentRef = useRef<AgentClient | null>(null);
  if (agentRef.current === null) {
    const initial = read();
    agentRef.current = new AgentClient({ url: initial.agentUrl, personId: initial.agentPerson });
  }
  const agent = agentRef.current;

  const transportRef = useRef<AgentTransport | null>(null);
  if (transportRef.current === null) {
    transportRef.current = createAgentTransport({ resolve: () => agentRef.current });
  }
  const transport = transportRef.current;

  const [view, setView] = useState<AgentView>(() => agent.getView());
  const [link, setLink] = useState<AgentLinkStatus>(() => agent.getStatus());
  const [streaming, setStreaming] = useState<boolean>(() => agent.isStreaming());
  const [notice, setNotice] = useState<string | null>(null);
  const [generation, setGeneration] = useState(0);
  const [selected, setSelected] = useState<AgentConversation | null>(null);
  const mounted = useRef(true);

  const agentPerson = read().agentPerson;

  /**
   * The doors on offer, from the agent's own configuration.
   *
   * Asked for rather than derived from the transcript, which is what the list of
   * doors used to be. Deriving it is defensible right up until the moment it
   * stops being the same feature: a Telegram conversation that has not started
   * yet has no Telegram in the transcript, so the one door a browser could
   * *open* is the one door it cannot offer. The agent's own config is the other
   * half of the answer, and it also carries whether the door will admit anybody
   * at all — which the transcript cannot know, and which is the difference
   * between a channel and a silence.
   */
  const [doors, setDoors] = useState<AgentDoor[]>([]);

  const { closed } = useMemo(
    () => reconcileDoors(doors, toChatTimeline(view.messages).channels),
    [doors, view.messages],
  );

  // The conversation being read, decided here rather than in the pane so that the
  // door the agent will answer on and the conversation being drawn cannot be two
  // different answers to one question.
  const conversations = useMemo(() => {
    const found = [...conversationsIn(transcript, agentPerson)];
    // The terminal's line is a conversation before anything has been typed in
    // it, the same way the web line is: the bar offers it, and a pane that
    // offered a door it could not open would be a door that reads as broken.
    const terminal = `cli:${agentPerson}`;
    if (!found.some((entry) => entry.id === terminal)) {
      found.push({
        id: terminal, channel: 'cli', person: agentPerson,
        label: channelLabel('cli'), messages: 0, lastAt: 0,
      });
    }
    // A door with a bot token on file is a place a conversation can start, so it
    // is offered before anybody has written on it — the way the web line is.
    // One entry per door, which disappears the moment a real conversation does,
    // because a list that keeps an empty door beside the people on it is a list
    // that reads twice.
    for (const door of doors) {
      if (door.id === 'web' || door.id === 'cli') continue;
      if (!found.some((entry) => entry.channel === door.id)) {
        found.push({
          id: `${door.id}:`, channel: door.id, person: '',
          label: channelLabel(door.id), messages: 0, lastAt: 0,
        });
      }
    }
    return found;
  }, [transcript, agentPerson, doors]);
  // A conversation that has fallen out of the transcript cannot be read, so the
  // reader lands on the web line — the conversation this interface conducts, and
  // the one the page opens on — rather than on the newest one that can.
  const conversation = selected && conversations.some((entry) => entry.id === selected.id)
    ? conversations.find((entry) => entry.id === selected.id) ?? null
    : conversations.find((entry) => entry.id === `web:${agentPerson}`) ?? null;

  /**
   * Points the agent at this conversation's door, and at the person on it.
   *
   * Repointed rather than read at construction, because the client is rebuilt
   * whenever the person or the endpoint changes, and a rebuild is a client that
   * would otherwise forget which conversation it was asked to talk about.
   *
   * The address goes over only when it is somebody else's. When the
   * conversation is with the reader themselves the deployment's own contact book
   * holds the address, and a value carried here would be a second copy of it to
   * keep in step with the first — and the one case where it could be wrong is a
   * conversation with no address at all, where sending it as one would put a
   * person's name where a channel wants a chat id.
   */
  useEffect(() => {
    const client = agentRef.current;
    if (!client) return;
    client.setChannel(conversation?.channel ?? 'web');
    client.setAddress(conversation && conversation.person && conversation.person !== agentPerson
      ? conversation.person
      : null);
  }, [conversation, agentPerson, generation]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refreshDoors = useCallback(async () => {
    const found = await agentRef.current?.doors();
    if (found) setDoors(found);
  }, []);

  useEffect(() => agent.subscribe((event) => {
    if (!mounted.current) return;
    if (event.type === 'view') {
      setView(event.view);
      setStreaming(agent.isStreaming());
    } else if (event.type === 'status') {
      setLink(event.status);
      setStreaming(agent.isStreaming());
    }
  }), [agent]);

  const agentUrl = read().agentUrl;

  // Read on every endpoint change and every client rebuild, because both are the
  // same event as "this is a different agent": a deployment's doors belong to the
  // agent, not to this page, and a surface showing the previous agent's doors
  // would offer a door that is not there.
  useEffect(() => {
    let live = true;
    const client = agentRef.current;
    void client?.doors().then((found) => {
      if (live) setDoors(found);
    });
    return () => {
      live = false;
    };
  }, [generation, agentUrl]);

  // The endpoint is a setting, so changing it is changing which agent this is.
  useEffect(() => {
    agent.setUrl(agentUrl);
    void agent.connect();
  }, [agent, agentUrl]);

  // A new person is a new conversation as far as the agent is concerned, so the
  // link is rebuilt rather than renamed in place.
  useEffect(() => {
    agent.dispose();
    agentRef.current = new AgentClient({ url: agentUrl, personId: agentPerson });
    transportRef.current = createAgentTransport({ resolve: () => agentRef.current });
    setGeneration((value) => value + 1);
  }, [agentPerson, agentUrl]);

  const control = useCallback(async (action: AgentControlAction) => {
    await transport.control(action);
    if (mounted.current) setNotice(action);
  }, [transport]);

  const undoAction = useCallback(async (id: string) => {
    await transport.undoAction(id);
    if (mounted.current) setNotice(`undo:${id}`);
  }, [transport]);

  const closeIntention = useCallback(async (id: string) => {
    await transport.closeIntention(id);
    if (mounted.current) setNotice(`close:${id}`);
  }, [transport]);

  const refresh = useCallback(async () => {
    await agent.refresh();
  }, [agent]);

  const reconnect = useCallback(() => {
    void agent.connect();
  }, [agent]);

  const dismissNotice = useCallback(() => setNotice(null), []);

  const current = generation > 0 ? agentRef.current! : agent;
  const currentTransport = generation > 0 ? transportRef.current! : transport;

  useEffect(() => () => {
    current.dispose();
  }, [current]);

  return useMemo(() => ({
    agent: current,
    transport: currentTransport,
    view,
    link,
    streaming,
    notice,
    dismissNotice,
    control,
    undoAction,
    closeIntention,
    refresh,
    reconnect,
    conversations,
    conversation,
    doors,
    closedChannels: closed,
    setConversation: setSelected,
    refreshDoors,
  }), [
    current, currentTransport, view, link, streaming, notice,
    dismissNotice, control, undoAction, closeIntention, refresh, reconnect,
    conversations, conversation, doors, closed, refreshDoors,
  ]);
}
