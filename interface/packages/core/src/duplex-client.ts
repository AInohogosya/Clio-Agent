import { createTranslator, type TranslationKey } from './i18n.js';
import { describeAgentFailure } from './agent/reasons.js';
import {
  discoverProviderModels,
  ProviderRequestError,
  requestProviderCompletion,
  type CompletionMessage,
} from './provider-adapter.js';
import { providerFor } from './providers.js';
import { normalizeMessages } from './normalize.js';
import {
  MAX_PROMPT_LENGTH,
  isSafeTimestamp,
  sanitizeStoredText,
} from './security.js';
import type {
  ChatMessage,
  ClientEvent,
  ClientSnapshot,
  ConnectionStatus,
  Language,
  ModelDiscoveryResult,
  Settings,
} from './types.js';
import { createSettings, PROVIDER_DEFAULTS } from './types.js';

export interface DuplexClientOptions {
  settings?: Partial<Settings>;
  initialMessages?: ChatMessage[];
  now?: () => number;
  /**
   * Optional indirection for provider calls. The browser uses this to reach a
   * local bridge that holds the credential, so a key stored in an owner-only
   * file can serve a page that is never given that key.
   *
   * It can also be attached after construction, because a surface has to find
   * the bridge first. Reading it eagerly meant the browser always started
   * without one and quietly fell back to answering for itself.
   */
  transport?: DuplexTransport;
  /**
   * Where the next question goes, read at the moment it is asked.
   *
   * A callback rather than a value because the destination is a decision a reader
   * changes while looking at the transcript, and a value captured here would be
   * the one that was current when the client was built.
   *
   * It exists so the surface's own turn is written into the conversation it was
   * sent into. A transcript that draws one conversation per person cannot file
   * an unlabelled question anywhere, so a reader who asks a question and watches
   * it disappear would be right to conclude the question was never sent — and it
   * is only gone for the moment between pressing Enter and the agent's row
   * arriving, which is exactly how long it takes to notice.
   */
  destination?: () => { channel?: string; person?: string };
}

/**
 * The ids a store of record gave the two halves of a turn, once it has them.
 *
 * The surface writes its own turn optimistically — the question the instant Enter
 * is pressed, the answer the moment it comes back — and a store that keeps the
 * record of the conversation writes the same turn again under ids of its own. A
 * provider hands back nothing, so both halves keep the ids minted here; the agent
 * mints a real row id for each, and this is how those reach the transcript.
 *
 * It exists because the merge between the surface's turn and the agent's row is a
 * union *by id* (`bindAgentConversation`), and two descriptions of one turn under
 * two different ids are not a merge at all: the question is drawn twice, the
 * answer is drawn twice, and a conversation of one exchange reads as four
 * messages the agent sent without being asked. Carrying the ids across is what
 * makes the union collapse them, which is the whole point of unioning.
 */
export interface TurnAssignment {
  /** The id the store gave the question, replacing the optimistic one. */
  questionId?: string;
  /** The id the store gave the answer, replacing the optimistic one. */
  answerId?: string;
}

export interface DuplexTransport {
  /** True when the transport can sign a request without a browser-held key. */
  readonly authenticated: boolean;
  complete(
    prompt: string,
    history: CompletionMessage[],
    signal: AbortSignal,
    onDelta: (text: string) => void,
    onAssigned?: (assignment: TurnAssignment) => void,
  ): Promise<string>;
  discoverModels(signal?: AbortSignal): Promise<ModelDiscoveryResult>;
}

type Listener = (event: ClientEvent) => void;

let clientId = 0;

function nextId(prefix: string): string {
  clientId += 1;
  return `${prefix}-${Date.now().toString(36)}-${clientId.toString(36)}`;
}

/** A snapshot handed to a listener is its own copy, so a surface cannot write state. */
function copySnapshot(snapshot: ClientSnapshot): ClientSnapshot {
  return {
    ...snapshot,
    settings: { ...snapshot.settings },
    messages: snapshot.messages.map((message) => ({ ...message })),
  };
}

/**
 * Whether a reply can come from a real provider rather than being refused.
 *
 * The transport may hold a credential the settings never see — that is the
 * whole point of the browser bridge — so a transport that can sign a request
 * outranks whatever the settings happen to carry.
 */
function providerIsUsable(settings: Settings, transport: DuplexTransport | null): boolean {
  if (!settings.model || !settings.baseUrl) return false;
  if (transport?.authenticated) return true;
  return !providerFor(settings.provider).requiresApiKey || settings.apiKey.trim().length > 0;
}

/**
 * The machine-readable reason a request failed, or `''` when it did not fail
 * with a recognisable error. Kept out of the user-facing text on purpose: the
 * snapshot carries a translated sentence, while this is the raw code for logs
 * and the debug pane.
 */
function failureCode(error: unknown): string {
  if (error instanceof ProviderRequestError) return error.toString();
  if (error instanceof Error) return error.message.trim();
  return '';
}

/** Why one failure is not the next, in the words of the provider that refused. */
function providerErrorKey(error: ProviderRequestError): TranslationKey {
  switch (error.code) {
    case 'missing_credentials':
      return 'providerNoKey';
    case 'invalid_endpoint':
      return 'providerBadEndpoint';
    case 'missing_prompt':
      return 'providerNoPrompt';
    case 'prompt_too_large':
      return 'providerPromptTooLarge';
    case 'network_unavailable':
      return 'providerUnavailable';
    case 'timeout':
      return 'providerTimeout';
    case 'redirect_refused':
      return 'providerRedirectRefused';
    case 'response_too_large':
      return 'providerResponseTooLarge';
    case 'no_text_in_response':
      return error.detail ? 'providerNoText' : 'providerEmptyReply';
    case 'aborted':
      return 'providerInterrupted';
    default:
      return 'providerRefused';
  }
}

/**
 * A failure, in one sentence, keeping whatever the provider said about it.
 *
 * A translated reason alone sends people hunting; a raw status alone is not
 * readable in three languages. The two together say both what happened here
 * and what the provider objected to.
 */
export function describeFailure(error: unknown, language: Language): string {
  const t = createTranslator(language);
  // An agent's refusals are its own vocabulary, and it knows which of several
  // different things went wrong; asking a provider sentence to describe an agent
  // that has been stopped would turn "the agent is stopped" into "the endpoint
  // is unreachable", which sends the reader to fix the wrong machine.
  const agentReason = describeAgentFailure(error, language);
  if (agentReason) return agentReason;
  if (error instanceof ProviderRequestError) {
    if (error.code === 'http' && error.status) {
      const reason = t('providerHttp', { status: error.status });
      return error.detail ? `${reason} · ${error.detail}` : reason;
    }
    const reason = t(providerErrorKey(error));
    // A detail that only restates the reason is not worth printing twice; the
    // sentence already says it.
    if (!error.detail || error.detail === reason) return reason;
    return `${reason} · ${error.detail}`;
  }
  if (error instanceof Error && /interrupted|abort/i.test(error.message)) return t('providerInterrupted');
  return t('providerUnavailable');
}

/**
 * The shared client. One immutable snapshot is the whole state; every change
 * goes through `update`, which swaps the snapshot and fans out a `snapshot`
 * event, so a surface can never observe a half-applied transition.
 *
 * Staleness is decided by one counter, `operation`. Anything that makes an
 * in-flight reply meaningless — a new message, an explicit interrupt, a
 * disconnect, disposal — increments it, and a reply that comes back afterwards
 * is dropped instead of being written into the transcript.
 *
 * The client never writes a message the provider did not write. A turn that
 * fails ends with the person's question in the transcript and a reason on the
 * snapshot; there is no local stand-in, because a fluent sentence that looks
 * like a reply but is not one is worse than a visible failure.
 */
export class DuplexClient {
  private readonly listeners = new Set<Listener>();
  private readonly now: () => number;
  private readonly abortControllers = new Set<AbortController>();
  private operation = 0;
  private snapshotState: ClientSnapshot;
  private transportRef: DuplexTransport | null;
  private readonly destination: () => { channel?: string; person?: string };
  private disposed = false;

  constructor(options: DuplexClientOptions = {}) {
    this.transportRef = options.transport ?? null;
    this.now = options.now ?? Date.now;
    this.destination = options.destination ?? (() => ({}));
    this.snapshotState = {
      status: 'offline',
      connected: false,
      settings: createSettings(options.settings),
      messages: normalizeMessages(options.initialMessages),
      lastActivityAt: null,
      pending: false,
      streamingText: '',
      lastError: null,
    };
  }

  getSnapshot(): ClientSnapshot {
    return copySnapshot(this.snapshotState);
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  // ------------------------------------------------------------- connection

  /**
   * Attaches or replaces the transport.
   *
   * A reply already in flight keeps the transport it began with, so attaching
   * one mid-turn cannot change the provider halfway through an answer.
   */
  setTransport(transport: DuplexTransport | null): void {
    this.transportRef = transport;
  }

  connect(): void {
    if (this.disposed || this.snapshotState.connected) return;
    this.setStatus('connecting');
    this.update((snapshot) => ({ ...snapshot, connected: true, status: 'online', lastError: null }));
  }

  disconnect(): void {
    this.cancelPending();
    this.update((snapshot) => ({
      ...snapshot,
      connected: false,
      status: 'offline',
      pending: false,
      streamingText: '',
    }));
  }

  dispose(): void {
    this.disconnect();
    this.disposed = true;
    this.listeners.clear();
  }

  // ---------------------------------------------------------------- settings

  setSettings(settings: Settings): void {
    const candidate = createSettings(settings);
    const current = this.snapshotState.settings;
    const defaults = PROVIDER_DEFAULTS[candidate.provider];
    // A provider change is a change of identity, not a tweak: the endpoint, the
    // model and the credential belong to the provider that was just left behind,
    // so any of them the caller did not touch moves to the new provider's
    // defaults. A credential is never carried across, because that would post
    // one provider's key to another provider's endpoint.
    const safeSettings = candidate.provider === current.provider
      ? candidate
      : createSettings({
        ...candidate,
        apiKey: candidate.apiKey === current.apiKey ? '' : candidate.apiKey,
        baseUrl: candidate.baseUrl === current.baseUrl ? defaults.baseUrl : candidate.baseUrl,
        model: candidate.model === current.model ? defaults.model : candidate.model,
      });
    this.update((snapshot) => ({ ...snapshot, settings: safeSettings }));
    this.emit({ type: 'settings', settings: { ...safeSettings } });
  }

  updateSettings(patch: Partial<Settings>): void {
    this.setSettings(createSettings({ ...this.snapshotState.settings, ...patch }));
  }

  // ------------------------------------------------------------ conversation

  clearConversation(): void {
    this.update((snapshot) => ({ ...snapshot, messages: [], lastActivityAt: null, streamingText: '', lastError: null }));
  }

  /**
   * Replaces the transcript with one authored elsewhere, so a second surface
   * (the browser, or another phone process) can hand over a shared history.
   */
  adoptHistory(messages: ChatMessage[]): void {
    const next = normalizeMessages(messages).slice(-100);
    this.update((snapshot) => ({
      ...snapshot,
      messages: next,
      lastActivityAt: next.at(-1)?.createdAt ?? null,
      streamingText: '',
    }));
  }

  /**
   * The request lifecycle, in the order a reader should expect it:
   *
   *   connect → supersede anything in flight → record the question → ask →
   *   stream the reply in → append it → back to idle.
   *
   * The step that supersedes in-flight work is what makes a rapid second
   * message replace the first rather than race it: the abandoned reply finds
   * `operation` moved on and is dropped instead of being written to the
   * transcript.
   */
  async sendMessage(text: string): Promise<ChatMessage | undefined> {
    const content = sanitizeStoredText(text, MAX_PROMPT_LENGTH).trim();
    if (!content || this.disposed) return undefined;
    this.connect();
    this.interrupt();
    const createdAt = this.timestamp();
    const questionId = nextId('message');
    // Labelled with where it is going, not merely that it is going: a transcript
    // that draws one conversation per person has to be able to put this turn in
    // one of them, and an unlabelled question belongs to none.
    const { channel, person } = this.destination();
    this.appendMessage({
      id: questionId,
      role: 'user',
      text: content,
      createdAt,
      ...(channel ? { channel } : {}),
      ...(person ? { person } : {}),
    });
    this.update((snapshot) => ({
      ...snapshot,
      status: 'thinking',
      pending: true,
      lastActivityAt: createdAt,
      lastError: null,
      streamingText: '',
    }));

    const currentOperation = ++this.operation;
    const controller = new AbortController();
    this.abortControllers.add(controller);
    const onDelta = (increment: string) => {
      if (this.disposed || currentOperation !== this.operation) return;
      this.update((snapshot) => ({ ...snapshot, streamingText: `${snapshot.streamingText}${increment}` }));
      this.emit({ type: 'delta', text: increment });
    };
    // Whatever the store of record calls this turn, learned as it is learned.
    //
    // Deliberately not gated on the turn still being current. A turn the reader
    // supersedes keeps its question in the transcript — the question is history,
    // only the answer is abandoned — and the store's row for that question is
    // already there, so the id is exactly what that abandoned question still
    // needs. Refusing to hand it over because the turn moved on left every
    // superseded question on screen twice, for good. `relabelMessage` and
    // `dropMessage` are the real guards: both no-op on a message that is not in
    // the transcript, so reconciling a turn that was cleared cannot resurrect it.
    let answerId: string | undefined;
    const onAssigned = (assigned: TurnAssignment) => {
      if (this.disposed) return;
      if (assigned.questionId) this.relabelMessage(questionId, assigned.questionId);
      if (assigned.answerId) answerId = assigned.answerId;
    };
    try {
      const answer = await this.askProvider(
        content, currentOperation, controller.signal, onDelta, onAssigned,
      );
      return this.settleTurn(answer, currentOperation, answerId);
    } catch (error) {
      return this.failTurn(error, currentOperation);
    } finally {
      this.abortControllers.delete(controller);
    }
  }

  /**
   * Stops a reply in flight and returns the line to where it was.
   *
   * The status goes back to `online` rather than resting at `interrupted`,
   * because an interruption is something that happened, not a condition the
   * line is left in.
   */
  interrupt(): void {
    this.cancelPending();
    this.update((snapshot) => ({
      ...snapshot,
      pending: false,
      streamingText: '',
      status: snapshot.connected ? 'online' : 'offline',
    }));
  }

  async discoverModels(signal?: AbortSignal, candidate?: Settings): Promise<ModelDiscoveryResult> {
    // A draft the caller is holding beats the adopted settings. A form probing a
    // provider somebody has just chosen is asking about that one, and answering
    // from the last saved settings is a catalogue for a model nobody picked.
    const draft = candidate ?? this.snapshotState.settings;
    // With no draft there is nothing to pass on, so the question goes to the
    // agent, which is the side holding the base model's key. With no link there is
    // nobody to ask, and a list invented here would be a guess presented as a
    // catalogue.
    if (!candidate && this.snapshotState.settings.interface === 'agent') {
      if (this.transportRef) return this.transportRef.discoverModels(signal);
      return { models: [], source: 'offline', error: 'agent_offline' };
    }
    if (!candidate && this.transportRef) return this.transportRef.discoverModels(signal);
    if (this.snapshotState.settings.interface === 'agent') {
      // In agent mode this client holds no credential, and a page served by the
      // agent is not permitted to reach a provider itself, so there is nothing to
      // try: the fallback list for the provider that was asked about, and the
      // reason. Falling through to the fetch below would report the provider as
      // unreachable when it is the service that is not there.
      return {
        models: providerFor(draft.provider).staticModels,
        source: 'offline',
        error: 'agent_unreachable',
      };
    }
    return discoverProviderModels(createSettings(draft), fetch, signal);
  }

  /** True when replies can come from a real provider rather than being refused. */
  isProviderBacked(): boolean {
    return this.hasUsableProvider();
  }

  /**
   * Why the current settings cannot reach anything, or `''` when they can.
   *
   * In `agent` mode the answer is about the link and not about a credential:
   * there is no key to supply, so saying "no key" would describe a field that is
   * not on this screen.
   */
  unusableReason(): string {
    const settings = this.snapshotState.settings;
    if (settings.interface === 'agent') {
      return this.hasUsableProvider()
        ? ''
        : createTranslator(settings.language)('agentOffline');
    }
    if (!settings.model) return createTranslator(settings.language)('providerNoModel');
    if (!settings.baseUrl) return createTranslator(settings.language)('providerNoEndpoint');
    if (this.hasUsableProvider()) return '';
    return createTranslator(settings.language)('providerNoKey');
  }

  // ------------------------------------------------------------------ private

  /**
   * Settles one turn, if it is still the current one.
   *
   * A turn superseded while its request was in flight resolves to `undefined`
   * and writes nothing at all.
   *
   * `answerId` is the id the store of record gave this answer, and it is used in
   * preference to a freshly minted one for the reason the union is by id: a
   * surface's own answer and the agent's row for it are one message, and minting
   * a second id for text the store has already named is what puts it on screen
   * twice. A transport that has no id of its own to offer (a model provider)
   * leaves it unset and the local id stands.
   */
  private settleTurn(response: string, operation: number, answerId?: string): ChatMessage | undefined {
    if (this.disposed || operation !== this.operation) return undefined;
    const answer = response.trim();
    if (!answer) {
      this.update((snapshot) => ({ ...snapshot, pending: false, streamingText: '', status: 'online' }));
      return undefined;
    }
    const assistantMessage: ChatMessage = {
      id: answerId || nextId('message'),
      role: 'assistant',
      text: answer,
      createdAt: this.timestamp(),
    };
    this.appendMessage(assistantMessage);
    this.update((snapshot) => ({
      ...snapshot,
      pending: false,
      streamingText: '',
      status: 'online',
      lastActivityAt: assistantMessage.createdAt,
      lastError: null,
    }));
    return assistantMessage;
  }

  /**
   * Records why a turn produced no answer, and announces it.
   *
   * There is deliberately no local fallback. The previous version answered from
   * a table of canned sentences whenever a provider failed, so a wrong or
   * expired key produced confident nonsense in the transcript —
   * indistinguishable from a real reply, and persisted to the shared file for
   * the other interface to read back. The honest failure is the actionable one.
   */
  private failTurn(error: unknown, operation: number): undefined {
    if (this.disposed || operation !== this.operation) return undefined;
    const connected = this.snapshotState.connected;
    this.update((snapshot) => ({
      ...snapshot,
      pending: false,
      streamingText: '',
      status: connected ? 'online' : 'offline',
      lastError: describeFailure(error, snapshot.settings.language),
    }));
    this.emit({ type: 'error', message: failureCode(error) });
    return undefined;
  }

  private async askProvider(
    prompt: string,
    operation: number,
    signal: AbortSignal,
    onDelta: (text: string) => void,
    onAssigned?: (assignment: TurnAssignment) => void,
  ): Promise<string> {
    if (!this.hasUsableProvider()) {
      throw new ProviderRequestError('missing_credentials', this.unusableReason());
    }
    // Everything already said, minus the question this reply answers.
    const history: CompletionMessage[] = this.snapshotState.messages.slice(0, -1).map((message) => ({
      role: message.role,
      content: message.text,
    }));
    const transport = this.transportRef;
    if (this.snapshotState.settings.interface === 'agent' && !transport) {
      throw new ProviderRequestError('missing_credentials', this.unusableReason());
    }
    const answer = transport
      ? await transport.complete(prompt, history, signal, onDelta, onAssigned)
      : await requestProviderCompletion(this.snapshotState.settings, prompt, fetch, signal, history, { onDelta });
    if (operation !== this.operation) throw new ProviderRequestError('aborted');
    return answer;
  }

  private hasUsableProvider(): boolean {
    return providerIsUsable(this.snapshotState.settings, this.transportRef);
  }

  /**
   * Aborts every request in flight and marks them all stale.
   *
   * Bumping the counter here is what stops an aborted request from being
   * mistaken for a failed one: a disconnect or a disposal used to leave the
   * turn current, so the rejection that followed the abort was answered with a
   * stand-in reply — written into the transcript, and from there into the
   * shared file — for a question nobody ever received an answer to.
   */
  private cancelPending(): void {
    this.operation += 1;
    for (const controller of this.abortControllers) controller.abort();
    this.abortControllers.clear();
  }

  private timestamp(): number {
    const value = this.now();
    return isSafeTimestamp(value) ? value : Date.now();
  }

  /**
   * Adds a message, unless the transcript already holds one under that id.
   *
   * Idempotent on purpose. An id is the key the surface's transcript and the
   * agent's store are unioned by, and a message can arrive here twice: once as the
   * surface's own optimistic copy of a turn and again as the row the store wrote
   * for that same turn, which the binding has already merged in by the time the
   * turn settles. Appending blindly put the same reply on screen under the same id
   * twice — two bubbles, one of which read as the agent repeating itself — and no
   * later union could undo it, because the union is what produced the copy.
   *
   * The one already held wins. It is the store's row, and it carries the delivery
   * status and the timestamp the store chose, which is the whole reason the agent
   * side of the merge is meant to win.
   */
  private appendMessage(message: ChatMessage): void {
    const safeMessage = normalizeMessages([message])[0];
    if (!safeMessage) return;
    if (this.snapshotState.messages.some((held) => held.id === safeMessage.id)) return;
    this.update((snapshot) => ({ ...snapshot, messages: [...snapshot.messages, safeMessage].slice(-100) }));
    this.emit({ type: 'message', message: safeMessage });
  }

  /**
   * Reconciles the surface's own copy of a turn with the id the store gave it.
   *
   * The store's transcript is merged into this one by id, so an optimistic copy
   * under a locally minted id is invisible to that union: it cannot replace the
   * store's row and the store's row cannot replace it, and the reader sees one
   * exchange as four messages. This hands the store's id to the local copy, which
   * is the only thing that makes the union collapse the pair.
   *
   * Which way it goes depends on which copy is already here. If the store's row has
   * not arrived yet the local copy is renamed to that id, in place, so the question
   * keeps the position and the timestamp of the moment it was asked. If the row
   * *has* arrived — the binding merged it in while the turn was still in flight —
   * the row wins and the local copy is dropped rather than renamed onto it, because
   * two messages cannot share a key and keeping both is the duplicate this exists to
   * remove.
   */
  private relabelMessage(from: string, to: string): void {
    if (!from || !to || from === to) return;
    if (this.snapshotState.messages.some((message) => message.id === to)) {
      this.dropMessage(from);
      return;
    }
    let renamed = false;
    this.update((snapshot) => {
      if (!snapshot.messages.some((message) => message.id === from)) return snapshot;
      renamed = true;
      return {
        ...snapshot,
        messages: snapshot.messages.map((message) => (message.id === from ? { ...message, id: to } : message)),
      };
    });
    const message = this.findMessage(to);
    if (renamed && message) this.emit({ type: 'message', message });
  }

  private dropMessage(id: string): void {
    if (!this.snapshotState.messages.some((message) => message.id === id)) return;
    this.update((snapshot) => ({
      ...snapshot,
      messages: snapshot.messages.filter((message) => message.id !== id),
    }));
  }

  private findMessage(id: string): ChatMessage | undefined {
    return this.snapshotState.messages.find((message) => message.id === id);
  }

  private setStatus(status: ConnectionStatus): void {
    this.update((snapshot) => ({ ...snapshot, status }));
    this.emit({ type: 'status', status });
  }

  private update(updater: (snapshot: ClientSnapshot) => ClientSnapshot): void {
    this.snapshotState = updater(this.snapshotState);
    this.emit({ type: 'snapshot', snapshot: this.getSnapshot() });
  }

  private emit(event: ClientEvent): void {
    for (const listener of this.listeners) listener(event);
  }
}
