import { isSafeTimestamp, MAX_TIMESTAMP, sanitizeStoredText } from '../security.js';
import type { ChatMessage } from '../types.js';

/**
 * The vocabulary of an agent link.
 *
 * An agent is not a model provider. A provider answers a prompt; an agent has a
 * state it is in whether or not anybody is talking to it, decides for itself
 * whether a message deserves an answer, and may act without answering at all.
 * So the wire model here is a *state* — presence, thoughts, intentions, actions,
 * budget, guardian, lifecycle — with the conversation as one panel of it.
 *
 * Every reader below is total. A field that is missing, of the wrong shape or
 * absurdly large becomes a bounded default instead of throwing, because the one
 * thing worse than an agent that looks idle is a surface that cannot draw it.
 */

/** How the link itself is doing, which is not the same as what the agent is doing. */
export type AgentLinkStatus = 'offline' | 'connecting' | 'online' | 'degraded';

export const AGENT_CONTROL_ACTIONS = [
  'pause_actions',
  'pause_all',
  'resume',
  'stop',
  'emergency_stop',
  'preview_on',
  'preview_off',
] as const;
export type AgentControlAction = (typeof AGENT_CONTROL_ACTIONS)[number];

export function isAgentControlAction(value: unknown): value is AgentControlAction {
  return (AGENT_CONTROL_ACTIONS as readonly string[]).includes(value as string);
}

export interface AgentControl {
  paused: boolean;
  pause_actions: boolean;
  stopped: boolean;
  emergency: boolean;
  /**
   * The agent may talk but not act: every tool is refused by the gate.
   *
   * Its own flag rather than another name for `pause_actions`, because the two
   * answer different questions and a surface that could not tell them apart would
   * report the wrong one. `pause_actions` is oversight — somebody stopped this
   * agent — and it is cleared by `resume`. This is a mode the owner put the agent
   * in, and no lifecycle verb touches it in either direction.
   */
  preview: boolean;
}

export interface AgentFocus {
  intention_id: string | null;
  title: string;
  kind: string;
}

export interface AgentCycle {
  state: string;
  ts: string | null;
  tier: string | null;
}

export interface AgentPresence {
  state: string;
  focus: AgentFocus | null;
  ts: string | null;
  recentCycles: AgentCycle[];
}

export interface AgentThought {
  id: string;
  ts: string | null;
  summary: string;
}

export interface AgentIntention {
  id: string;
  parent_id: string | null;
  kind: string;
  title: string;
  desired_end_state: string | null;
  status: string;
  priority: number;
  deadline: string | null;
  commissioned_by: string | null;
  budget_usd: number | null;
  spent_usd: number | null;
  origin: string | null;
  children: AgentIntention[];
  depth: number;
}

export interface AgentAction {
  id: string;
  ts_start: string | null;
  ts_end: string | null;
  thread_id: string | null;
  tool: string;
  args_redacted: Record<string, unknown> | null;
  reason: string | null;
  status: string;
  result_digest: Record<string, unknown> | null;
  undo_ref: string | null;
}

export interface AgentBudgetBucket {
  bucket: string;
  total: number;
}

export interface AgentSpend {
  model: string;
  total: number;
}

export interface AgentDailySpend {
  day: string;
  total: number;
}

/**
 * The caps the agent enforces itself.
 *
 * They are read from the same configuration the gateway enforces, and carried
 * alongside the spend rather than hard-coded in a surface: a dashboard that
 * repeats the number it was built with is a dashboard that quietly starts lying
 * the day the cap is retuned.
 */
export interface AgentBudgetCaps {
  daily_usd: number;
  monthly_usd: number;
  commitment_usd: number;
  discretionary_usd: number;
}

export interface AgentBudget {
  todayByBucket: AgentBudgetBucket[];
  byModel: AgentSpend[];
  daily: AgentDailySpend[];
  caps: AgentBudgetCaps;
}

export interface AgentTrashEntry {
  id: string;
  origin: string;
  trashed_at: string | null;
  retention_until: string | null;
  reason: string | null;
  path: string | null;
}

export interface AgentSnapshotEntry {
  name: string;
  created_at: string | null;
  reason: string | null;
  backend: string | null;
}

export interface AgentIntegrityReport {
  ok: boolean;
  ran_at: string | null;
  audit_chain_ok: boolean;
  artifact_mismatches: unknown[];
}

export interface AgentAuditHead {
  seq: number | null;
  ts: string | null;
  summary: string | null;
  hash: string | null;
}

export interface AgentGuardian {
  trash: AgentTrashEntry[];
  snapshots: AgentSnapshotEntry[];
  integrity: AgentIntegrityReport | null;
  auditHead: AgentAuditHead | null;
  protectedCount: number;
}

/**
 * The model the agent thinks with.
 *
 * An agent routes its own calls, so the model catalogue is the agent's business
 * and not a choice a surface makes on its behalf. But a person running one on
 * their own machine has exactly one key, and the agent cannot know it — so the
 * agent accepts a *base model*: one model, configured here, that it prefers over
 * everything in its catalogue. That is what a surface is allowed to set, and
 * setting it is the difference between an agent that can think and one that
 * answers every question with a gateway error.
 *
 * `keyPresent` and `keyHint` are the whole of what a surface ever learns about
 * the credential. The key is written to the agent's own file and read from there
 * by the agent; it does not travel back over the link, which is what lets this
 * form be filled in from a page that has never held a secret.
 *
 * `keyEnv` is the same idea for a key that lives in the environment: the file
 * records the *name* of the variable, and the agent reads the value out of it on
 * every request. A surface can therefore offer "use the variable" for a machine
 * whose key is already exported, and can say afterwards which variable is in play
 * — without the value ever being anything it has seen.
 */
export interface AgentBaseModel {
  configured: boolean;
  /** A vendor a person chose, as this kit's `ProviderId` names it. */
  provider: string;
  /** The adapter the agent will hand the request to; several vendors share one. */
  protocol: string;
  model: string;
  baseUrl: string;
  keyPresent: boolean;
  keyHint: string;
  /** The environment variable the file points at, when it points at one. */
  keyEnv: string;
  /** Whether the process that would read it can actually see it. */
  keyEnvPresent: boolean;
}

/**
 * A base model as a surface writes it. An empty `apiKey` keeps the stored one.
 *
 * `useEnv` is "use the provider's variable", and it is a separate flag rather
 * than a magic value in `apiKey` because the two are different decisions: one
 * means *this key, which I typed*, the other means *whatever is exported there
 * when the request goes out*. Sending a sentinel string as the key would put a
 * value that looks like a credential into a header.
 */
export interface AgentBaseModelInput {
  provider: string;
  model: string;
  baseUrl: string;
  apiKey?: string;
  clearKey?: boolean;
  useEnv?: boolean;
}

/**
 * One provider's environment credential, as far as a surface may know it.
 *
 * A fact, not a secret: which variables are consulted, whether one of them is
 * set, which one it was, and four characters of it. That is the whole of what a
 * form needs to decide whether to offer the button, and it is the most a page
 * can hold even if it wanted more — a browser has no access to a process's
 * environment at all, which is the whole reason this crosses the link.
 */
export interface EnvApiKey {
  provider: string;
  variables: string[];
  present: boolean;
  /** The variable that is actually set, or `''` when none is. */
  variable: string;
  hint: string;
}

/** Reads the environment report, dropping anything that is not one of these. */
export function readEnvApiKeys(value: unknown): EnvApiKey[] {
  return list(record(value).keys)
    .map((entry) => {
      const source = record(entry);
      return {
        provider: text(source.provider, LIMITS.code),
        variables: list(source.variables)
          .filter((name): name is string => typeof name === 'string')
          .map((name) => text(name, LIMITS.code))
          .filter(Boolean)
          .slice(0, 4),
        present: bool(source.present),
        variable: text(source.variable, LIMITS.code),
        hint: text(source.hint, 32),
      };
    })
    .filter((entry) => entry.provider !== '')
    .slice(0, 32);
}

/**
 * What `POST /api/model` refuses, as a code rather than a status.
 *
 * Four different mistakes, because the four things somebody typed are four
 * different things to be told about: a provider the agent has no adapter for, a
 * model name that is not one, an address the agent must not post its reasoning
 * to, and a vendor that needs a key that was not given. Two more are not things
 * somebody typed but things the interface had to answer for: a variable that is
 * not exported where the agent runs, and a home the agent cannot write into —
 * which is the one that used to arrive as an outage, and told somebody to start
 * an agent that was already running.
 */
export const BASE_MODEL_REFUSALS = [
  'unknown_provider',
  'invalid_model',
  'invalid_endpoint',
  'key_required',
  'env_key_missing',
  'home_unwritable',
  'cross_origin_refused',
] as const;
export type BaseModelRefusal = (typeof BASE_MODEL_REFUSALS)[number];

export function isBaseModelRefusal(value: unknown): value is BaseModelRefusal {
  return typeof value === 'string' && (BASE_MODEL_REFUSALS as readonly string[]).includes(value);
}

export function emptyAgentBaseModel(): AgentBaseModel {
  return {
    configured: false,
    provider: '',
    protocol: '',
    model: '',
    baseUrl: '',
    keyPresent: false,
    keyHint: '',
    keyEnv: '',
    keyEnvPresent: false,
  };
}

export function readAgentBaseModel(value: unknown): AgentBaseModel {
  const source = record(value);
  return {
    configured: bool(source.configured),
    provider: text(source.provider, LIMITS.code),
    protocol: text(source.protocol, LIMITS.code),
    model: text(source.model, LIMITS.model),
    baseUrl: text(source.base_url, LIMITS.endpoint),
    keyPresent: bool(source.key_present),
    keyHint: text(source.key_hint, 32),
    keyEnv: text(source.key_env, LIMITS.code),
    keyEnvPresent: bool(source.key_env_present),
  };
}

/**
 * The base model as a service reported it in reply to a direct question, or
 * `null` when it did not report one at all.
 *
 * Separate from {@link readAgentBaseModel} because the two answer different
 * questions. A snapshot with no `model` section is a snapshot from a build
 * without the section, and the total reader rightly reads that as "no base
 * model". A *reply* to "what is your base model?" that is not a base model means
 * the service did not understand the question — and the difference is the whole
 * point, because a service that does not have the route answers it with its own
 * HTML and a 200. Believing that would report a healthy agent as having no model.
 */
export function readBaseModelReply(value: unknown): AgentBaseModel | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (!Object.prototype.hasOwnProperty.call(value, 'configured')) return null;
  return readAgentBaseModel(value);
}

/**
 * What the agent is called, as far as a surface may know it.
 *
 * `configured` is the honest half of this answer, and it is why it exists at all. An
 * empty `selfName` does not mean the agent has no name — it has whatever its config
 * calls it — it means no *person* has chosen one. A settings screen showing an empty
 * field is offering to fill it in; a screen showing a name has something to show.
 *
 * The name is a person's own word and travels like any other free text: read through
 * {@link text}, so it is sanitised and bounded like everything else that arrives from
 * a service and ends up on a screen.
 */
export interface AgentIdentity {
  configured: boolean;
  selfName: string;
}

/** Nothing named: the state a form offers to leave, not one it fills in. */
export function emptyAgentIdentity(): AgentIdentity {
  return { configured: false, selfName: '' };
}

export function readAgentIdentity(value: unknown): AgentIdentity {
  const source = record(value);
  const selfName = text(source.self_name, LIMITS.selfName);
  // A name with nothing behind it is not a name. The agent answers this the same
  // way, and a surface that believed the flag over the field would show an empty
  // field as though it were the one thing about the agent a person had decided.
  return { configured: bool(source.configured) && selfName !== '', selfName };
}

/**
 * The identity as a service reported it in reply to a direct question, or `null`
 * when it did not report one at all.
 *
 * Separate from {@link readAgentIdentity} for the reason {@link readBaseModelReply} is
 * separate from {@link readAgentBaseModel}: a service without the route answers an
 * unmatched path with its own HTML and a 200, and believing that would report a
 * healthy agent as one nobody has named.
 */
export function readIdentityReply(value: unknown): AgentIdentity | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  if (!Object.prototype.hasOwnProperty.call(value, 'configured')) return null;
  return readAgentIdentity(value);
}

/**
 * What `POST /api/identity` refuses, as a code rather than a status.
 *
 * Three, and the first is the only one that is somebody's typing: a name that is not
 * a name, a home the agent cannot be written into, and a request from somewhere that
 * is not this page. They share the last two with the base model on purpose — the same
 * two failures, with the same fixes, and one vocabulary for both.
 */
export const IDENTITY_REFUSALS = [
  'invalid_name',
  'home_unwritable',
  'cross_origin_refused',
] as const;
export type IdentityRefusal = (typeof IDENTITY_REFUSALS)[number];

export function isIdentityRefusal(value: unknown): value is IdentityRefusal {
  return typeof value === 'string' && (IDENTITY_REFUSALS as readonly string[]).includes(value);
}

/**
 * What a door write can be refused for.
 *
 * Every one of them is a *different* thing somebody typed, and each has a
 * different fix, so they do not share a sentence. A door that does not exist and
 * an id that is not one for that door both look like "that is not a channel" from
 * the outside, and one of them is a typo in a name while the other is a chat id
 * read off the wrong screen.
 */
export const CHANNEL_REFUSALS = [
  'unknown_channel',
  'unknown_field',
  'invalid_value',
  'invalid_allowed_id',
  'invalid_address',
  'home_unwritable',
  'cross_origin_refused',
] as const;
export type ChannelRefusal = (typeof CHANNEL_REFUSALS)[number];

export function isChannelRefusal(value: unknown): value is ChannelRefusal {
  return typeof value === 'string' && (CHANNEL_REFUSALS as readonly string[]).includes(value);
}

/** One line of the shared timeline, as the agent's own store holds it. */
export interface AgentMessage {
  id: string;
  ts: number;
  direction: 'inbound' | 'outbound';
  channel: string;
  person_id: string | null;
  /**
   * Who wrote this, as that channel's own platform spells their name.
   *
   * Beside `person_id` rather than instead of it, because the two answer
   * different questions and only one of them has an answer on every channel:
   * `person_id` is the address a reply goes to (`tg:819012345678`), and a
   * display name is something WhatsApp gives and Slack only gives with the
   * `users:read` scope. Either may be null; `senderLabel` is what to read when
   * a surface needs one string.
   */
  sender_name: string | null;
  sender_username: string | null;
  text: string;
  urgency: string | null;
  delivery_status: string | null;
}

/**
 * Whoever wrote a message, for a surface that has one line to draw.
 *
 * The same fallback chain the agent itself reads, and duplicated here on purpose
 * rather than shipped from the agent: this runs in a browser and a terminal where
 * the agent's Python is not, and the two have to agree about what
 * `tg:819012345678` reads as — otherwise the transcript says "alice" and the
 * agent says "tg:819012345678" and the reader is left guessing which is which.
 *
 * The address is kept even when a name is known, for the reason it is kept on the
 * agent's side: it is the only half that is guaranteed, and it is what identifies
 * the conversation.
 */
export function senderLabel(message: Pick<AgentMessage, 'person_id' | 'sender_name' | 'sender_username'>): string {
  const name = (message.sender_name ?? '').trim();
  const handle = (message.sender_username ?? '').trim().replace(/^@/, '');
  const who = name || handle;
  // The handle goes in beside a name only when the two are different things —
  // otherwise a message with nothing but a handle reads `ada (@ada, tg:1)`.
  const detail = name && handle && handle.toLowerCase() !== name.toLowerCase() ? `@${handle}` : '';
  const parts = detail && message.person_id ? `${detail}, ${message.person_id}`
    : detail || (message.person_id ?? '');
  if (who && parts) return `${who} (${parts})`;
  return who || parts || 'unknown';
}

export interface AgentView {
  presence: AgentPresence;
  control: AgentControl;
  /** The model the agent thinks with, and the size of the catalogue behind it. */
  model: AgentBaseModel;
  /** What the agent is called, when a person has chosen a name for it. */
  identity: AgentIdentity;
  catalogue: number;
  thoughts: AgentThought[];
  intentions: AgentIntention[];
  actions: AgentAction[];
  budget: AgentBudget;
  guardian: AgentGuardian;
  messages: AgentMessage[];
  /** When this view was last reconciled with the agent, in epoch milliseconds. */
  updatedAt: number;
  /** False until the first snapshot has been read; an empty view is not "nothing happened". */
  loaded: boolean;
}

export type AgentEvent =
  | { type: 'status'; status: AgentLinkStatus }
  | { type: 'view'; view: AgentView }
  | { type: 'agent'; kind: string; payload: Record<string, unknown> }
  | { type: 'error'; message: string };

/** What the agent refuses, and why — the reasons a surface has to be able to say. */
export type AgentFailureCode =
  | 'agent_offline'
  | 'agent_unreachable'
  | 'agent_invalid_endpoint'
  | 'agent_not_running'
  | 'agent_stopped'
  | 'agent_declined'
  // The agent heard the question and never got to an answer: a model that
  // failed, ran out of room, or answered in prose. Kept apart from
  // `agent_declined` because the two call for opposite things — one needs
  // nothing, the other is worth sending again.
  | 'agent_unanswered'
  | 'agent_timeout'
  | 'agent_aborted'
  | 'agent_refused';

export class AgentRequestError extends Error {
  readonly code: AgentFailureCode;
  readonly detail: string;

  constructor(code: AgentFailureCode, detail = '') {
    super(detail ? `${code}: ${detail}` : code);
    this.name = 'AgentRequestError';
    this.code = code;
    this.detail = detail;
  }

  override toString(): string {
    return this.detail ? `${this.code}: ${this.detail}` : this.code;
  }
}

// ---------------------------------------------------------------------------
// Readers
// ---------------------------------------------------------------------------

const LIMITS = {
  text: 4_000,
  summary: 4_000,
  id: 128,
  name: 256,
  code: 64,
  json: 64_000,
  thoughts: 60,
  intentions: 400,
  actions: 60,
  messages: 100,
  cycles: 24,
  trash: 40,
  snapshots: 20,
  series: 14,
  models: 12,
  endpoint: 2_048,
  model: 256,
  /** Matches the agent's own rule for a name, so a form cannot accept what it refuses. */
  selfName: 64,
} as const;

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** A string field, sanitised and bounded. Never `undefined`, never a throw. */
function text(value: unknown, max: number): string {
  if (typeof value !== 'string') return '';
  return sanitizeStoredText(value, max);
}

function maybeText(value: unknown, max: number): string | null {
  const result = text(value, max);
  return result ? result : null;
}

function identifier(value: unknown, max = LIMITS.id): string {
  if (typeof value === 'string') {
    const trimmed = text(value, max).trim();
    if (trimmed) return trimmed;
  }
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  return '';
}

function bool(value: unknown): boolean {
  return value === true || value === 1 || value === 'true';
}

function amount(value: unknown): number {
  const numeric = typeof value === 'string' ? Number.parseFloat(value) : value;
  if (typeof numeric !== 'number' || !Number.isFinite(numeric)) return 0;
  // Postgres NUMERIC arrives as a string through JSON in some drivers; a cap
  // keeps a nonsensical value from drawing a bar that overflows its panel.
  return Math.max(0, Math.min(numeric, 1_000_000_000));
}

function optionalAmount(value: unknown): number | null {
  if (value === null || value === undefined || value === '') return null;
  const numeric = typeof value === 'string' ? Number.parseFloat(value) : value;
  return typeof numeric === 'number' && Number.isFinite(numeric) ? numeric : null;
}

/** An ISO instant from the agent, as epoch milliseconds; `null` when unusable. */
function instant(value: unknown): number | null {
  if (typeof value === 'number' && isSafeTimestamp(value)) return Math.min(value, MAX_TIMESTAMP);
  if (typeof value !== 'string' || !value.trim()) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function optionalInstant(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.slice(0, 40) : null;
}

/**
 * A redacted-arguments or result-digest blob.
 *
 * A JSONB column arrives as an object, but it crosses a driver boundary on the
 * way and can arrive as a string instead, so both are accepted. Anything else —
 * a number, an array, a truncated write — is not an argument map and is reported
 * as absent rather than half-drawn.
 */
function jsonObject(value: unknown): Record<string, unknown> | null {
  if (value === null || value === undefined) return null;
  const candidate = typeof value === 'string' ? parseBoundedJson(value) : value;
  if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) return null;
  return candidate as Record<string, unknown>;
}

function parseBoundedJson(value: string): unknown {
  if (!value.trim() || value.length > LIMITS.json) return null;
  try {
    return JSON.parse(value);
  } catch {
    return null;
  }
}

export function readControl(value: unknown): AgentControl {
  const source = record(value);
  return {
    paused: bool(source.paused),
    pause_actions: bool(source.pause_actions),
    stopped: bool(source.stopped),
    emergency: bool(source.emergency),
    preview: bool(source.preview),
  };
}

export function readPresence(value: unknown): AgentPresence {
  const source = record(value);
  const focusRaw = record(source.focus);
  const title = text(focusRaw.title, LIMITS.summary);
  const focus = title || focusRaw.intention_id
    ? {
      intention_id: maybeText(focusRaw.intention_id, LIMITS.id),
      title,
      kind: text(focusRaw.kind, LIMITS.code) || 'intention',
    }
    : null;
  return {
    state: text(source.state, LIMITS.code) || 'UNKNOWN',
    focus,
    ts: optionalInstant(source.ts),
    recentCycles: list(source.recentCycles).slice(0, LIMITS.cycles).map((entry) => {
      const cycle = record(entry);
      return {
        state: text(cycle.state, LIMITS.code) || 'UNKNOWN',
        ts: optionalInstant(cycle.ts),
        tier: maybeText(cycle.tier, LIMITS.code),
      };
    }),
  };
}

export function readThought(value: unknown): AgentThought {
  const source = record(value);
  return {
    id: identifier(source.id),
    ts: optionalInstant(source.ts),
    summary: text(source.summary, LIMITS.summary),
  };
}

function readIntentionNode(value: unknown): AgentIntention {
  const source = record(value);
  return {
    id: identifier(source.id),
    parent_id: maybeText(source.parent_id, LIMITS.id),
    kind: text(source.kind, LIMITS.code) || 'intention',
    title: text(source.title, LIMITS.summary),
    desired_end_state: maybeText(source.desired_end_state, LIMITS.summary),
    status: text(source.status, LIMITS.code) || 'open',
    priority: amount(source.priority),
    deadline: optionalInstant(source.deadline),
    commissioned_by: maybeText(source.commissioned_by, LIMITS.name),
    budget_usd: optionalAmount(source.budget_usd),
    spent_usd: optionalAmount(source.spent_usd),
    origin: maybeText(source.origin, LIMITS.code),
    children: [],
    depth: 0,
  };
}

/**
 * Intentions arrive flat with a parent pointer; a tree is what a reader
 * actually navigates.
 *
 * Two things are handled rather than trusted. An intention whose parent is not in
 * the page is lifted to the roots, because a missing row is a display bug and a
 * silently vanished goal is not. And a parent pointer that closes a loop is
 * refused, because a cycle rendered as a tree recurses forever — two intentions
 * that point at each other are still two intentions, shown side by side.
 */
export function readIntentions(value: unknown): AgentIntention[] {
  const nodes = list(value)
    .slice(0, LIMITS.intentions)
    .map(readIntentionNode)
    .filter((node) => node.id);
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const roots: AgentIntention[] = [];
  for (const node of nodes) {
    const parent = node.parent_id ? byId.get(node.parent_id) : undefined;
    if (parent && parent !== node && !descendsFrom(parent, node.id, byId)) {
      parent.children.push(node);
      node.depth = 1;
    } else {
      roots.push(node);
    }
  }
  return roots;
}

/** True when `id` is reachable by walking parents upward from `from`. */
function descendsFrom(from: AgentIntention, id: string, byId: Map<string, AgentIntention>): boolean {
  let cursor: AgentIntention | undefined = from;
  // The hop count is a bound, not a guess: a chain longer than the page cannot
  // exist, so stopping here cannot discard a real parent.
  for (let hops = 0; cursor && hops < LIMITS.intentions; hops += 1) {
    if (cursor.id === id) return true;
    cursor = cursor.parent_id ? byId.get(cursor.parent_id) : undefined;
  }
  return false;
}

export function readAction(value: unknown): AgentAction {
  const source = record(value);
  return {
    id: identifier(source.id),
    ts_start: optionalInstant(source.ts_start),
    ts_end: optionalInstant(source.ts_end),
    thread_id: maybeText(source.thread_id, LIMITS.id),
    tool: text(source.tool, LIMITS.name) || 'unknown',
    args_redacted: jsonObject(source.args_redacted),
    reason: maybeText(source.reason, LIMITS.summary),
    status: text(source.status, LIMITS.code) || 'unknown',
    result_digest: jsonObject(source.result_digest),
    undo_ref: maybeText(source.undo_ref, LIMITS.id),
  };
}

function readBudgetBucket(value: unknown): AgentBudgetBucket {
  const source = record(value);
  return { bucket: text(source.bucket, LIMITS.code) || 'discretionary', total: amount(source.total) };
}

function readSpend(value: unknown): AgentSpend {
  const source = record(value);
  return { model: text(source.model, LIMITS.name) || 'unknown', total: amount(source.total) };
}

function readDailySpend(value: unknown): AgentDailySpend {
  const source = record(value);
  return { day: text(source.day, 32), total: amount(source.total) };
}

/**
 * A cap of zero is passed through as zero, and the panels read it as "no cap
 * configured" rather than as "every dollar is over budget" — which is the honest
 * reading of a bridge that has not been told about the gateway's configuration.
 */
function readCaps(value: unknown): AgentBudgetCaps {
  const source = record(value);
  return {
    daily_usd: amount(source.daily_usd),
    monthly_usd: amount(source.monthly_usd),
    commitment_usd: amount(source.commitment_usd),
    discretionary_usd: amount(source.discretionary_usd),
  };
}

export function readBudget(value: unknown): AgentBudget {
  const source = record(value);
  return {
    todayByBucket: list(source.todayByBucket).slice(0, 8).map(readBudgetBucket),
    byModel: list(source.byModel).slice(0, LIMITS.models).map(readSpend),
    daily: list(source.daily).slice(0, LIMITS.series).map(readDailySpend),
    caps: readCaps(source.caps),
  };
}

export function readTrashEntry(value: unknown): AgentTrashEntry {
  const source = record(value);
  return {
    id: text(source.id, LIMITS.name),
    origin: text(source.origin, LIMITS.name) || 'unknown',
    trashed_at: optionalInstant(source.trashed_at),
    retention_until: optionalInstant(source.retention_until),
    reason: maybeText(source.reason, LIMITS.summary),
    path: maybeText(source.path, LIMITS.name),
  };
}

export function readSnapshotEntry(value: unknown): AgentSnapshotEntry {
  const source = record(value);
  return {
    name: text(source.name, LIMITS.name) || 'snapshot',
    created_at: optionalInstant(source.created_at),
    reason: maybeText(source.reason, LIMITS.summary),
    backend: maybeText(source.backend, LIMITS.code),
  };
}

export function readIntegrity(value: unknown): AgentIntegrityReport | null {
  const source = record(value);
  if (!Object.keys(source).length) return null;
  return {
    ok: bool(source.ok),
    ran_at: optionalInstant(source.ran_at),
    audit_chain_ok: bool(source.audit_chain_ok),
    artifact_mismatches: list(source.artifact_mismatches).slice(0, 32),
  };
}

export function readAuditHead(value: unknown): AgentAuditHead | null {
  const source = record(value);
  if (!Object.keys(source).length) return null;
  const seq = typeof source.seq === 'number' ? source.seq : Number.parseInt(String(source.seq ?? ''), 10);
  return {
    seq: Number.isFinite(seq) ? seq : null,
    ts: optionalInstant(source.ts),
    summary: maybeText(source.summary, LIMITS.summary),
    hash: maybeText(source.hash, 256),
  };
}

export function readGuardian(value: unknown): AgentGuardian {
  const source = record(value);
  return {
    trash: list(source.trash).slice(0, LIMITS.trash).map(readTrashEntry),
    snapshots: list(source.snapshots).slice(0, LIMITS.snapshots).map(readSnapshotEntry),
    integrity: readIntegrity(source.integrity),
    auditHead: readAuditHead(source.auditHead),
    protectedCount: Math.floor(amount(source.protectedCount)),
  };
}

export function readAgentMessage(value: unknown): AgentMessage {
  const source = record(value);
  const delivery = record(source.delivery);
  return {
    id: identifier(source.id),
    // An unparseable timestamp must not become `NaN`, which would sort every
    // such message to one end of the timeline and hide it behind an hour of
    // other traffic.
    ts: instant(source.ts) ?? 0,
    direction: source.direction === 'outbound' ? 'outbound' : 'inbound',
    channel: text(source.channel, LIMITS.code) || 'web',
    person_id: maybeText(source.person_id, LIMITS.name),
    sender_name: maybeText(source.sender_name, LIMITS.name),
    sender_username: maybeText(source.sender_username, LIMITS.name),
    text: text(source.text, LIMITS.text),
    urgency: maybeText(delivery.urgency, LIMITS.code),
    delivery_status: maybeText(delivery.status, LIMITS.code),
  };
}

/** An empty view. Never `null`: a surface with nothing to draw still has to draw. */
export function emptyAgentView(now = 0): AgentView {
  return {
    presence: readPresence(null),
    control: readControl(null),
    model: emptyAgentBaseModel(),
    identity: emptyAgentIdentity(),
    catalogue: 0,
    thoughts: [],
    intentions: [],
    actions: [],
    budget: readBudget(null),
    guardian: readGuardian(null),
    messages: [],
    updatedAt: now,
    loaded: false,
  };
}

export function readAgentView(value: unknown, now = 0): AgentView {
  const source = record(value);
  const catalogue = source.catalogue;
  return {
    presence: readPresence(source.presence),
    control: readControl(source.control ?? record(record(source.presence).control)),
    model: readAgentBaseModel(source.model),
    identity: readAgentIdentity(source.identity),
    catalogue: typeof catalogue === 'number' && Number.isFinite(catalogue) && catalogue >= 0
      ? Math.min(Math.trunc(catalogue), 10_000)
      : 0,
    thoughts: list(source.thoughts).slice(0, LIMITS.thoughts).map(readThought),
    intentions: readIntentions(source.intentions),
    actions: list(source.actions).slice(0, LIMITS.actions).map(readAction),
    budget: readBudget(source.budget),
    guardian: readGuardian(source.guardian),
    messages: list(source.messages).slice(-LIMITS.messages).map(readAgentMessage),
    updatedAt: now,
    loaded: true,
  };
}

/**
 * The timeline as the shared transcript, and the channels it spans.
 *
 * Direction becomes role, and nothing else is invented: an inbound message is
 * something the person said, an outbound one is something the agent said, and
 * the timestamps are the agent's own. There is no third role and no status field
 * for turns the agent never took.
 *
 * The channel list comes back alongside rather than being computed later by a
 * surface, so both surfaces learn the same answer from the same function — a
 * browser filtering on `telegram` and a terminal filtering on `telegram` that
 * disagree about whether Telegram exists is two vocabularies for one agent.
 */
export function toChatTimeline(messages: readonly AgentMessage[]): {
  messages: ChatMessage[];
  channels: string[];
} {
  const chat = toChatMessages(messages);
  return { messages: chat, channels: channelsIn(chat) };
}

export function toChatMessages(messages: readonly AgentMessage[]): ChatMessage[] {
  return messages.map((message) => ({
    id: message.id,
    role: message.direction === 'outbound' ? 'assistant' : 'user',
    text: message.text,
    createdAt: message.ts,
    // The one thing carried across that is not invented here: the agent already
    // knows which door each line came through, and dropping it would leave a
    // transcript that mixes four channels with nothing to tell them apart.
    channel: message.channel,
    // The address of the person this line is with, on both halves of the
    // exchange: the agent's own turn is delivered to the same address the
    // question arrived from, so a reply with nobody on it is a reply with
    // nobody in the conversation either.
    ...(message.person_id ? { person: message.person_id } : {}),
    // The sender, and only the sender: a transcript that shows four Telegram
    // messages as one undifferentiated stream cannot answer "who said the third
    // one", and a name that has been invented for it would be worse than none.
    // Absent for the agent's own turns and for anything the channel could not
    // name, which is the honest floor — the same shape `toChatMessages` already
    // accepts `undefined` for on `channel`.
    ...(message.direction === 'inbound' && (message.sender_name || message.sender_username)
      ? { author: senderLabel(message) }
      : {}),
  }));
}

/**
 * Which conversation one line belongs to.
 *
 * The agent's own key — `{door}:{address}` — so the surface and the store agree
 * about what a conversation is. That is the whole reason it is this pair and not
 * the address alone: the same person reached on Telegram and on the browser is
 * two conversations the agent can answer differently, and merging them would
 * draw one person's reply inside the other person's question.
 *
 * A turn with no address on it is still a conversation, and is keyed by its door
 * alone rather than dropped: a row from a door that could not name its sender is
 * a message somebody wrote, and hiding it would be a worse answer than showing
 * it under the door it came in on.
 */
export function conversationKey(message: ChatMessage): string {
  const channel = typeof message.channel === 'string' ? message.channel.trim() : '';
  const person = typeof message.person === 'string' ? message.person.trim() : '';
  return `${channel || 'web'}:${person}`;
}

/** One person, on one door: everything a surface needs to show and answer them. */
export interface AgentConversation {
  /** `{door}:{address}`, which is `conversationKey` and the store's own key. */
  id: string;
  channel: string;
  /** The address the door reaches them at, or `''` when it could name nobody. */
  person: string;
  /**
   * What to call them in a list.
   *
   * A name the door gave, and otherwise the address itself: a reader asking
   * "who is this?" is owed the truth rather than a placeholder, and the address
   * is the one half of the pair that every channel supplies. A conversation with
   * no address at all is named by its door, which is the only thing known about
   * it.
   */
  label: string;
  /** Turns held here, and when the last of them arrived. */
  messages: number;
  lastAt: number;
}

/**
 * The conversations a transcript holds, most recently active first.
 *
 * Newest first rather than alphabetical because this list answers "who did I
 * miss?" — the conversation at the top is the one that moved while the reader
 * was looking at something else, and a list sorted by name would bury it under
 * whoever happens to start with an A.
 *
 * `self` is the person reading this surface, and it is asked for rather than
 * guessed at because the local line has to be offered before it exists: a browser
 * that has never been typed into holds no `web` row at all, and a filter built
 * only from what has been said offers nothing to say it with.
 *
 * `localChannel` is the door the reading surface's own words travel through —
 * the browser's is `web`, the terminal's is `cli` — because the local line is
 * offered per surface, not once for everybody. Each interface's own turn is a
 * conversation of its own, and the one this surface is on by default is the one
 * its own words are filed under.
 */
export function conversationsIn(
  messages: readonly ChatMessage[],
  self = '',
  localChannel = 'web',
): AgentConversation[] {
  const found = new Map<string, AgentConversation>();
  for (const message of messages) {
    const channel = (message.channel ?? 'web').trim() || 'web';
    const person = (message.person ?? '').trim();
    const id = `${channel}:${person}`;
    const existing = found.get(id);
    if (existing) {
      existing.messages += 1;
      existing.lastAt = Math.max(existing.lastAt, message.createdAt);
      // The newest name wins, so a correspondent who renames themselves in
      // Telegram is named by what they call themselves now.
      if (message.author) existing.label = message.author;
      continue;
    }
    found.set(id, {
      id,
      channel,
      person,
      label: message.author ?? (person || channelLabel(channel)),
      messages: 1,
      lastAt: message.createdAt,
    });
  }
  const mine = self.trim();
  const home = localChannel.trim() || 'web';
  const local: AgentConversation = {
    id: `${home}:${mine}`,
    channel: home,
    person: mine,
    // The reader's own name is the surface's to say, not the store's: `owner` is
    // what the contact book calls them and is rarely what they are called.
    label: mine || channelLabel(home),
    messages: 0,
    lastAt: 0,
  };
  if (!found.has(local.id)) found.set(local.id, local);
  return [...found.values()].sort((left, right) => (
    right.lastAt - left.lastAt || left.id.localeCompare(right.id)
  ));
}

/**
 * Which channels a transcript actually holds.
 *
 * Derived from the messages rather than from a configured list, and that is the
 * point: a filter offering a channel nobody has spoken on is a filter that can
 * only ever produce an empty view, and a reader who picks it cannot tell "no
 * traffic" from "this door does not exist". `web` is always present because the
 * surface's own turns live there even before the agent has answered anything.
 */
export function channelsIn(messages: readonly ChatMessage[]): string[] {
  const seen = new Set<string>();
  for (const message of messages) {
    const channel = typeof message.channel === 'string' ? message.channel.trim() : '';
    if (channel) seen.add(channel);
  }
  seen.add('web');
  return [...seen].sort();
}

/**
 * A door the agent has open, and who it can be reached at.
 */
export interface AgentDoor {
  id: string;
  /**
   * Whether the deployment turned the door on, and whether it will admit anybody.
   *
   * Two facts rather than one because they fail differently and a reader cannot
   * tell them apart from outside: a door that is off is a setting, while a door
   * that is on with an empty allowlist *looks* configured and answers nobody.
   * Sending into one produces a question the agent deliberates about and a reply
   * that is discarded at the last step, so a surface has to be able to say so
   * before the send rather than after the silence.
   */
  enabled: boolean;
  admits: boolean;
  /**
   * Where this door reaches each person it knows.
   *
   * Reported rather than derivable on this side: a channel name is not a
   * destination. Telegram and WhatsApp address people in their own namespaces
   * (`tg:<chat id>`, `wa:<number>`), so "talk to owner through Telegram" is only a
   * complete instruction if somebody supplies that address — and the deployment
   * is where it lives.
   */
  contacts: AgentContact[];
}

export interface AgentContact {
  person: string;
  address: string;
}

/**
 * One credential a door cannot start without, as a form may see it.
 *
 * Never the value. A page that could read `TELEGRAM_BOT_TOKEN` would be a page
 * that could post it somewhere, so what comes back is where one is — the four
 * characters at the end, enough to tell the token typed an hour ago from the one
 * just pasted — and the *name* of the variable it may instead live in.
 *
 * `envPresent` is a separate fact from `present` and both are needed. `env` is
 * what a hand-edited file asked for; `envPresent` is whether the process serving
 * the agent can actually read it, and the two disagreeing is the normal way for
 * an operator to have a working agent and a form that says it is not configured.
 */
export interface AgentDoorCredential {
  key: string;
  label: string;
  env: string;
  present: boolean;
  hint: string;
  envPresent: boolean;
}

/**
 * A door, and everything a person needs in order to open it.
 *
 * All of them, not only the ones already open: the doors worth setting up are
 * exactly the doors that are shut, so a view built from the open ones could not
 * answer the question it exists for — "where does the Telegram token go".
 */
export interface AgentDoorSetup {
  id: string;
  enabled: boolean;
  credentials: AgentDoorCredential[];
  /** The ids this door admits, and the field they live in. Empty for the receiver. */
  allowlist: string;
  allowed: string[];
  /** Whether the allowlist admits anybody — the fact that a filled-in form still may not. */
  admits: boolean;
  /**
   * Whether this door answers anybody who writes, allowlist or not.
   *
   * A third answer alongside `admits`, not a variant of it. An empty allowlist
   * means "nobody is configured yet" on a fresh install and means the same on a
   * deployment that deliberately wants it that way, so it cannot double as a
   * switch — and reading it as one would open every door that was set up but not
   * yet filled in.
   */
  acceptFromAnyone: boolean;
  /** What an address on this door looks like, which is what the adapters refuse anything else for. */
  prefix: string;
  address: string;
  /** Whether this door is only reachable through the shared webhook receiver. */
  needsWebhook: boolean;
  webhookOn: boolean;
  /** Where a push door is declared, for a tunnel to forward to. */
  path?: string;
  /** The receiver's address, reported for the doors that need one. */
  host?: string;
  port?: number;
}

/** Every door, and the person the addresses belong to. */
export interface AgentChannelSetup {
  person: string;
  /** Where an interface writes, so a person who cannot find a field can look. */
  file: string;
  doors: AgentDoorSetup[];
}

/** What a door write may change. An absent field is left exactly as it is. */
export interface AgentDoorWrite {
  id: string;
  person?: string;
  enabled?: boolean;
  /**
   * A credential by key. An empty string keeps the one on file — a form that has
   * never held a credential must be able to save a door without clearing it —
   * and `null` is the instruction to remove it.
   */
  credentials?: Record<string, string | null>;
  allowed?: string[];
  /**
   * Whether this door answers anybody who writes.
   *
   * Omitted means "leave it as it is", like every other field here: a form
   * saving a token must not silently close a door that was open to the public.
   */
  acceptFromAnyone?: boolean;
  address?: string | null;
}

/**
 * The doors, as the bridge reports them.
 *
 * Total, like every reader here: a route that is missing, or answers with a
 * stranger's shape, yields a setup with no doors rather than throwing. A form
 * with nothing to show can say "the agent could not be asked", which is
 * recoverable; a form that crashed is not. The receiver is *not* invented here
 * the way the local line is in `readAgentDoors` — it is a configured thing, and a
 * setup that offered one to a deployment that has none would be offering a
 * second port to forward for nothing.
 */
export function readAgentChannelSetup(value: unknown): AgentChannelSetup {
  const setup = record(record(value).setup ?? value);
  return {
    person: text(setup.person, LIMITS.code).trim() || 'owner',
    file: text(setup.file, LIMITS.endpoint).trim(),
    doors: list(setup.doors)
      .map((entry): AgentDoorSetup | null => {
        const door = record(entry);
        const id = text(door.id, LIMITS.code).trim();
        if (!id) return null;
        return {
          id,
          enabled: door.enabled === true,
          credentials: list(door.credentials)
            .map((entry): AgentDoorCredential | null => {
              const credential = record(entry);
              const key = text(credential.key, LIMITS.code).trim();
              return key
                ? {
                    key,
                    label: text(credential.label, LIMITS.code).trim() || key,
                    env: text(credential.env, LIMITS.code).trim(),
                    present: credential.present === true,
                    hint: text(credential.hint, LIMITS.code).trim(),
                    envPresent: credential.env_present === true,
                  }
                : null;
            })
            .filter((credential): credential is AgentDoorCredential => credential !== null)
            .slice(0, 8),
          allowlist: text(door.allowlist, LIMITS.code).trim(),
          allowed: list(door.allowed)
            .map((value) => text(value, LIMITS.code).trim())
            .filter(Boolean)
            .slice(0, LIMITS.thoughts),
          admits: door.admits === true,
          acceptFromAnyone: door.accept_from_anyone === true,
          prefix: text(door.prefix, LIMITS.code).trim(),
          address: text(door.address, LIMITS.id).trim(),
          needsWebhook: door.needs_webhook === true,
          webhookOn: door.webhook_on === true,
          ...(typeof door.host === 'string' ? { host: text(door.host, LIMITS.id).trim() } : {}),
          ...(typeof door.path === 'string' ? { path: text(door.path, LIMITS.id).trim() } : {}),
          ...(Number.isInteger(door.port) ? { port: Number(door.port) } : {}),
        };
      })
      .filter((door): door is AgentDoorSetup => door !== null),
  };
}

/** The setups a door write left behind, read out of the write's own answer. */
export function readAgentDoorWrite(value: unknown): AgentChannelSetup {
  return readAgentChannelSetup(value);
}

/**
 * The doors the agent has open, as the bridge reports them.
 *
 * Total, like every reader here: a route that is missing, or answers with a
 * stranger's shape, yields the local line alone. That is the honest floor rather
 * than an empty list, because the local line is the one door whose being open is a
 * property of the bridge rather than of a config file somebody has to edit.
 *
 * A contact is dropped when it names nobody or has no address, because a door
 * listed with a contact that cannot be written to is a door a surface will offer
 * and then fail on.
 */
export function readAgentDoors(value: unknown): AgentDoor[] {
  const doors = list(record(value).channels)
    .map((entry): AgentDoor | null => {
      const door = record(entry);
      const id = text(door.id, LIMITS.code).trim();
      if (!id) return null;
      return {
        id,
        enabled: door.enabled !== false,
        admits: door.admits !== false,
        contacts: list(door.contacts)
          .map((entry): AgentContact | null => {
            const contact = record(entry);
            const person = text(contact.person, LIMITS.code).trim();
            const address = text(contact.address, LIMITS.code).trim();
            return person && address ? { person, address } : null;
          })
          .filter((contact): contact is AgentContact => contact !== null)
          .slice(0, LIMITS.code),
      };
    })
    .filter((door): door is AgentDoor => door !== null);
  const local: AgentDoor = { id: 'web', enabled: true, admits: true, contacts: [] };
  return doors.some((door) => door.id === local.id) ? doors : [local, ...doors];
}

/**
 * The doors a surface may actually send through, and the ones it may not.
 *
 * The union is what makes the two sources useful together. The bridge knows which
 * doors are configured, and the transcript knows which have been spoken on — and
 * each alone is a filter with a failure mode. Configured doors alone offer a
 * Telegram conversation that has never happened, which is how a browser becomes
 * the place a conversation *starts*; transcript doors alone offer nothing at all
 * until somebody happens to write in from a phone, which is the same feature with
 * the door shut.
 *
 * `closed` is the difference between those two answers: a door the agent has
 * spoken on and no longer has open. It is kept apart from the offered list so a
 * surface can keep the history visible while refusing to send into it, rather than
 * hiding one or silently accepting the other.
 */
export function reconcileDoors(
  configured: readonly AgentDoor[],
  spoken: readonly string[],
): { offered: AgentDoor[]; closed: string[] } {
  const spokenOn = new Set(spoken.map((channel) => channel.trim()).filter(Boolean));
  // Offered is a property of the configuration alone, so a door nobody has used
  // yet is offered exactly like one that has — which is the whole reason this is
  // not derived from the transcript.
  const offered = configured
    .filter((door) => door.enabled && door.admits)
    .sort((a, b) => a.id.localeCompare(b.id));
  const open = new Set(offered.map((door) => door.id));
  // Closed is the difference, which is what makes one list enough. A door the
  // transcript holds and the configuration does not offer is closed whether it was
  // turned off, emptied of its allowlist, or removed outright — the three look the
  // same from outside and are the same thing to the reader, who can no longer send
  // through it and would otherwise have to infer that from silence.
  const closed = [...spokenOn].filter((channel) => !open.has(channel)).sort();
  return { offered, closed };
}

/**
 * A channel named the way a reader would say it.
 *
 * The raw value is what the store holds, and it is a deployment's word for a
 * door rather than a word for a person: `whatsapp` reads as a vendor's name in
 * the store and as something a person says in a filter. An unrecognized name is
 * passed through rather than replaced — this program does not know every channel
 * a deployment adds, and showing a stranger's own name back beats inventing one.
 */
export function channelLabel(channel: string): string {
  const known: Record<string, string> = {
    web: 'Web',
    cli: 'TUI',
    telegram: 'Telegram',
    whatsapp: 'WhatsApp',
    slack: 'Slack',
    discord: 'Discord',
    email: 'Email',
    // The shared receiver, which is not a door anybody talks on and is still
    // something a person has to be able to name while setting up WhatsApp or
    // Slack: it is the other half of both of them.
    webhook: 'Webhook',
  };
  const raw = channel.trim();
  return known[raw.toLowerCase()] ?? raw;
}

/**
 * The door a reader named, and why not when there is not one.
 *
 * Case-insensitive on purpose: a terminal has no dropdown to click, so
 * `/channel Telegram` has to be the same answer as `/channel telegram`, and a
 * reader who typed the name they see on screen should not be told it does not
 * exist.
 *
 * A miss is answered with the names that would have worked, because "unknown
 * channel" on its own leaves the only question a reader has — *which* — with no
 * answer. The offered list is the deployment's own, not a table this file
 * happens to know: a door added by configuration is as nameable as a built-in
 * one, and a kit that only knew its own vocabulary would refuse it.
 */
export function findDoor(
  doors: readonly AgentDoor[],
  wanted: string,
): { ok: true; door: AgentDoor } | { ok: false; reason: 'unknown'; offered: string[] } {
  const name = wanted.trim();
  const lowered = name.toLowerCase();
  const match = doors.find((door) => door.id.trim().toLowerCase() === lowered);
  if (match) return { ok: true, door: match };
  return {
    ok: false,
    reason: 'unknown',
    offered: [...new Set([...doors.map((door) => door.id), 'web'])].sort(),
  };
}

/**
 * Where one door reaches one person, or `null` when it cannot reach them.
 *
 * The address is not the person, and this is the one place on the surface side
 * that has to hold both: Telegram and WhatsApp post to a chat id and a phone
 * number, Discord and Slack to a channel, and `owner` is not a place any of them
 * can post to. A door with no address for the person who is speaking has nowhere
 * for the reply to go, and `null` is the answer that stops the send rather than
 * the one that fabricates a conversation — the same refusal the bridge makes, for
 * the same reason, at the layer below.
 *
 * The person is matched exactly rather than case-insensitively. `person_id` is
 * the key a contact book is written in, and a case-folded match would let a
 * surface address a person the deployment has never heard of.
 */
export function addressFor(door: AgentDoor | undefined, personId: string): string | null {
  if (!door) return null;
  const wanted = personId.trim();
  if (!wanted) return null;
  for (const contact of door.contacts) {
    if (contact.person === wanted) return contact.address;
  }
  return null;
}
