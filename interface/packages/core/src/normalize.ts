import {
  MAX_ADDRESS_LENGTH,
  MAX_CHANNEL_LENGTH,
  MAX_MESSAGE_TEXT_LENGTH,
  MAX_SENDER_LABEL_LENGTH,
  isSafeIdentifier,
  isSafeStoredText,
  isSafeTimestamp,
  sanitizeStoredText,
} from './security.js';
import { createSettings, type ChatMessage, type Settings } from './types.js';

/**
 * Reading settings and a transcript out of something untrusted.
 *
 * The shared file and a payload from the local bridge both arrive as `unknown`,
 * and are answered here with the one normalised shape. A field that is missing
 * or unusable becomes its default rather than an error: a state that cannot be
 * understood still has to be a state, because the alternative is a surface with
 * nothing to render and nowhere to write.
 *
 * Nothing here decides *where* state is kept. The file is the only store, and
 * what may be written into it is `store.ts`'s decision, not this module's.
 */

function hasOwn(value: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(value, key);
}

/**
 * A channel name, or nothing.
 *
 * Bounded and stripped rather than validated against a list. The set of channels
 * grows with the deployment, so a reader that refused an unknown name would
 * report a live conversation as having no channel — and would then filter it
 * out of a view the person is looking at. Anything unprintable becomes nothing,
 * which reads as the local line.
 */
function safeChannel(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const clean = value.trim().slice(0, MAX_CHANNEL_LENGTH);
  if (!/^[\w:.-]+$/.test(clean)) return undefined;
  return clean || undefined;
}

/**
 * The address of a person, or nothing.
 *
 * Not pattern-checked the way a channel is, because an address is the door's
 * own business: a `tg:` chat id, an E.164 number, a Slack channel id, none of
 * which this package can enumerate, and a reader that refused the ones it did
 * not recognise would drop half the conversations a deployment actually has.
 * What is refused is whitespace and control characters, which are never part of
 * an address and are what a line of a name would be smuggled in as.
 */
function safePerson(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const clean = value.trim().slice(0, MAX_ADDRESS_LENGTH);
  if (!clean || /\s/.test(clean) || /[\u0000-\u001f\u007f]/.test(clean)) return undefined;
  return clean;
}

/**
 * Who a turn is from, as one line, or nothing.
 *
 * Carried for the same reason the address is and because it is the only half of
 * the answer a reader can use: the address tells two correspondents apart, the
 * name tells a person which of them is standing in front of them. Dropped
 * whitespace is collapsed rather than refused, so a name that arrived with a
 * newline in it is still read as a name instead of silently becoming no name at
 * all.
 */
function safeAuthor(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const clean = sanitizeStoredText(value, MAX_SENDER_LABEL_LENGTH).replace(/\s+/g, ' ').trim();
  return clean || undefined;
}

function validMessage(value: unknown): value is ChatMessage {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const item = value as Partial<ChatMessage>;
  return hasOwn(item, 'id')
    && hasOwn(item, 'text')
    && hasOwn(item, 'createdAt')
    && hasOwn(item, 'role')
    && isSafeIdentifier(item.id, 128)
    && isSafeStoredText(item.text, MAX_MESSAGE_TEXT_LENGTH)
    && isSafeTimestamp(item.createdAt)
    && (item.role === 'user' || item.role === 'assistant' || item.role === 'system');
}

/**
 * The transcript as it may be used: every entry is a real message, every field
 * is bounded, and the result is never longer than the shared state allows.
 */
export function normalizeMessages(value: unknown): ChatMessage[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(validMessage)
    .slice(-100)
    .map((message) => ({
      id: message.id,
      role: message.role,
      text: sanitizeStoredText(message.text, MAX_MESSAGE_TEXT_LENGTH),
      createdAt: message.createdAt,
      // Carried through the shared file, so a channel survives a restart. Only
      // added when there is one: `undefined` is not written, so a provider-direct
      // transcript stays byte-identical to what it was before channels existed.
      ...(safeChannel(message.channel) ? { channel: safeChannel(message.channel) } : {}),
      // And so does the address of the person the turn is with, for the same
      // reason and with the same rule. This is what lets a surface answer "who
      // wrote this" from its own transcript: drop it here and every arrival is
      // folded back into one list, which is the reading that made four
      // correspondents look like one person talking to themselves.
      ...(safePerson(message.person) ? { person: safePerson(message.person) } : {}),
      // The name that address goes with, kept through the same door. It is what
      // the agent worked out from the channel — `Ada Lovelace (@ada, tg:1)` —
      // and it is not recomputed here, because the channel's own spelling of a
      // person is the one they recognise.
      ...(safeAuthor(message.author) ? { author: safeAuthor(message.author) } : {}),
    }));
}

/**
 * Settings read from anywhere but the code that wrote them — a file edited by
 * hand, a snapshot from the bridge — returned as the one valid shape.
 * `createSettings` is that normaliser; this name is kept because callers read
 * better when they are describing an act of validation.
 */
export function normalizeSettings(value: unknown): Settings {
  return createSettings(value as Partial<Settings>);
}
