import {
  MAX_MODEL_COUNT,
  MAX_MODEL_LENGTH,
  MAX_PROMPT_LENGTH,
  MAX_RESPONSE_BYTES,
  MAX_RESPONSE_TEXT_LENGTH,
  isSafeModelName,
  isSafeStoredText,
  isValidProviderEndpoint,
  sanitizeStoredText,
} from './security.js';
import { createSettings, type ModelDiscoveryResult, type ProviderId, type Settings } from './types.js';
import { normalizeBaseUrl, providerFor } from './providers.js';

/**
 * The one place that knows how a provider is spoken to.
 *
 * Three rules hold everywhere in this file, and they are what the previous
 * version got wrong:
 *
 *  - **Nothing here invents content.** A provider that refuses is reported as
 *    a refusal carrying the provider's own words. A reply is either something
 *    the model wrote or it does not exist.
 *  - **Nothing here decides how long a model may think.** A wall-clock cap on a
 *    whole completion aborts reasoning models and local inference routinely, so
 *    the budget is a *stall* budget: how long we wait for the first byte, and
 *    how long we tolerate silence afterwards.
 *  - **Nothing here gets in the way of a working provider.** A system message
 *    is placed where that provider wants it, a redirect it issues is followed
 *    when it stays on the same origin, and a server that cannot stream is read
 *    as one ordinary JSON body rather than refused.
 */

const FIRST_BYTE_TIMEOUT_MS = 60_000;
const IDLE_TIMEOUT_MS = 150_000;
const CATALOGUE_TIMEOUT_MS = 20_000;
const MAX_REDIRECTS = 3;
const MAX_HISTORY_MESSAGES = 32;

/** Anthropic requires this on every request, so it is a constant and not a setting. */
const ANTHROPIC_VERSION = '2023-06-01';

/**
 * A ceiling the provider needs but the caller does not. Anthropic rejects a
 * request without one; asking for 512 tokens truncated ordinary answers
 * mid-sentence, which is the program's decision to make about the model's
 * output rather than the model's own.
 */
const ANTHROPIC_MAX_TOKENS = 4096;

export type ProviderErrorCode =
  | 'missing_credentials'
  | 'invalid_endpoint'
  | 'missing_prompt'
  | 'prompt_too_large'
  | 'network_unavailable'
  | 'timeout'
  | 'redirect_refused'
  | 'response_too_large'
  | 'no_text_in_response'
  | 'aborted'
  | 'http';

export class ProviderRequestError extends Error {
  readonly code: ProviderErrorCode;
  readonly status?: number;
  /** The provider's own explanation, bounded and sanitised. Never invented. */
  readonly detail: string;

  constructor(code: ProviderErrorCode, detail = '', status?: number) {
    super(code);
    this.name = 'ProviderRequestError';
    this.code = code;
    this.detail = detail;
    this.status = status;
  }

  /** `provider_http_401: Incorrect API key provided` — for logs and the debug pane. */
  toString(): string {
    return this.status ? `${this.code}_${this.status}${this.detail ? `: ${this.detail}` : ''}` : `${this.code}${this.detail ? `: ${this.detail}` : ''}`;
  }
}

type Fetcher = (input: string | URL, init?: RequestInit) => Promise<Response>;

type CompletionRole = 'user' | 'assistant' | 'system';

export interface CompletionMessage {
  role: CompletionRole;
  content: string;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function extractModels(payload: unknown, provider: ProviderId): string[] {
  const record = asRecord(payload);
  if (!record) return [];
  const items = provider === 'gemini'
    ? (Array.isArray(record.models) ? record.models : [])
    : (Array.isArray(record.data) ? record.data : []);
  return items
    .slice(0, MAX_MODEL_COUNT)
    .map((item) => {
      const candidate = asRecord(item);
      const value = provider === 'gemini' ? candidate?.name : candidate?.id;
      if (typeof value !== 'string') return '';
      const name = provider === 'gemini' ? value.replace(/^models\//, '') : value;
      const cleaned = sanitizeStoredText(name, MAX_MODEL_LENGTH);
      return isSafeModelName(cleaned) ? cleaned : '';
    })
    .filter(Boolean);
}

export function isMissingKey(settings: Settings): boolean {
  return providerFor(settings.provider).requiresApiKey && settings.apiKey.trim().length === 0;
}

// --------------------------------------------------------------------- request

/**
 * A live request whose budget is re-armed by progress.
 *
 * The important property is that the timer is *reset* whenever a chunk arrives,
 * so a slow model is never punished for being slow — only for going silent.
 */
class LiveRequest {
  private readonly controller = new AbortController();
  private readonly timers = new Set<ReturnType<typeof setTimeout>>();
  private readonly relayAbort: () => void;
  private readonly finishListeners: Array<() => void> = [];
  private settled = false;
  /** True when a guard gave up, as opposed to the caller aborting. */
  private stalled = false;

  constructor(signal?: AbortSignal) {
    this.relayAbort = () => this.controller.abort();
    if (signal) {
      if (signal.aborted) this.controller.abort();
      else signal.addEventListener('abort', this.relayAbort, { once: true });
    }
  }

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  get aborted(): boolean {
    return this.controller.signal.aborted;
  }

  get didStall(): boolean {
    return this.stalled;
  }

  /** Arms a stall timer. Returns a function that cancels just that timer. */
  guard(milliseconds: number, onStall: () => void): () => void {
    if (this.controller.signal.aborted) return () => undefined;
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      this.stalled = true;
      onStall();
      this.controller.abort();
    }, milliseconds);
    (timer as unknown as { unref?: () => void }).unref?.();
    this.timers.add(timer);
    return () => {
      clearTimeout(timer);
      this.timers.delete(timer);
    };
  }

  onFinish(listener: () => void): void {
    this.finishListeners.push(listener);
  }

  /**
   * Runs when the request is torn down.
   *
   * A body being read by hand is not attached to the abort signal the way a
   * `fetch` response is, so an abandoned stream has to be cancelled explicitly.
   * Without that the read stays pending and the connection stays open: an
   * interrupted reply kept the socket busy for as long as the provider felt like
   * writing.
   */
  onAbort(listener: () => void): void {
    if (this.controller.signal.aborted) {
      listener();
      return;
    }
    this.controller.signal.addEventListener('abort', listener, { once: true });
  }

  settle(): void {
    if (this.settled) return;
    this.settled = true;
    for (const timer of this.timers) clearTimeout(timer);
    this.timers.clear();
    for (const listener of this.finishListeners) listener();
    this.finishListeners.length = 0;
  }
}

function isAbortError(error: unknown): boolean {
  if (error instanceof ProviderRequestError) return error.code === 'aborted';
  return error instanceof Error && (error.name === 'AbortError' || /abort/i.test(error.message));
}

/**
 * Why a request ended, in one place so every exit agrees.
 *
 * A stall is a timeout and an abort is an interruption: they are different
 * events with different causes, and collapsing them made an interrupted reply
 * look like a provider that stopped answering.
 */
function endCode(request: LiveRequest, external?: AbortSignal): 'aborted' | 'timeout' {
  if (request.didStall) return 'timeout';
  return external?.aborted || request.aborted ? 'aborted' : 'timeout';
}

function sameOrigin(left: string, right: string): boolean {
  try {
    const a = new URL(left);
    const b = new URL(right);
    return a.protocol === b.protocol && a.host === b.host;
  } catch {
    return false;
  }
}

/**
 * Issues one request, following the redirects a provider is entitled to issue.
 *
 * `redirect: 'error'` treated every redirect as an attack, which broke the
 * providers that legitimately use one. So the redirect is followed by hand and
 * only while it stays on the origin we already decided to trust: a cross-origin
 * hop is refused rather than followed, which is what keeps a custom
 * `x-api-key` from ever being re-sent to a host the user never named.
 */
async function send(
  fetcher: Fetcher,
  url: string,
  init: RequestInit,
  request: LiveRequest,
): Promise<Response> {
  let target = url;
  for (let hop = 0; hop <= MAX_REDIRECTS; hop += 1) {
    if (request.aborted) throw new ProviderRequestError('aborted');
    const release = request.guard(FIRST_BYTE_TIMEOUT_MS, () => undefined);
    let response: Response;
    try {
      response = await fetcher(target, {
        ...init,
        cache: 'no-store',
        credentials: 'omit',
        redirect: 'manual',
        referrerPolicy: 'no-referrer',
        signal: request.signal,
      });
    } finally {
      release();
    }
    const location = response.status >= 300 && response.status < 400
      ? response.headers?.get('location')
      : null;
    if (!location) return response;
    await cancelResponse(response);
    let next: string;
    try {
      next = new URL(location, target).toString();
    } catch {
      throw new ProviderRequestError('redirect_refused', 'the provider sent an unreadable redirect');
    }
    if (!sameOrigin(next, url)) {
      throw new ProviderRequestError('redirect_refused', `refused a redirect to ${safeOrigin(next)}`);
    }
    target = next;
  }
  throw new ProviderRequestError('redirect_refused', 'too many redirects');
}

function safeOrigin(value: string): string {
  try {
    return new URL(value).host;
  } catch {
    return 'an unknown host';
  }
}

// ----------------------------------------------------------------------- body

/**
 * Reads a whole response, refusing one that outgrows its budget.
 *
 * The byte ceiling is checked as the bytes arrive rather than afterwards, so a
 * runaway body is abandoned instead of being buffered in full first.
 */
async function readAll(response: Response, request: LiveRequest, idleMs: number): Promise<string> {
  const declared = response.headers?.get('content-length');
  if (declared) {
    const length = Number(declared);
    if (Number.isFinite(length) && length > MAX_RESPONSE_BYTES) {
      await cancelResponse(response);
      throw new ProviderRequestError('response_too_large');
    }
  }
  const body = response.body;
  if (body && typeof body.getReader === 'function') {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    request.onAbort(() => {
      void reader.cancel().catch(() => undefined);
    });
    const release = request.guard(idleMs, () => undefined);
    let total = 0;
    const chunks: string[] = [];
    try {
      while (true) {
        const result = await reader.read();
        if (result.done) break;
        total += result.value.byteLength;
        if (total > MAX_RESPONSE_BYTES) {
          await reader.cancel();
          throw new ProviderRequestError('response_too_large');
        }
        chunks.push(decoder.decode(result.value, { stream: true }));
      }
      chunks.push(decoder.decode());
      return chunks.join('');
    } finally {
      release();
      reader.releaseLock();
    }
  }
  const text = await response.text();
  if (text.length > MAX_RESPONSE_BYTES) throw new ProviderRequestError('response_too_large');
  return text;
}

async function cancelResponse(response: Response): Promise<void> {
  try {
    await response.body?.cancel();
  } catch {
    // The socket is already gone; nothing left to do about it.
  }
}

async function readJson(response: Response, request: LiveRequest, idleMs: number): Promise<unknown> {
  const raw = await readAll(response, request, idleMs);
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    throw new ProviderRequestError('no_text_in_response', 'the provider sent something that is not JSON');
  }
}

/**
 * What the provider said went wrong, in its own words.
 *
 * Every major vendor puts the useful sentence in `error.message`; keeping it is
 * the difference between "something went wrong" and "Incorrect API key
 * provided", which is the only thing that tells a person what to do next.
 */
function detailFromPayload(payload: unknown): string {
  const record = asRecord(payload);
  if (!record) return '';
  const direct = asRecord(record.error);
  const message = direct
    ? (typeof direct.message === 'string' ? direct.message : '')
    : (typeof record.message === 'string' ? record.message : '');
  return sanitizeDetail(message);
}

function sanitizeDetail(value: string): string {
  return sanitizeStoredText(value, 400).replace(/\s+/g, ' ').trim();
}

/** Turns a non-2xx response into the error the caller will show. */
async function httpError(response: Response, request: LiveRequest): Promise<ProviderRequestError> {
  const status = response.status;
  let detail = '';
  try {
    detail = detailFromPayload(await readJson(response, request, IDLE_TIMEOUT_MS));
  } catch {
    // A body that is missing, truncated or not JSON still leaves the status,
    // which is the part worth reporting.
    await cancelResponse(response);
  }
  return new ProviderRequestError('http', detail, status);
}

// -------------------------------------------------------------------- headers

function authHeaders(settings: Settings): Record<string, string> {
  const headers: Record<string, string> = { Accept: 'application/json' };
  const provider = providerFor(settings.provider);
  if (!provider.requiresApiKey) return headers;
  if (settings.provider === 'anthropic') {
    headers['x-api-key'] = settings.apiKey;
    headers['anthropic-version'] = ANTHROPIC_VERSION;
  } else if (settings.provider === 'gemini') {
    headers['x-goog-api-key'] = settings.apiKey;
  } else {
    headers.Authorization = `Bearer ${settings.apiKey}`;
  }
  return headers;
}

// -------------------------------------------------------------------- history

/**
 * The conversation as a provider will be given it, in order.
 *
 * Every provider takes turns, so the history is rebuilt here rather than by
 * each caller. Nothing is dropped: a system turn stays a system turn, because
 * whether it belongs *inside* the turn list is a property of the API, not of
 * the conversation. Anthropic and Gemini want it lifted out — handing it to
 * them inline is a 400 rather than a sentence — while the OpenAI-compatible
 * shape wants it exactly where it was written.
 */
function prepareHistory(history: readonly CompletionMessage[]): CompletionMessage[] {
  const turns: CompletionMessage[] = [];
  for (const message of history.slice(-MAX_HISTORY_MESSAGES)) {
    const content = sanitizeStoredText(message.content, MAX_PROMPT_LENGTH);
    if (!content) continue;
    turns.push({ role: message.role, content });
  }
  return turns;
}

/**
 * Lifts the system turns out of a list, and returns the rest.
 *
 * A turn list that opens on the assistant's own reply is rejected by most
 * providers, so it is trimmed from the front rather than sent and refused.
 */
function liftSystem(turns: CompletionMessage[]): { system: string; rest: CompletionMessage[] } {
  const system: string[] = [];
  const rest: CompletionMessage[] = [];
  for (const turn of turns) {
    if (turn.role === 'system') system.push(turn.content);
    else rest.push({ role: turn.role === 'assistant' ? 'assistant' : 'user', content: turn.content });
  }
  while (rest.length > 0 && rest[0]?.role === 'assistant') rest.shift();
  return { system: system.join('\n\n'), rest };
}

interface BuiltBody {
  body: Record<string, unknown>;
  /** The path segment for a provider that names the model in the URL. */
  model: string;
}

function completionEndpoint(settings: Settings, model: string, stream: boolean): string {
  const baseUrl = normalizeBaseUrl(settings.baseUrl || providerFor(settings.provider).defaultBaseUrl);
  if (settings.provider === 'anthropic') return `${baseUrl}/messages`;
  if (settings.provider === 'gemini') {
    const method = stream ? 'streamGenerateContent?alt=sse' : 'generateContent';
    return `${baseUrl}/models/${encodeURIComponent(model)}:${method}`;
  }
  return `${baseUrl}/chat/completions`;
}

function completionBody(
  settings: Settings,
  prompt: string,
  history: readonly CompletionMessage[],
  stream: boolean,
): BuiltBody {
  const turns = prepareHistory(history);
  const model = settings.model || providerFor(settings.provider).defaultModel;

  if (settings.provider === 'anthropic') {
    const { system, rest } = liftSystem(turns);
    return {
      model,
      body: {
        model,
        ...(system ? { system } : {}),
        max_tokens: ANTHROPIC_MAX_TOKENS,
        messages: [...rest, { role: 'user', content: prompt }],
        ...(stream ? { stream: true } : {}),
      },
    };
  }

  if (settings.provider === 'gemini') {
    const { system, rest } = liftSystem(turns);
    return {
      model,
      body: {
        ...(system ? { systemInstruction: { parts: [{ text: system }] } } : {}),
        contents: [...rest, { role: 'user' as const, content: prompt }].map((turn) => ({
          role: turn.role === 'assistant' ? 'model' : 'user',
          parts: [{ text: turn.content }],
        })),
      },
    };
  }

  // OpenAI-compatible: the system turn is a turn like any other.
  return {
    model,
    body: {
      model,
      messages: [...trimLeadingAssistant(turns), { role: 'user', content: prompt }],
      ...(stream ? { stream: true } : {}),
    },
  };
}

function trimLeadingAssistant(turns: CompletionMessage[]): CompletionMessage[] {
  let start = 0;
  while (start < turns.length && turns[start]?.role === 'assistant') start += 1;
  return turns.slice(start);
}

// -------------------------------------------------------------------- replies

function boundedReply(value: string): string {
  const cleaned = sanitizeStoredText(value, MAX_RESPONSE_TEXT_LENGTH);
  return isSafeStoredText(cleaned, MAX_RESPONSE_TEXT_LENGTH) ? cleaned : '';
}

/** The text of a whole, non-streamed response, whichever shape it arrived in. */
function textFromPayload(payload: unknown, provider: ProviderId): string {
  const record = asRecord(payload);
  if (!record) throw new ProviderRequestError('no_text_in_response', 'the provider returned an empty response');

  if (provider === 'anthropic') {
    const text = joinText(record.content, (item) => asRecord(item)?.text);
    const cleaned = boundedReply(text);
    if (cleaned) return cleaned;
  }
  if (provider === 'gemini') {
    const text = joinText(partsOf(record), (item) => asRecord(item)?.text);
    const cleaned = boundedReply(text);
    if (cleaned) return cleaned;
  }

  const choices = record.choices;
  if (Array.isArray(choices)) {
    const first = asRecord(choices[0]);
    const content = asRecord(first?.message)?.content ?? first?.text;
    if (typeof content === 'string') {
      const cleaned = boundedReply(content);
      if (cleaned) return cleaned;
    }
  }

  const fallback = joinText(record.content, (item) => asRecord(item)?.text);
  if (fallback) return boundedReply(fallback);
  throw new ProviderRequestError('no_text_in_response', describeEmptyChoice(record));
}

function partsOf(record: Record<string, unknown>): unknown {
  const candidates = record.candidates;
  if (!Array.isArray(candidates)) return [];
  return asRecord(asRecord(candidates[0])?.content)?.parts;
}

function joinText(value: unknown, read: (item: unknown) => unknown): string {
  if (!Array.isArray(value)) return '';
  return value
    .slice(0, 4_000)
    .map((item) => read(item))
    .filter((part): part is string => typeof part === 'string' && part.length > 0)
    .join('');
}

/**
 * Why a reply was empty, in the provider's vocabulary. A reasoning model that
 * ran out of budget and a refused request look identical from the outside, and
 * guessing between them is worse than reporting the reason the provider gave.
 */
function describeEmptyChoice(record: Record<string, unknown>): string {
  const choices = Array.isArray(record.choices) ? record.choices : [];
  const finish = asRecord(choices[0])?.finish_reason;
  if (typeof finish === 'string' && finish && finish !== 'stop') {
    return finish === 'length' ? 'the model reached its token limit' : `the model stopped early (${finish})`;
  }
  const feedback = asRecord(asRecord(choices[0])?.message)?.content;
  if (typeof feedback === 'string' && feedback.trim()) return feedback.trim().slice(0, 200);
  const promptFeedback = asRecord(record.promptFeedback)?.blockReason;
  if (typeof promptFeedback === 'string' && promptFeedback) {
    return `the request was blocked (${promptFeedback})`;
  }
  return 'the response did not contain any text';
}

// ------------------------------------------------------------------- streaming

/**
 * Reads the increment of one server-sent event.
 *
 * The three payload shapes differ, but the question asked of each is the same:
 * "did this frame carry any new text, and did it carry an error?" Keeping the
 * three shapes here, rather than in three parse paths, is what lets one stream
 * loop serve OpenAI-compatible, Anthropic and Gemini.
 */
function deltaFromFrame(
  data: string,
  provider: ProviderId,
): { text: string; done: boolean; error: string } {
  const trimmed = data.trim();
  if (trimmed === '' || trimmed === '[DONE]') {
    return trimmed === '[DONE]'
      ? { text: '', done: true, error: '' }
      : { text: '', done: false, error: '' };
  }

  let payload: unknown;
  try {
    payload = JSON.parse(trimmed) as unknown;
  } catch {
    return { text: '', done: false, error: '' };
  }
  const record = asRecord(payload);
  if (!record) return { text: '', done: false, error: '' };

  const failure = detailFromPayload(record);
  if (failure) return { text: '', done: true, error: failure };

  if (provider === 'anthropic') {
    const type = record.type;
    if (type === 'error') {
      return { text: '', done: true, error: detailFromPayload(record) || 'the provider reported an error' };
    }
    const delta = asRecord(record.delta);
    const text = typeof delta?.text === 'string' ? delta.text : '';
    return { text, done: type === 'message_stop', error: '' };
  }

  if (provider === 'gemini') {
    const text = joinText(partsOf(record), (item) => asRecord(item)?.text);
    const first = Array.isArray(record.candidates) ? asRecord(record.candidates[0]) : null;
    const finish = first?.finishReason;
    return { text, done: typeof finish === 'string' && finish.length > 0, error: '' };
  }

  const choices = Array.isArray(record.choices) ? record.choices : [];
  const first = asRecord(choices[0]);
  const delta = asRecord(first?.delta) ?? asRecord(first?.message);
  const content = delta?.content;
  if (typeof content === 'string' && content) return { text: content, done: false, error: '' };
  const finish = first?.finish_reason;
  return { text: '', done: typeof finish === 'string' && finish.length > 0, error: '' };
}

/**
 * A server-sent event, as the wire actually writes one.
 *
 * The framing is not decoration: every payload is prefixed with `data:`, events
 * are separated by a blank line, and a frame can arrive split across any number
 * of reads. Reading the frame as if it were the JSON payload parses nothing at
 * all, so the fields are separated here and the joined `data` lines are what
 * gets interpreted.
 */
interface SseFrame {
  event: string;
  data: string;
}

/** One line at a time, off the front of the buffer. */
function readSseLine(buffer: string): { line: string; rest: string; blank: boolean } | null {
  const lf = buffer.indexOf('\n');
  if (lf === -1) return null;
  const line = buffer.slice(0, lf).replace(/\r$/, '');
  return { line, rest: buffer.slice(lf + 1), blank: line === '' };
}

/**
 * Folds one wire line into the event being assembled.
 *
 * A blank line is reported by the caller as "the event is complete". `retry:`
 * and `id:` carry no answer, and a leading colon marks a comment — which is
 * what a provider sends to keep an idle connection open.
 */
function foldSseLine(line: string, frame: SseFrame): void {
  if (line.startsWith(':')) return;
  const colon = line.indexOf(':');
  const field = colon === -1 ? line : line.slice(0, colon);
  const value = colon === -1 ? '' : line.slice(colon + 1).replace(/^ /, '');
  if (field === 'data') frame.data = frame.data ? `${frame.data}\n${value}` : value;
  else if (field === 'event') frame.event = value;
}

/**
 * Consumes a `text/event-stream`, calling `onDelta` for every increment.
 *
 * The stall timer is re-armed on each frame, so a model that pauses to think is
 * given `idleMs` of silence per pause rather than being cut off on a budget
 * that assumed the whole answer arrives at once.
 */
async function readStream(
  response: Response,
  request: LiveRequest,
  provider: ProviderId,
  onDelta: (text: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const reader = response.body?.getReader();
  if (!reader) throw new ProviderRequestError('no_text_in_response');
  request.onAbort(() => {
    void reader.cancel().catch(() => undefined);
  });
  const decoder = new TextDecoder();
  let release = request.guard(IDLE_TIMEOUT_MS, () => undefined);
  let total = 0;
  let answer = '';
  let buffer = '';
  let frame: SseFrame = { event: '', data: '' };
  let done = false;

  /**
   * Emits whatever a completed event carried.
   *
   * The event is cleared first, so a frame that throws cannot be replayed by
   * the next one. Returns true when the provider has said it is finished.
   */
  const emit = (): boolean => {
    const event = frame;
    frame = { event: '', data: '' };
    if (event.event === 'error') {
      done = true;
      throw new ProviderRequestError('http', event.data || 'the provider reported an error');
    }
    if (event.data === '') return false;
    const step = deltaFromFrame(event.data, provider);
    if (step.error) {
      done = true;
      throw new ProviderRequestError('http', step.error);
    }
    if (step.text) {
      answer += step.text;
      if (answer.length > MAX_RESPONSE_TEXT_LENGTH) answer = answer.slice(0, MAX_RESPONSE_TEXT_LENGTH);
      onDelta(step.text);
    }
    if (step.done) done = true;
    return step.done;
  };

  try {
    while (!done) {
      let result: ReadableStreamReadResult<Uint8Array>;
      try {
        result = await reader.read();
      } catch (error) {
        if (isAbortError(error)) throw new ProviderRequestError(endCode(request, signal));
        throw new ProviderRequestError('network_unavailable');
      }
      if (result.done) break;
      total += result.value.byteLength;
      if (total > MAX_RESPONSE_BYTES) {
        await reader.cancel();
        throw new ProviderRequestError('response_too_large');
      }
      // Progress is proof the model is still working, so the stall budget
      // restarts here. A slow model is never punished for being slow; only a
      // silent one is.
      release();
      release = request.guard(IDLE_TIMEOUT_MS, () => undefined);
      buffer += decoder.decode(result.value, { stream: true });

      let line = readSseLine(buffer);
      while (line && !done) {
        buffer = line.rest;
        if (line.blank) {
          // A blank line closes the event.
          emit();
        } else {
          foldSseLine(line.line, frame);
        }
        line = readSseLine(buffer);
      }
    }
    if (request.aborted) throw new ProviderRequestError(endCode(request, signal));
    if (!done) {
      // The stream stopped without a terminator, which a server that closes the
      // connection does. Whatever arrived is the answer, and an event that was
      // still open at the cut counts as one.
      const tail = decoder.decode();
      if (tail) buffer += tail;
      let line = readSseLine(buffer);
      while (line) {
        buffer = line.rest;
        if (!line.blank) foldSseLine(line.line, frame);
        line = readSseLine(buffer);
      }
      emit();
    }
  } finally {
    release();
    reader.releaseLock();
  }
  return answer;
}

// -------------------------------------------------------------------- exports

export async function discoverProviderModels(
  settings: Settings,
  fetcher: Fetcher = fetch,
  signal?: AbortSignal,
): Promise<ModelDiscoveryResult> {
  const safeSettings = createSettings(settings);
  const provider = providerFor(safeSettings.provider);
  const baseUrl = normalizeBaseUrl(safeSettings.baseUrl || provider.defaultBaseUrl);
  const offline = (error: string): ModelDiscoveryResult => ({
    models: provider.staticModels,
    source: 'offline',
    error,
  });
  // A local endpoint needs no key, so a missing one is not a reason to refuse
  // the request: the catalogue is exactly what a keyless provider is for.
  if (provider.requiresApiKey && isMissingKey(safeSettings)) return offline('missing_credentials');
  if (!isValidProviderEndpoint(baseUrl, safeSettings.provider)) return offline('invalid_endpoint');

  const request = new LiveRequest(signal);
  request.onFinish(() => request.settle());
  try {
    const response = await send(
      fetcher,
      `${baseUrl}/models`,
      { headers: authHeaders(safeSettings) },
      request,
    );
    if (!response.ok) {
      await cancelResponse(response);
      return offline(`http_${response.status}`);
    }
    const models = [...new Set(extractModels(await readJson(response, request, CATALOGUE_TIMEOUT_MS), safeSettings.provider))].sort();
    if (models.length === 0) return offline('empty_catalog');
    return { models, source: 'remote' };
  } catch (error) {
    if (error instanceof ProviderRequestError && error.code === 'aborted') throw error;
    return offline('network_unavailable');
  } finally {
    request.settle();
  }
}

export interface CompletionOptions {
  /** Called with each increment of a streamed reply. */
  onDelta?: (text: string) => void;
}

/**
 * Asks the provider for a reply, and returns exactly what it wrote.
 *
 * The stream is requested, but a stream is not required: a server that answers
 * with one ordinary JSON body is read as such. Refusing to talk to a provider
 * because it could not stream is the same mistake as refusing a redirect, only
 * with a worse outcome for the person waiting.
 */
export async function requestProviderCompletion(
  settings: Settings,
  prompt: string,
  fetcher: Fetcher = fetch,
  signal?: AbortSignal,
  history: readonly CompletionMessage[] = [],
  options: CompletionOptions = {},
): Promise<string> {
  const safeSettings = createSettings(settings);
  const provider = providerFor(safeSettings.provider);
  if (isMissingKey(safeSettings)) throw new ProviderRequestError('missing_credentials');
  if (typeof prompt !== 'string' || !prompt.trim()) throw new ProviderRequestError('missing_prompt');
  if (prompt.length > MAX_PROMPT_LENGTH) throw new ProviderRequestError('prompt_too_large');

  const baseUrl = normalizeBaseUrl(safeSettings.baseUrl || provider.defaultBaseUrl);
  if (!isValidProviderEndpoint(baseUrl, safeSettings.provider)) throw new ProviderRequestError('invalid_endpoint');

  const promptText = sanitizeStoredText(prompt, MAX_PROMPT_LENGTH).trim();
  const wantsStream = typeof options.onDelta === 'function';
  const built = completionBody(safeSettings, promptText, history, wantsStream);
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...authHeaders(safeSettings),
  };
  if (safeSettings.provider === 'openrouter') {
    headers['HTTP-Referer'] = 'https://clio-agent.local';
    headers['X-Title'] = 'Clio Agent 3 Beta 1';
  }

  const request = new LiveRequest(signal);
  let response: Response;
  try {
    response = await send(
      fetcher,
      completionEndpoint(safeSettings, built.model, wantsStream),
      {
        method: 'POST',
        headers,
        body: JSON.stringify(built.body),
      },
      request,
    );
  } catch (error) {
    if (error instanceof ProviderRequestError) throw error;
    if (isAbortError(error)) throw new ProviderRequestError(endCode(request, signal));
    throw new ProviderRequestError('network_unavailable');
  }

  try {
    if (!response.ok) throw await httpError(response, request);

    const contentType = response.headers?.get('content-type') ?? '';
    if (wantsStream && contentType.toLowerCase().includes('text/event-stream')) {
      const answer = await readStream(response, request, safeSettings.provider, options.onDelta!, signal);
      const cleaned = boundedReply(answer);
      if (cleaned) return cleaned;
      throw new ProviderRequestError('no_text_in_response');
    }

    const text = textFromPayload(await readJson(response, request, IDLE_TIMEOUT_MS), safeSettings.provider);
    if (text) options.onDelta?.(text);
    return text;
  } catch (error) {
    if (error instanceof ProviderRequestError) throw error;
    if (isAbortError(error)) throw new ProviderRequestError(endCode(request, signal));
    throw new ProviderRequestError('network_unavailable');
  } finally {
    request.settle();
  }
}
