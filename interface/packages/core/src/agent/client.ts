import {
  MAX_ADDRESS_LENGTH,
  MAX_CHANNEL_LENGTH,
  MAX_PROMPT_LENGTH,
  sanitizeStoredText,
} from '../security.js';
import type { ModelDiscoveryResult } from '../types.js';
import {
  createSocketChannel,
  hasSocketSupport,
  resolveAgentEndpoints,
  type AgentChannel,
  type AgentChannelHandlers,
  type AgentEndpoints,
  type SocketFactory,
} from './channel.js';
import {
  AgentRequestError,
  emptyAgentIdentity,
  emptyAgentView,
  isAgentControlAction,
  isBaseModelRefusal,
  isChannelRefusal,
  isIdentityRefusal,
  readAgentBaseModel,
  readAgentChannelSetup,
  readAgentDoorWrite,
  readAgentDoors,
  readAgentMessage,
  readAgentView,
  readBaseModelReply,
  readControl,
  readEnvApiKeys,
  readIdentityReply,
  readPresence,
  readThought,
  type AgentBaseModel,
  type AgentBaseModelInput,
  type AgentChannelSetup,
  type AgentControl,
  type AgentControlAction,
  type AgentDoor,
  type AgentDoorWrite,
  type AgentEvent,
  type AgentFailureCode,
  type AgentIdentity,
  type AgentLinkStatus,
  type AgentMessage,
  type AgentView,
  type EnvApiKey,
} from './types.js';

/**
 * The link to the agent.
 *
 * Commands travel over HTTP and observation travels over a socket when there is
 * one, so a surface keeps working — polling — on a runtime that has no socket at
 * all. The two halves are deliberately not symmetric: a command that was refused
 * must be able to say so, while a missed event is caught by the next refresh.
 *
 * Nothing here writes agent state into the agent. A surface draws what the agent
 * reports about itself and asks for changes; the agent decides whether to make
 * them. A dashboard that could set an intention's status by itself would be a
 * second, unreviewed writer to the thing it is meant to be showing.
 */

/** Backoff bounds for the event stream, matching the kit's other reconnect law. */
const RECONNECT_MIN_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

/** How often a socket-less surface re-reads the snapshot. */
const POLL_INTERVAL_MS = 4_000;

/** Coalescing window for "something changed that only a refresh knows about". */
const REFRESH_DEBOUNCE_MS = 180;

/**
 * How many already-seen declines are remembered for a waiter that has not armed
 * yet. Far more than the handful of questions a surface can have in flight, and
 * bounded because it is a gap to bridge, not a history to keep.
 */
const DECLINE_MEMORY = 32;

/**
 * A channel name this program is willing to send through, or `undefined`.
 *
 * A shape check rather than a known set: the deployment decides which doors
 * exist, and a build that refused a channel the agent had actually opened would
 * fail exactly when the feature works. What is refused is everything that is not
 * an identifier — the value reaches a request body that decides where the agent's
 * own reply gets delivered.
 */
function sanitizeChannel(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const clean = value.trim().slice(0, MAX_CHANNEL_LENGTH);
  return /^[\w:.-]+$/.test(clean) ? clean : undefined;
}

/**
 * A destination address, or `null`.
 *
 * Bounded like the channel and for the same reason — this string decides where a
 * message physically goes — but not pattern-checked, because the shape is the
 * channel's own business: a Telegram chat id, an E.164 number, a Discord channel
 * id. A kit that insisted on one of those would refuse a channel added after this
 * file was written, and the bridge is the right place to make that call because
 * the bridge is what knows which doors exist.
 */
function sanitizeAddress(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const clean = value.trim().slice(0, MAX_ADDRESS_LENGTH);
  return clean ? clean : null;
}

/**
 * A turn is given a budget of *silence*, not of time.
 *
 * An agent may think for minutes, and a life cycle between a question and its
 * answer can be long; but a link that has stopped moving is not thinking, it is
 * broken, and a reader left staring at "thinking…" forever cannot tell those
 * apart. So the guard is re-armed by every sign of life the agent gives — a new
 * cycle, a thought, an action — and expires only when nothing at all happens.
 */
export const REPLY_STALL_MS = 150_000;

/**
 * How long the link may hear nothing at all before the agent is not on it.
 *
 * Far longer than the agent's own longest quiet stretch: it publishes on every
 * cycle while working and every half minute while paused, and it does not stop
 * for the night — there is no hour of the day in which it is off. So a gap this
 * wide is not a slow agent, it is an absent one, and the two have opposite fixes.
 */
export const AGENT_ABSENT_MS = 900_000;

export interface AgentClientOptions {
  /** Base http(s) origin of the agent's interface service. */
  url: string;
  /** Who the reader is, as the agent's contact book names them. */
  personId?: string;
  /**
   * Which door to talk to the agent through. `web` unless a surface says otherwise.
   *
   * Named `sendChannel` and not `channel` because `channel` here already means
   * the socket the observations arrive on — the transport, not the destination.
   * Two different things, one word, is how a reader ends up filtering a websocket.
   */
  sendChannel?: string;
  /**
   * Where the chosen door reaches this person, when the deployment's contact book
   * has not been taught it.
   *
   * A destination rather than a person — a `tg:` chat id, a `dc:` channel id — and
   * left unset in the ordinary case, because the bridge already looks the real
   * address up in the contact book and a value hard-coded here would be a second
   * one to keep in step with it. It exists for the one case the book cannot
   * answer: somebody who knows the deployment well enough to name an address
   * themselves, which is exactly what `phone send --to` is.
   */
  sendAddress?: string;
  now?: () => number;
  fetchImpl?: typeof fetch;
  channel?: AgentChannel | null;
  socketFactory?: SocketFactory;
  replyStallMs?: number;
  pollIntervalMs?: number;
  absentAfterMs?: number;
}

/** Per-message overrides for `sendMessage`, for the one-shot paths. */
export interface AgentSendOptions {
  /**
   * The door to send through, overriding the client's current one.
   *
   * Per message rather than per client because `phone send --channel telegram` is
   * a single question asked in a single place; having to move the whole terminal
   * to Telegram to ask it and then move it back would make the flag a setting in
   * disguise.
   */
  channel?: string;
  /**
   * Where that door reaches the person, when the contact book has not been told.
   *
   * A destination, not a person: a `tg:` chat id, a `dc:` channel id. Ignored when
   * the deployment's own mapping already knows this person, because the two
   * agreeing is the ordinary case and disagreeing is the operator's to resolve.
   */
  address?: string;
}

/** The sections only a full read knows about. Coalesced into one request. */
export type RefetchSection = 'intentions' | 'actions' | 'budget' | 'guardian' | 'presence';

interface ReplyWaiter {
  after: number;
  /** Messages already on the timeline when the question went out. */
  seen: ReadonlySet<string>;
  /** The agent's own id for the question, so a decline can be tied to it. */
  asked: string;
  resolve(message: AgentMessage): void;
  reject(error: unknown): void;
  armedAt: number;
  onProgress(): void;
  timer: ReturnType<typeof setTimeout> | null;
}

export class AgentClient {
  private readonly listeners = new Set<(event: AgentEvent) => void>();
  private readonly now: () => number;
  private readonly fetcher: typeof fetch;
  private readonly personId: string;
  private readonly replyStallMs: number;
  private readonly pollIntervalMs: number;
  private readonly absentAfterMs: number;
  private readonly waiters = new Set<ReplyWaiter>();
  /**
   * When the agent last showed any sign of life, or `null` for a link that has
   * heard none.
   *
   * Read from what actually arrived rather than from a clock of our own: a
   * presence, a thought, an action, a message, and a snapshot whose cycle has
   * moved on are all the agent saying it is here. It is what separates an agent
   * that has gone quiet from a machine with no agent on it, which is otherwise
   * the same wait run to the same end.
   */
  private lastLifeAt: number | null = null;
  /**
   * True once this link has actually asked the agent something.
   *
   * Without it "no sign of life" cannot be told from "not looked yet", and a
   * reader who typed before the first read came back would be told the agent is
   * not running on the strength of a link that has not spoken.
   */
  private hasRead = false;
  /**
   * Declines already seen, by the message they decline.
   *
   * A question is asked over HTTP and the answer is waited for afterwards, so a
   * decline for a question the agent had already read can land in the gap
   * between the two — before there is a waiter to give it to. Keeping the recent
   * ones means that gap loses nothing: a waiter that arms after its decline has
   * already arrived still finds it, instead of waiting out the whole budget for
   * an answer the agent has already accounted for. Bounded, because it is a
   * short gap and not a log.
   */
  private readonly declines = new Map<string, { code: AgentFailureCode; reason: string }>();

  private view: AgentView;
  private status: AgentLinkStatus = 'offline';
  private endpoints: AgentEndpoints | null = null;
  /** The door commands go out of. The socket is `socketChannel`, below. */
  private sendChannel: string;
  private sendAddress: string | null;
  private channel: AgentChannel | null = null;
  private readonly injectedChannel: AgentChannel | null;
  private readonly socketFactory: SocketFactory | undefined;

  private attempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  private pendingRefresh = new Set<RefetchSection>();
  private inFlight: Promise<boolean> | null = null;
  private disposed = false;
  /** True when the runtime opened a socket; false means observation is polling. */
  private streaming = false;

  constructor(options: AgentClientOptions) {
    this.now = options.now ?? Date.now;
    this.fetcher = options.fetchImpl ?? ((input, init) => fetch(input, init));
    this.personId = (options.personId ?? 'owner').trim() || 'owner';
    // Bounded and stripped here rather than trusted from a surface. This string
    // reaches the bridge, which answers on the channel a message arrives on, so
    // an unbounded value from a shared-state file would be a way to name
    // something that is not a channel at all.
    this.sendChannel = sanitizeChannel(options.sendChannel) ?? 'web';
    this.sendAddress = sanitizeAddress(options.sendAddress);
    this.replyStallMs = options.replyStallMs ?? REPLY_STALL_MS;
    this.pollIntervalMs = options.pollIntervalMs ?? POLL_INTERVAL_MS;
    this.absentAfterMs = options.absentAfterMs ?? AGENT_ABSENT_MS;
    this.injectedChannel = options.channel ?? null;
    this.socketFactory = options.socketFactory;
    this.view = emptyAgentView(0);
    this.endpoints = safeEndpoints(options.url);
  }

  // ------------------------------------------------------------- observation

  getStatus(): AgentLinkStatus {
    return this.status;
  }

  getView(): AgentView {
    return this.view;
  }

  /**
   * Which door this client's messages go out of.
   *
   * A method rather than a public field so it can be read without being written.
   * Changing it changes which conversation a question belongs to, so it is a
   * decision — and a decision that can be made per client, the way the person id
   * is.
   */
  getChannel(): string {
    return this.sendChannel;
  }

  /**
   * Moves this client to another door.
   *
   * A no-op for the same channel, so a filter re-clicking what is already
   * selected cannot churn the connection. It does not clear the view: the
   * timeline is the agent's, and hiding another channel's traffic from it would
   * make the agent look like it had stopped talking to somebody.
   */
  setChannel(channel: string): void {
    this.sendChannel = sanitizeChannel(channel) ?? 'web';
  }

  /**
   * Where the chosen door reaches this person, or `null` to let the deployment's
   * contact book decide.
   *
   * A method rather than a public field, for the same reason `setChannel` is one:
   * both of these are decisions about where a conversation belongs, and a
   * decision that can be read without being written cannot drift from the value
   * actually being sent.
   */
  getAddress(): string | null {
    return this.sendAddress;
  }

  setAddress(address: string | null): void {
    this.sendAddress = sanitizeAddress(address);
  }

  /** True when new state arrives over a socket; false means this surface polls. */
  isStreaming(): boolean {
    return this.streaming;
  }

  subscribe(listener: (event: AgentEvent) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /**
   * Points the link at a different agent.
   *
   * Changing the endpoint is a change of identity, so the socket is torn down and
   * the view is emptied: showing the previous agent's intentions next to the new
   * agent's presence would be two agents wearing one screen.
   */
  setUrl(url: string): void {
    const next = safeEndpoints(url);
    if (next && this.endpoints && next.http === this.endpoints.http) return;
    this.stopStreams();
    this.endpoints = next;
    this.view = emptyAgentView(0);
    // A different agent is a different set of evidence: what the last one said
    // about being alive says nothing about this one.
    this.hasRead = false;
    this.lastLifeAt = null;
    this.emit({ type: 'view', view: this.getView() });
    this.setStatus('offline');
    if (next) void this.connect();
  }

  async connect(): Promise<boolean> {
    if (this.disposed || !this.endpoints) return false;
    this.setStatus(this.status === 'offline' ? 'connecting' : this.status);
    const loaded = await this.refresh();
    if (this.disposed) return false;
    this.startStreams();
    return loaded;
  }

  disconnect(): void {
    this.stopStreams();
    this.failWaiters(new AgentRequestError('agent_offline', 'disconnected'));
    this.setStatus('offline');
  }

  dispose(): void {
    this.disconnect();
    this.disposed = true;
    this.listeners.clear();
  }

  /**
   * Reads the whole agent once.
   *
   * Concurrent callers share one request: the event stream asks for a refresh
   * the moment a change lands, and a surface asking at the same moment must not
   * turn one change into two full reads of every table.
   */
  async refresh(): Promise<boolean> {
    if (this.disposed || !this.endpoints) return false;
    if (this.inFlight) return this.inFlight;
    const run = this.readSnapshot().finally(() => {
      this.inFlight = null;
    });
    this.inFlight = run;
    return run;
  }

  private async readSnapshot(): Promise<boolean> {
    const endpoints = this.endpoints;
    if (!endpoints) return false;
    let payload: unknown;
    try {
      const response = await this.fetcher(`${endpoints.http}/api/snapshot`, {
        headers: { accept: 'application/json' },
      });
      if (!response.ok) throw new AgentRequestError('agent_unreachable', `http_${response.status}`);
      payload = await response.json();
    } catch (error) {
      this.noteUnreachable(error);
      return false;
    }
    if (this.disposed) return false;
    this.hasRead = true;
    const previousCycle = this.view.presence.ts;
    const next = readAgentView(payload, this.timestamp());
    this.view = next;
    // A cycle the agent has recorded since the last read is the agent saying it
    // is still going — and it says *when*, on its own clock. Reading that as a
    // sign of life at the moment it was fetched is how a snapshot carrying a
    // cycle from an hour ago keeps a machine with no agent on it looking alive,
    // which is the same silence this is meant to name. So the evidence is dated
    // by the agent, and only recent evidence re-arms a wait.
    const cycledAt = next.presence.ts ? Date.parse(next.presence.ts) : Number.NaN;
    if (Number.isFinite(cycledAt) && next.presence.ts !== previousCycle) {
      this.lastLifeAt = Math.max(this.lastLifeAt ?? 0, cycledAt);
      if (cycledAt > this.timestamp() - this.absentAfterMs) {
        for (const waiter of this.waiters) waiter.onProgress();
      }
    }
    this.emit({ type: 'view', view: this.getView() });
    this.setStatus('online');
    this.attempt = 0;
    this.settleWaiters(next.messages);
    return true;
  }

  private noteUnreachable(error: unknown): void {
    const code = error instanceof AgentRequestError ? error.code : 'agent_unreachable';
    const stopped = this.view.control.stopped || this.view.control.emergency;
    this.setStatus(stopped ? 'degraded' : 'offline');
    this.emit({ type: 'error', message: error instanceof Error ? error.message : String(error) });
    // A link that cannot be reached cannot hold a turn open, so the waiters are
    // told now rather than left to time out against a service that is not there.
    if (code === 'agent_unreachable' || code === 'agent_offline') {
      this.failWaiters(new AgentRequestError(code, 'unreachable'));
    }
  }

  // ---------------------------------------------------------------- commands

  /**
   * Hands a line to the agent.
   *
   * The returned id is the agent's own row id, which is what makes the reply
   * that follows correlatable instead of merely the next thing that arrived.
   *
   * The door and the address come from the client, with whatever this call names
   * overriding them. Both are read at the moment of the send rather than captured
   * at construction, because the turn goes out through the transport — and the
   * transport is shared by the whole session, so "which door" is a property of
   * the moment a question is asked, not of the client object.
   */
  async sendMessage(text: string, options: AgentSendOptions = {}): Promise<string> {
    const content = sanitizeStoredText(text, MAX_PROMPT_LENGTH).trim();
    if (!content) throw new AgentRequestError('agent_refused', 'empty');
    const endpoints = this.requireEndpoints();
    const channel = sanitizeChannel(options.channel) ?? this.sendChannel;
    const body: Record<string, unknown> = {
      text: content,
      person_id: this.personId,
      channel,
    };
    // Only sent when there is one. An empty `address` would reach the bridge as a
    // destination of `""`, which is a place rather than the absence of one, and
    // the bridge's own lookup is what should decide this.
    const address = sanitizeAddress(options.address) ?? this.sendAddress;
    if (address) body.address = address;
    const response = await this.post(`${endpoints.http}/api/message`, body);
    const payload = (await response.json().catch(() => ({}))) as { message_id?: unknown };
    const id = typeof payload.message_id === 'string' ? payload.message_id : '';
    if (!id) throw new AgentRequestError('agent_refused', 'no_message_id');
    return id;
  }

  /**
   * Moves the lifecycle. `resume` is the only one that clears `emergency`, so
   * the surface asks for it deliberately rather than offering a blanket restart.
   */
  async control(action: AgentControlAction): Promise<AgentControl> {
    if (!isAgentControlAction(action)) {
      throw new AgentRequestError('agent_refused', 'unknown_action');
    }
    const endpoints = this.requireEndpoints();
    await this.post(`${endpoints.http}/api/control`, { action, by: 'phone' });
    // The flags are merged in the agent's own store, so the new value is read
    // back rather than assumed — `stopped` has to survive a later `resume`, and
    // assuming otherwise is how a stopped agent looks restarted.
    await this.refresh();
    this.settleWaiters(this.view.messages);
    return this.view.control;
  }

  /** Queues an undo for a journalled action that recorded one. */
  async undoAction(id: string): Promise<void> {
    const actionId = id.trim();
    if (!actionId) throw new AgentRequestError('agent_refused', 'no_action_id');
    const endpoints = this.requireEndpoints();
    await this.post(`${endpoints.http}/api/actions/${encodeURIComponent(actionId)}/undo`, {
      requested_by: this.personId,
    });
  }

  /** Asks the agent to cancel an intention. It may decline, and then it still stands. */
  async closeIntention(id: string): Promise<void> {
    const intentionId = id.trim();
    if (!intentionId) throw new AgentRequestError('agent_refused', 'no_intention_id');
    const endpoints = this.requireEndpoints();
    await this.post(`${endpoints.http}/api/intentions/${encodeURIComponent(intentionId)}/close`, {
      by: this.personId,
    });
  }

  // ----------------------------------------------------------- the base model

  /**
   * The model the agent thinks with, or `null` when it cannot be asked.
   *
   * `null` and "nothing is configured" are different answers and are kept apart:
   * one means the settings screen has nothing to show, the other means the agent
   * is running on its own catalogue. A surface that conflated them would tell
   * somebody their agent had no model when the agent was simply not reachable.
   *
   * A body that is not JSON is the same as no answer, and deliberately so. It is
   * what a service without this route sends instead: a single-page app answers
   * any unmatched path with its own HTML and a 200, so a reader that believed
   * the status would report a healthy agent with no base model — a false all-clear
   * on exactly the question somebody opened the screen to ask.
   */
  async readBaseModel(): Promise<AgentBaseModel | null> {
    const endpoints = this.endpoints;
    if (!endpoints) return null;
    try {
      const response = await this.fetcher(`${endpoints.http}/api/model`, {
        headers: { accept: 'application/json' },
      });
      if (!response.ok) return null;
      return readBaseModelReply(await response.json());
    } catch {
      return null;
    }
  }

  /**
   * Writes the agent's base model.
   *
   * An empty `apiKey` keeps the key already stored rather than clearing it, so
   * the model can be changed from a form that never held the credential. Removing
   * it is `clearKey`, because an empty field and an untouched field look the same
   * and only one of them means anything. `useEnv` is the third answer: take the
   * key from the provider's variable, which the agent records by name and reads
   * afresh on every request.
   *
   * A refusal comes back as the code the agent gave rather than as a bare
   * failure, so a person is told which of the four things they typed was wrong.
   */
  async saveBaseModel(input: AgentBaseModelInput): Promise<AgentBaseModel> {
    const body: Record<string, unknown> = {
      provider: input.provider.trim(),
      model: input.model.trim(),
      base_url: input.baseUrl.trim(),
    };
    if (input.apiKey?.trim()) body.api_key = input.apiKey.trim();
    if (input.clearKey) body.clear_key = true;
    // A typed key wins over the variable: somebody who has just pasted one is
    // trying that one, and quietly substituting the environment's would save a
    // model they did not choose.
    if (input.useEnv && !input.apiKey?.trim()) body.use_env = true;
    const result = await this.postBaseModel(body);
    if (!result.ok) throw new AgentRequestError('agent_refused', result.refusal);
    return readAgentBaseModel(result.payload);
  }

  /** Asks the agent to go back to its own catalogue. */
  async clearBaseModel(): Promise<AgentBaseModel> {
    const result = await this.postBaseModel({ enabled: false });
    if (!result.ok) throw new AgentRequestError('agent_refused', result.refusal);
    return readAgentBaseModel(result.payload);
  }

  /**
   * The doors the agent has open, and who each one can reach.
   *
   * Asked of the agent rather than worked out here, because only the deployment
   * knows: whether a channel is on, whether its allowlist admits anybody, and the
   * address it reaches a person at all live in files the agent reads. A surface
   * that inferred any of it would be guessing, and the two guesses that matter are
   * both silent — offering a door that is shut, and offering no door but the local
   * line when Telegram is configured and quiet.
   *
   * Never rejects. A bridge that is down, or an older one with no such route, is
   * reported as the local line alone, because that is the one door whose being
   * open needs nothing configured.
   */
  async doors(): Promise<AgentDoor[]> {
    const endpoints = this.endpoints;
    if (!endpoints) return readAgentDoors(null);
    try {
      const response = await this.fetcher(`${endpoints.http}/api/channels`, {
        method: 'GET',
        headers: { accept: 'application/json' },
      });
      if (!response.ok) return readAgentDoors(null);
      return readAgentDoors(await response.json().catch(() => null));
    } catch {
      return readAgentDoors(null);
    }
  }

  /**
   * Every door, and how each one is configured.
   *
   * Asked of the agent rather than worked out here, for the reason `doors` gives:
   * only the deployment knows which doors exist, and a credential used to exist
   * only as the *name* of an environment variable — so a person who installed the
   * agent had a settings screen and a terminal and no way at all to hand it one.
   *
   * `null` when the agent cannot be asked, which is a different answer from "no
   * doors" and is the one a form has to be able to say: a field over a machine it
   * has never reached is a promise its save button cannot keep.
   */
  async channelSetup(): Promise<AgentChannelSetup | null> {
    const endpoints = this.endpoints;
    if (!endpoints) return null;
    try {
      const response = await this.fetcher(
        `${endpoints.http}/api/channels?person=${encodeURIComponent(this.personId)}`,
        { method: 'GET', headers: { accept: 'application/json' } },
      );
      if (!response.ok) return null;
      return readAgentChannelSetup(await response.json().catch(() => null));
    } catch {
      return null;
    }
  }

  /**
   * Writes one door's settings, and reads the answer rather than throwing past it.
   *
   * Only what the caller mentions is changed, so a form saving the allowlist does
   * not clear the token beside it. An empty credential keeps the one on file and
   * `null` removes it, the same distinction a base model makes — and for the same
   * reason: a field somebody did not fill in and a field somebody emptied mean
   * different things, and only one of them can be told from the other.
   */
  async saveDoor(write: AgentDoorWrite): Promise<AgentChannelSetup> {
    const body: Record<string, unknown> = { id: write.id.trim(), person: this.personId };
    if (write.enabled !== undefined) body.enabled = write.enabled;
    if (write.credentials) {
      const credentials: Record<string, string | null> = {};
      for (const [key, value] of Object.entries(write.credentials)) {
        const name = sanitizeChannel(key) ?? '';
        if (!name) continue;
        credentials[name] = value === null ? null : value.trim();
      }
      body.credentials = credentials;
    }
    if (write.allowed) body.allowed = write.allowed.map((value) => value.trim()).filter(Boolean);
    // Sent only when it is mentioned, for the same reason `enabled` is: `undefined`
    // leaves the door as it is and `false` closes one somebody opened on purpose.
    // A boolean rather than a string, because the agent refuses anything else and a
    // form that coerced it would be opening a door it thought it was describing.
    if (write.acceptFromAnyone !== undefined) body.accept_from_anyone = write.acceptFromAnyone;
    if (write.address !== undefined) body.address = write.address === null ? null : write.address.trim();
    const result = await this.postChannel(body);
    if (!result.ok) throw new AgentRequestError('agent_refused', result.refusal);
    return readAgentDoorWrite(result.payload);
  }

  /**
   * The vendor's own list of models, for the base-model form to offer.
   *
   * Asked of the agent rather than fetched here, because the agent is the side
   * holding the key. This is the whole reason a page can show a real catalogue
   * for a keyed provider without ever being given the credential — and the reason
   * it cannot be done by a caller with only a `Settings` object.
   *
   * Never rejects. A provider that is down, a key that is wrong and an endpoint
   * that is refused all come back as `source: 'offline'` with a reason and
   * whatever fallback list exists, because a form that keeps working with a short
   * list is recoverable and a form that threw is not. The `agent` here means the
   * interface service, which is the same word for two things in this file: it is
   * the one answering, and it is the one that will refuse.
   */
  async discoverBaseModels(
    candidate: Partial<AgentBaseModelInput> = {},
  ): Promise<ModelDiscoveryResult> {
    const endpoints = this.endpoints;
    if (!endpoints) return { models: [], source: 'offline', error: 'agent_unreachable' };
    const body: Record<string, unknown> = {};
    if (candidate.provider?.trim()) body.provider = candidate.provider.trim();
    if (candidate.baseUrl?.trim()) body.base_url = candidate.baseUrl.trim();
    // The key that was just typed is what the catalogue is fetched with. It is
    // sent for the fetch and not stored, so asking a question about a second
    // vendor cannot leave a credential behind in the first one's file.
    if (candidate.apiKey?.trim()) body.api_key = candidate.apiKey.trim();
    if (candidate.useEnv && !candidate.apiKey?.trim()) body.use_env = true;
    const offline = (error: string): ModelDiscoveryResult => ({ models: [], source: 'offline', error });
    try {
      const response = await this.fetcher(`${endpoints.http}/api/models`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify(body),
      });
      if (!response.ok) return offline(`http_${response.status}`);
      const payload = (await response.json().catch(() => ({}))) as {
        models?: unknown;
        source?: unknown;
        error?: unknown;
        key_source?: unknown;
      };
      const models = Array.isArray(payload.models)
        ? payload.models.filter((entry): entry is string => typeof entry === 'string')
        : [];
      return {
        models,
        source: payload.source === 'remote' ? 'remote' : 'offline',
        ...(typeof payload.error === 'string' ? { error: payload.error } : {}),
        ...(typeof payload.key_source === 'string' ? { keySource: payload.key_source } : {}),
      };
    } catch {
      // The service, not the provider: this is the one place where the fetch that
      // failed was ours, and the two must not share a code. A form reads
      // `agent_unreachable` as "start the agent" and `network_unavailable` as "the
      // vendor is down", and a page that cannot tell them apart falls back to
      // guessing which party to try next.
      return offline('agent_unreachable');
    }
  }

  /**
   * Which providers have a key in the agent process's environment.
   *
   * Asked over the same link as everything else about the agent, and read as
   * *facts* — a name, a presence and a hint — because a page cannot read a
   * process's environment and must not be handed the value that is in it.
   *
   * Never rejects and never returns a failure: an empty list means "nothing was
   * reported", and a settings screen with no button to offer is a screen that
   * still works. A service without the route answers with its own HTML, which is
   * not a report of anything.
   */
  async readEnvApiKeys(): Promise<EnvApiKey[]> {
    const endpoints = this.endpoints;
    if (!endpoints) return [];
    try {
      const response = await this.fetcher(`${endpoints.http}/api/env-keys`, {
        headers: { accept: 'application/json' },
      });
      if (!response.ok) return [];
      return readEnvApiKeys(await response.json());
    } catch {
      return [];
    }
  }

  /**
   * What the agent has been named, or `null` when the agent cannot be asked.
   *
   * `null` and "nobody has named it" are different answers and both are reachable, so
   * they are different values. The first is a service that is not answering and the
   * second is an answer that says there is no name yet; a surface that collapsed them
   * would offer to write a name into a machine it had not reached.
   */
  async readIdentity(): Promise<AgentIdentity | null> {
    const endpoints = this.endpoints;
    if (!endpoints) return null;
    try {
      const response = await this.fetcher(`${endpoints.http}/api/identity`, {
        headers: { accept: 'application/json' },
      });
      if (!response.ok) return null;
      return readIdentityReply(await response.json());
    } catch {
      return null;
    }
  }

  /**
   * Names the agent.
   *
   * Goes to the agent's own configuration rather than into this surface's settings,
   * because a name that lives in the interface is a name only the interface knows: the
   * browser would remember it and the terminal would forget it, and neither of them
   * is where the agent looks for what it is called.
   *
   * A refusal comes back as the code the agent gave, for the same reason the base
   * model's does — the two refusals a person can cause are a name that is not a name
   * and a home that cannot be written, and they have different fixes.
   */
  async saveIdentity(selfName: string): Promise<AgentIdentity> {
    const result = await this.postIdentity({ self_name: selfName.trim() });
    if (!result.ok) throw new AgentRequestError('agent_refused', result.refusal);
    return readIdentityReply(result.payload) ?? emptyAgentIdentity();
  }

  /** Takes the name back, leaving the agent with whatever its config calls it. */
  async clearIdentity(): Promise<AgentIdentity> {
    const result = await this.postIdentity({ enabled: false });
    if (!result.ok) throw new AgentRequestError('agent_refused', result.refusal);
    return readIdentityReply(result.payload) ?? emptyAgentIdentity();
  }

  /**
   * Posts a name, reading the refusal instead of throwing past it.
   *
   * The same two-outcome shape as `postBaseModel`, and for the same reason: these
   * routes answer 400 with a body naming which thing was wrong, and throwing that away
   * would leave a person with "the agent refused" and nothing to act on.
   */
  private async postIdentity(
    body: Record<string, unknown>,
  ): Promise<{ ok: true; payload: unknown } | { ok: false; refusal: string }> {
    const endpoints = this.requireEndpoints();
    const controller = new AbortController();
    let response: Response;
    try {
      response = await this.fetcher(`${endpoints.http}/api/identity`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (error) {
      this.noteUnreachable(error);
      throw new AgentRequestError('agent_unreachable', 'network');
    }
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    if (response.ok) return { ok: true, payload };
    if (isIdentityRefusal(payload.error)) return { ok: false, refusal: payload.error };
    throw new AgentRequestError(
      response.status >= 500 ? 'agent_unreachable' : 'agent_refused',
      `http_${response.status}`,
    );
  }

  /**
   * Posts a base model, reading the refusal instead of throwing past it.
   *
   * `post` treats any non-2xx as a failure, which is right for the agent's other
   * routes: they answer with a reason in the status, and there is nothing finer
   * to say. These answer 400 with a body naming *which* of four things was
   * wrong, and throwing that away would leave a person with "the agent refused"
   * and nothing to act on.
   */
  private async postBaseModel(
    body: Record<string, unknown>,
  ): Promise<{ ok: true; payload: unknown } | { ok: false; refusal: string }> {
    const endpoints = this.requireEndpoints();
    const controller = new AbortController();
    let response: Response;
    try {
      response = await this.fetcher(`${endpoints.http}/api/model`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (error) {
      this.noteUnreachable(error);
      throw new AgentRequestError('agent_unreachable', 'network');
    }
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    if (response.ok) return { ok: true, payload };
    if (isBaseModelRefusal(payload.error)) return { ok: false, refusal: payload.error };
    throw new AgentRequestError(
      response.status >= 500 ? 'agent_unreachable' : 'agent_refused',
      `http_${response.status}`,
    );
  }

  /**
   * Posts a door write, reading the refusal instead of throwing past it.
   *
   * The same two-outcome shape as `postBaseModel` and for the same reason: this
   * route answers 400 with a body naming which of seven things was wrong, and
   * discarding that would leave a person with "the agent refused" and a chat id
   * they cannot tell was the problem.
   */
  private async postChannel(
    body: Record<string, unknown>,
  ): Promise<{ ok: true; payload: unknown } | { ok: false; refusal: string }> {
    const endpoints = this.requireEndpoints();
    const controller = new AbortController();
    let response: Response;
    try {
      response = await this.fetcher(`${endpoints.http}/api/channels`, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (error) {
      this.noteUnreachable(error);
      throw new AgentRequestError('agent_unreachable', 'network');
    }
    const payload = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    if (response.ok) return { ok: true, payload };
    if (isChannelRefusal(payload.error)) return { ok: false, refusal: payload.error };
    throw new AgentRequestError(
      response.status >= 500 ? 'agent_unreachable' : 'agent_refused',
      `http_${response.status}`,
    );
  }

  private async post(url: string, body: unknown): Promise<Response> {
    const controller = new AbortController();
    try {
      const response = await this.fetcher(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        throw new AgentRequestError(
          response.status >= 500 ? 'agent_unreachable' : 'agent_refused',
          `http_${response.status}`,
        );
      }
      return response;
    } catch (error) {
      this.noteUnreachable(error);
      throw error instanceof AgentRequestError
        ? error
        : new AgentRequestError('agent_unreachable', 'network');
    }
  }

  private requireEndpoints(): AgentEndpoints {
    if (!this.endpoints) throw new AgentRequestError('agent_invalid_endpoint', 'not_configured');
    return this.endpoints;
  }

  // ------------------------------------------------------------------ replies

  /**
   * Waits for the agent's answer to a line sent at `after`.
   *
   * Resolves with the agent's own message, or refuses with a reason. It never
   * resolves with an empty string: an agent that has nothing to say has not said
   * something empty, and the surface must be able to show that difference.
   *
   * `exclude` is the timeline as it stood before the question went out. Matching
   * on time alone would let the previous turn's reply satisfy this wait on a
   * clock that reads a little fast.
   *
   * `asked` is the id the agent gave the question. It is what lets the agent's
   * "I heard you and chose not to answer" be reported as a decline of *this*
   * question rather than as a link that stopped moving.
   */
  async awaitReply(
    after: number,
    signal?: AbortSignal,
    exclude?: ReadonlySet<string>,
    asked = '',
  ): Promise<AgentMessage> {
    const seen = exclude ?? new Set<string>();
    const match = (messages: readonly AgentMessage[]): AgentMessage | undefined => messages.find(
      (message) => message.direction === 'outbound' && message.ts >= after && !seen.has(message.id),
    );
    const existing = match(this.view.messages);
    if (existing) return existing;
    // A decline that arrived before this waiter existed is still this waiter's
    // answer. The question is asked over HTTP and waited on afterwards, so the
    // agent can read it, decide against replying, and say so inside that gap.
    if (asked && this.declines.has(asked)) {
      const declined = this.declines.get(asked);
      this.declines.delete(asked);
      throw new AgentRequestError(declined?.code ?? 'agent_declined', declined?.reason ?? '');
    }
    if (!this.endpoints) {
      throw new AgentRequestError('agent_offline', 'not_connected');
    }
    return new Promise<AgentMessage>((resolve, reject) => {
      const waiter: ReplyWaiter = {
        after,
        seen,
        asked,
        resolve,
        reject,
        armedAt: this.timestamp(),
        onProgress: () => {
          if (waiter.timer) clearTimeout(waiter.timer);
          waiter.timer = setTimeout(() => {
            this.failWaiters(this.silenceFailure(), waiter);
          }, this.replyStallMs);
          // Deliberately *not* unref'd, unlike every other timer in this file.
          // The others are conveniences — nobody is waiting on a poll. This one
          // is the only thing standing between an outstanding turn and a turn
          // that never finishes: `phone send` awaits it and prints nothing else
          // until it settles, so a guard that does not hold the process open is a
          // guard that lets the process exit having said nothing at all. It
          // cannot hang anything either, because every path that ends a wait
          // clears its timer.
        },
        timer: null,
      };
      this.waiters.add(waiter);
      waiter.onProgress();
      signal?.addEventListener('abort', () => {
        this.failWaiters(new AgentRequestError('agent_aborted', 'interrupted'), waiter);
      }, { once: true });
    });
  }

  /**
   * Settles the waiters whose question the agent has now finished with in
   * silence.
   *
   * A decline is only ever read as a decline when it names the question that was
   * asked: an `ignore` aimed at some other message on the timeline says nothing
   * about this turn, and treating it as an answer would end a wait that is still
   * owed a real one. A waiter that cannot be identified waits out its budget
   * rather than being refused on a stranger's say-so.
   *
   * Silence that was *chosen* and silence that nobody got to are different
   * events, and the agent says which it is (`decided`). It has to, because both
   * arrive as `act_silently` with no outbound row: a model that failed, ran out
   * of room before writing anything, or answered in prose all produced exactly
   * the same thing a considered `ignore` produces. Reading them alike told
   * somebody the agent had chosen not to reply to every message it was sent,
   * when the truth was that it had not managed to think — and the two call for
   * opposite things, since one is worth sending again.
   */
  private settleDeclined(payload: Record<string, unknown>): void {
    const declined = typeof payload.message_id === 'string' ? payload.message_id : '';
    if (!declined) return;
    const reason = typeof payload.reason === 'string' ? payload.reason.slice(0, 120) : '';
    const decided = payload.decided !== false;
    const code: AgentFailureCode = decided ? 'agent_declined' : 'agent_unanswered';
    for (const waiter of [...this.waiters]) {
      if (!waiter.asked || waiter.asked !== declined) continue;
      this.declines.delete(declined);
      this.settleWaiter(waiter, () => waiter.reject(new AgentRequestError(code, reason)));
      return;
    }
    // Remembered with its own code, so a waiter that arms after this arrived --
    // the gap between asking over HTTP and waiting -- still reads it as what it
    // was. The freshest word about a question wins, so a decline that follows a
    // failure replaces it, and a real reply outranks both.
    this.rememberDecline(declined, code, reason);
  }

  private rememberDecline(declined: string, code: AgentFailureCode, reason: string): void {
    this.declines.set(declined, { code, reason });
    while (this.declines.size > DECLINE_MEMORY) {
      const oldest = this.declines.keys().next();
      if (oldest.done) break;
      this.declines.delete(oldest.value);
    }
  }

  private settleWaiters(messages: readonly AgentMessage[]): void {
    if (!this.waiters.size) return;
    for (const waiter of [...this.waiters]) {
      const reply = messages.find(
        (message) => message.direction === 'outbound'
          && message.ts >= waiter.after
          && !waiter.seen.has(message.id),
      );
      if (reply) this.settleWaiter(waiter, () => waiter.resolve(reply));
    }
  }

  private settleWaiter(waiter: ReplyWaiter, finish: () => void): void {
    if (!this.waiters.has(waiter)) return;
    this.waiters.delete(waiter);
    if (waiter.timer) clearTimeout(waiter.timer);
    finish();
  }

  private failWaiters(error: unknown, only?: ReplyWaiter): void {
    const targets = only ? [only] : [...this.waiters];
    for (const waiter of targets) {
      this.settleWaiter(waiter, () => waiter.reject(error));
    }
  }

  // ------------------------------------------------------------------ streams

  private startStreams(): void {
    if (this.disposed || !this.endpoints) return;
    const channel = this.resolveChannel();
    if (!channel) {
      this.streaming = false;
      this.startPolling();
      return;
    }
    const handlers: AgentChannelHandlers = {
      onOpen: () => {
        this.streaming = true;
        this.stopPolling();
        this.attempt = 0;
        this.setStatus('online');
        // The socket's first act is to ask what it missed, so a reconnect
        // resynchronises the whole view instead of only the changes after it.
        void this.refresh();
      },
      onFrame: (frame) => this.handleFrame(frame),
      onClose: () => this.scheduleReconnect(),
    };
    try {
      channel.open(handlers);
      this.channel = channel;
      this.streaming = true;
    } catch {
      this.streaming = false;
      this.startPolling();
    }
  }

  private resolveChannel(): AgentChannel | null {
    if (this.injectedChannel) return this.injectedChannel;
    if (!this.endpoints) return null;
    if (this.socketFactory === undefined && !hasSocketSupport()) return null;
    try {
      return createSocketChannel(this.endpoints.events, this.socketFactory);
    } catch {
      return null;
    }
  }

  private handleFrame(frame: unknown): void {
    const payload = frame && typeof frame === 'object' ? (frame as Record<string, unknown>) : {};
    if (payload.type === 'snapshot_available') {
      void this.refresh();
      return;
    }
    if (payload.type === 'event') {
      const kind = typeof payload.kind === 'string' ? payload.kind : '';
      const body = payload.payload && typeof payload.payload === 'object' && !Array.isArray(payload.payload)
        ? (payload.payload as Record<string, unknown>)
        : {};
      if (kind) this.apply(kind, body);
      return;
    }
    // `error` frames and anything unrecognised are dropped. A frame the link
    // cannot read is not a reason to tear down a stream that is otherwise fine.
  }

  private scheduleReconnect(): void {
    if (this.disposed) return;
    this.streaming = false;
    this.startPolling();
    if (this.reconnectTimer) return;
    const delay = Math.min(RECONNECT_MIN_MS * 2 ** this.attempt, RECONNECT_MAX_MS);
    this.attempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.disposed || !this.endpoints) return;
      void this.refresh().then(() => this.startStreams());
    }, delay);
    unref(this.reconnectTimer);
  }

  private startPolling(): void {
    if (this.disposed || this.pollTimer) return;
    this.pollTimer = setInterval(() => {
      void this.refresh();
    }, this.pollIntervalMs);
    unref(this.pollTimer);
  }

  private stopPolling(): void {
    if (!this.pollTimer) return;
    clearInterval(this.pollTimer);
    this.pollTimer = null;
  }

  private stopStreams(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.refreshTimer) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = null;
    }
    this.pendingRefresh.clear();
    this.stopPolling();
    const channel = this.channel;
    this.channel = null;
    this.streaming = false;
    channel?.close();
    this.attempt = 0;
  }

  // ----------------------------------------------------------------- reducer

  /**
   * Applies one agent event to the view.
   *
   * Three kinds are answered from the event itself — presence, a thought, a
   * message — because they are the ones a reader is watching when they happen and
   * a full re-read would make a thought arrive a beat late. Everything else
   * marks its section for a refresh, which is the only way to learn what changed
   * in a table the event does not describe.
   */
  private apply(kind: string, payload: Record<string, unknown>): void {
    if (this.disposed) return;
    const previous = this.view;
    let next: AgentView | null = null;
    let section: RefetchSection | null = null;
    // Any event at all is the agent saying it exists, including the ones this
    // reducer does not draw. Watching for a thought, a presence and an action and
    // nothing else made a link that was being answered look dead whenever the
    // agent's only sign of life was some other kind.
    this.hasRead = true;
    this.signalLife();

    if (kind === 'presence.update') {
      const presence = readPresence({ ...previous.presence, ...payload });
      next = { ...previous, presence: { ...presence, ts: presence.ts ?? new Date(this.timestamp()).toISOString() } };
    } else if (kind === 'thought.new') {
      const thought = readThought({ id: payload.episode_id ?? payload.id, ts: payload.ts, summary: payload.summary });
      if (thought.id && thought.summary) {
        next = {
          ...previous,
          thoughts: [thought, ...previous.thoughts.filter((item) => item.id !== thought.id)].slice(0, 60),
        };
      }
    } else if (kind === 'message.new') {
      const message = readAgentMessage({
        id: payload.id,
        ts: payload.ts,
        direction: payload.direction,
        channel: payload.channel,
        person_id: payload.person_id,
        text: payload.text,
        // The event carries the delivery block the agent recorded, so it is read
        // from there. Reading `status` off `urgency` reported every message as
        // though it had been sent with urgency `normal` and made a queued reply
        // indistinguishable from a delivered one.
        delivery: payload.delivery,
      });
      if (message.id && message.text) {
        next = {
          ...previous,
          messages: [...previous.messages.filter((item) => item.id !== message.id), message].slice(-100),
        };
        this.settleWaiters(next.messages);
      }
    } else if (kind === 'message.handled') {
      // The agent finished with a question and chose to write nothing. That is
      // its answer, and it is reported as one: a surface that waited out the
      // full stall budget and then said the agent had gone quiet was describing
      // a decision as a failure, and the person reading it had no way to tell
      // the two apart. Silence that was chosen arrives as silence that was
      // chosen.
      this.settleDeclined(payload);
    } else if (kind === 'control.update') {
      section = 'presence';
    } else if (kind === 'intention.update') {
      section = 'intentions';
    } else if (kind === 'action.update') {
      section = 'actions';
    } else if (kind === 'budget.update') {
      section = 'budget';
    } else if (kind === 'guardian.update') {
      section = 'guardian';
    }

    if (next) {
      this.view = { ...next, updatedAt: this.timestamp() };
      this.emit({ type: 'view', view: this.getView() });
    }
    if (section) this.requestRefresh(section);
    this.emit({ type: 'agent', kind, payload });
  }

  private requestRefresh(section: RefetchSection): void {
    this.pendingRefresh.add(section);
    if (this.refreshTimer) return;
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      this.pendingRefresh.clear();
      void this.refresh();
    }, REFRESH_DEBOUNCE_MS);
    unref(this.refreshTimer);
  }

  /** A waiter re-arms its stall guard: something the agent did is not silence. */
  private signalLife(): void {
    this.lastLifeAt = this.timestamp();
    for (const waiter of this.waiters) waiter.onProgress();
  }

  /**
   * Why a wait ran out, in the code that names the situation.
   *
   * "Went quiet" is a statement about an agent that was there and stopped moving,
   * and its fix is `resume`. It is not a statement about a machine with no agent
   * running on it, whose fix is to start one — and a reader told the first when
   * the second was true is sent to the wrong button. A link that has heard nothing
   * since it opened is not waiting on a slow agent, so it says so.
   */
  private silenceFailure(): AgentRequestError {
    if (this.isAgentAbsent()) return new AgentRequestError('agent_not_running');
    return new AgentRequestError('agent_timeout', 'stalled');
  }

  /**
   * True when the agent has said nothing at all, or said it long enough ago that
   * it can only be counted as gone.
   *
   * False until this link has asked, because "no sign of life" and "not looked
   * yet" are the same null and only one of them is a fact about the agent. Exposed
   * because a surface should be able to say this *before* it waits, not only after
   * the whole budget is spent: asking a machine with no agent on it to answer is
   * not a question, and 150 seconds of nothing is a long time to learn it.
   */
  isAgentAbsent(): boolean {
    if (!this.hasRead) return false;
    const silentFor = this.lastLifeAt === null ? null : this.timestamp() - this.lastLifeAt;
    return silentFor === null || silentFor >= this.absentAfterMs;
  }

  private timestamp(): number {
    const value = this.now();
    return Number.isFinite(value) ? value : Date.now();
  }

  private setStatus(status: AgentLinkStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.emit({ type: 'status', status });
  }

  private emit(event: AgentEvent): void {
    for (const listener of [...this.listeners]) listener(event);
  }
}

/** The control flags a surface shows before anything has been read. */
export const IDLE_CONTROL: AgentControl = readControl(null);

/**
 * Lets a timer be dropped from Node's event loop.
 *
 * Every timer here is a courtesy — a poll, a reconnect, a stall guard — and a
 * courtesy must not be the reason a terminal refuses to exit or a test run hangs.
 * A browser has no such handle, so this is a no-op there.
 */
function unref(timer: { unref?: () => void } | null): void {
  timer?.unref?.();
}

function safeEndpoints(base: string): AgentEndpoints | null {
  try {
    return resolveAgentEndpoints(base);
  } catch {
    return null;
  }
}
