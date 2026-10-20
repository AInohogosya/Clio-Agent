import type { ProviderId } from './types.js';

export const MAX_API_KEY_LENGTH = 512;
/**
 * A credential a *channel* needs — a bot token, a signing secret, a mailbox
 * password.
 *
 * Named apart from the API key above because the longest of them is chosen
 * rather than issued: a WhatsApp verify token is whatever string the deployment
 * invents, and a form that capped it at an API key's length would refuse a value
 * the agent would have accepted. One number, used by the field and by the write
 * that stores it, so the two cannot disagree about what fits.
 */
export const MAX_CREDENTIAL_LENGTH = 1_024;
export const MAX_ENDPOINT_LENGTH = 2_048;
export const MAX_MODEL_LENGTH = 256;
export const MAX_PROMPT_LENGTH = 32_000;
export const MAX_MESSAGE_TEXT_LENGTH = 100_000;
export const MAX_TERMINAL_TEXT_LENGTH = 8_192;
/**
 * A channel name. Short because they are identifiers the deployment picks, not
 * prose: `telegram`, `whatsapp`, `tg:819012345678`. Long enough for a
 * `channel:person` key, short enough that nothing can hide in one.
 */
export const MAX_CHANNEL_LENGTH = 64;
/**
 * A destination address on one of those channels: a chat id, a phone number, a
 * Discord or Slack channel id.
 *
 * Longer than a channel name because the value is genuinely longer — a
 * `telegram:819012345678` is twenty-odd characters before the `dc:` or `slack:`
 * prefix is added — and capped because it reaches a request body that decides
 * where a message physically goes.
 */
export const MAX_ADDRESS_LENGTH = 128;
/**
 * The length of a name somebody gives the agent, matching the agent's own rule.
 *
 * A name is a person's own word in whatever script they write in, so the limit is
 * generous and there is no character class to enforce here — the refusal lives in the
 * agent, where the value is about to become part of a system prompt. What a form owes
 * the person is the same number the agent will accept, so a form cannot take a name
 * that is going to be refused as though it had been saved.
 */
export const MAX_AGENT_NAME_LENGTH = 64;
/**
 * A sender as one line to draw: the name the channel gave them, and the address
 * that door reaches them at.
 *
 * Longer than either half because it is both, and it is the whole of what a
 * surface can say about who wrote a message when the channel named them — so a
 * reader looking at a list of correspondents is reading these.
 */
export const MAX_SENDER_LABEL_LENGTH = 288;
/**
 * How much of a reply is kept. Deliberately larger than the prompt budget: a
 * model may be asked one short question and answer at length, and capping the
 * answer at the size of the question is a limit nobody chose.
 */
export const MAX_RESPONSE_TEXT_LENGTH = 200_000;
export const MAX_RESPONSE_BYTES = 2_000_000;
export const MAX_MODEL_COUNT = 500;
export const MAX_TIMESTAMP = 4_102_444_800_000;

function isStoredControl(character: string): boolean {
  const code = character.codePointAt(0) ?? 0;
  return code <= 0x08
    || (code >= 0x0b && code <= 0x0c)
    || (code >= 0x0e && code <= 0x1f)
    || (code >= 0x007f && code <= 0x009f)
    || code === 0x2028
    || code === 0x2029
    || (code >= 0x202a && code <= 0x202e)
    || (code >= 0x2066 && code <= 0x2069);
}

function isTerminalControl(character: string): boolean {
  const code = character.codePointAt(0) ?? 0;
  return code <= 0x1f
    || (code >= 0x7f && code <= 0x9f)
    || (code >= 0x2028 && code <= 0x2029)
    || (code >= 0x202a && code <= 0x202e)
    || (code >= 0x2066 && code <= 0x2069);
}

function isCredentialControl(character: string): boolean {
  const code = character.codePointAt(0) ?? 0;
  return code <= 0x1f
    || (code >= 0x7f && code <= 0x9f)
    || code === 0x2028
    || code === 0x2029
    || (code >= 0x202a && code <= 0x202e)
    || (code >= 0x2066 && code <= 0x2069);
}

function hasControl(value: string, predicate: (character: string) => boolean): boolean {
  for (const character of value) {
    if (predicate(character)) return true;
  }
  return false;
}

function removeControl(value: string, predicate: (character: string) => boolean): string {
  let result = '';
  for (const character of value) {
    if (!predicate(character)) result += character;
  }
  return result;
}

export function isBoundedString(value: unknown, maxLength: number, allowEmpty = true): value is string {
  return typeof value === 'string'
    && value.length <= maxLength
    && (allowEmpty || value.length > 0)
    && !hasControl(value, isStoredControl);
}

export function isSafeCredential(value: unknown): value is string {
  return typeof value === 'string' && value.length <= MAX_API_KEY_LENGTH && !hasControl(value, isCredentialControl);
}

export function isSafeIdentifier(value: unknown, maxLength: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength && !hasControl(value, isCredentialControl);
}

export function isSafeModelName(value: unknown): value is string {
  return isSafeIdentifier(value, MAX_MODEL_LENGTH);
}

/** Model ids as vendors actually write them, with no whitespace to carry. */
const PROVIDER_MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$/;

/**
 * A model name both ends will accept.
 *
 * {@link isSafeModelName} answers "is this safe to store", which is a weaker
 * question than "will this be sent". A name with a space in it is perfectly
 * storable and is refused by every provider, and by the agent's own base-model
 * reader — so checking only the weaker rule here would mean a form that accepted
 * a name and then reported the agent's refusal of it as an unexpected error.
 *
 * The character class is the one the agent's configuration loader applies, kept
 * identical on purpose: two lists of what a model name may contain is one more
 * than there should be.
 */
export function isProviderModelName(value: unknown): value is string {
  return typeof value === 'string' && PROVIDER_MODEL_NAME.test(value);
}

/**
 * The storage-flavoured name for {@link isBoundedString}: text that is short
 * enough and free of the control characters that would corrupt a stored value
 * or a terminal. `isBoundedString` already applies exactly that rule, so this is
 * a name rather than a second opinion — the two must not drift apart.
 */
export function isSafeStoredText(value: unknown, maxLength: number): value is string {
  return isBoundedString(value, maxLength);
}

export function isSafeTimestamp(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= MAX_TIMESTAMP;
}

export function sanitizeStoredText(value: string, maxLength: number): string {
  if (typeof value !== 'string') return '';
  const normalized = value.replace(/\r\n?/g, '\n');
  const cleaned = removeControl(normalized, isStoredControl);
  return cleaned.length > maxLength ? cleaned.slice(0, maxLength) : cleaned;
}

export function sanitizeTerminalText(value: string, maxLength = MAX_TERMINAL_TEXT_LENGTH): string {
  if (typeof value !== 'string') return '';
  const normalized = value.replace(/\r\n?/g, '\n');
  const cleaned = removeControl(normalized, isTerminalControl);
  return cleaned.length > maxLength ? cleaned.slice(0, maxLength) : cleaned;
}

function ipv4Parts(hostname: string): number[] | null {
  const parts = hostname.split('.');
  if (parts.length !== 4) return null;
  const numbers = parts.map((part) => Number(part));
  if (numbers.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return null;
  return numbers;
}

function isPrivateIpv4(hostname: string): boolean {
  const parts = ipv4Parts(hostname);
  if (!parts) return false;
  const [first, second, third] = parts;
  return first === 0
    || first === 10
    || first === 127
    || (first === 100 && second >= 64 && second <= 127)
    || (first === 169 && second === 254)
    || (first === 172 && second >= 16 && second <= 31)
    || (first === 192 && second === 168)
    || (first === 192 && second === 0 && (third === 0 || third === 2))
    || (first === 198 && (second === 18 || second === 19 || (second === 51 && third === 100)))
    || (first === 203 && second === 0 && third === 113)
    || first >= 224;
}

function normalizedHostname(hostname: string): string {
  return hostname.replace(/^\[|\]$/g, '').split('%')[0].replace(/\.$/, '').toLowerCase();
}

function ipv6Parts(hostname: string): number[] | null {
  let host = normalizedHostname(hostname);
  if (!host.includes(':')) return null;
  if (host.includes('.')) {
    const lastColon = host.lastIndexOf(':');
    const embeddedIpv4 = ipv4Parts(host.slice(lastColon + 1));
    if (!embeddedIpv4) return null;
    const high = ((embeddedIpv4[0] << 8) | embeddedIpv4[1]).toString(16);
    const low = ((embeddedIpv4[2] << 8) | embeddedIpv4[3]).toString(16);
    host = `${host.slice(0, lastColon)}:${high}:${low}`;
  }
  const doubleColon = host.indexOf('::');
  if (doubleColon !== -1) {
    if (host.indexOf('::', doubleColon + 1) !== -1) return null;
    const left = host.slice(0, doubleColon).split(':').filter(Boolean);
    const right = host.slice(doubleColon + 2).split(':').filter(Boolean);
    const missing = 8 - left.length - right.length;
    if (missing < 1) return null;
    const values = [...left, ...Array.from({ length: missing }, () => '0'), ...right];
    const numbers = values.map((part) => Number.parseInt(part, 16));
    return numbers.every((part) => Number.isInteger(part) && part >= 0 && part <= 0xffff) ? numbers : null;
  }
  const values = host.split(':');
  if (values.length !== 8) return null;
  const numbers = values.map((part) => Number.parseInt(part, 16));
  return numbers.every((part) => Number.isInteger(part) && part >= 0 && part <= 0xffff) ? numbers : null;
}

function mappedIpv4(parts: number[]): string | null {
  if (parts.length !== 8) return null;
  const mapped = parts.slice(0, 5).every((part) => part === 0) && parts[5] === 0xffff;
  const compatible = parts.slice(0, 6).every((part) => part === 0);
  if (!mapped && !compatible) return null;
  return `${parts[6] >> 8}.${parts[6] & 0xff}.${parts[7] >> 8}.${parts[7] & 0xff}`;
}

/**
 * The single definition of "this host is the local machine". Shared by the
 * endpoint validator and the local bridge so the two cannot disagree about
 * which addresses count as loopback.
 */
export function isLoopbackHost(hostname: string): boolean {
  const host = normalizedHostname(hostname);
  if (host === 'localhost' || host.endsWith('.localhost')) return true;
  const parts = ipv4Parts(host);
  if (parts?.[0] === 127) return true;
  const ipv6 = ipv6Parts(host);
  if (!ipv6) return false;
  if (ipv6.slice(0, 7).every((part) => part === 0) && ipv6[7] === 1) return true;
  const mapped = mappedIpv4(ipv6);
  return mapped ? isLoopbackHost(mapped) : false;
}

function isPrivateHost(hostname: string): boolean {
  const host = normalizedHostname(hostname);
  if (isLoopbackHost(host)) return true;
  if (host === 'local' || host.endsWith('.local') || host === 'internal' || host.endsWith('.internal')) return true;
  if (host === 'localtest.me' || host.endsWith('.localtest.me') || host === 'lvh.me' || host.endsWith('.lvh.me')) return true;
  if (host === '127.0.0.1.nip.io' || host.endsWith('.nip.io') || host.endsWith('.sslip.io')) return true;
  const ipv4 = ipv4Parts(host);
  if (ipv4) return isPrivateIpv4(host);
  const ipv6 = ipv6Parts(host);
  if (!ipv6) return false;
  if (ipv6.every((part) => part === 0)) return true;
  if (ipv6.slice(0, 6).every((part) => part === 0)) return true;
  const first = ipv6[0] ?? 0;
  const second = ipv6[1] ?? 0;
  if ((first & 0xfe00) === 0xfc00) return true;
  if ((first & 0xffc0) === 0xfe80 || (first & 0xffc0) === 0xfec0) return true;
  if ((first & 0xff00) === 0xff00) return true;
  if (first === 0x0064 && second === 0xff9b) return true;
  if (first === 0x2002) return true;
  if (first === 0x2001 && second === 0x0000) return true;
  if (first === 0x2001 && second === 0x0db8) return true;
  const mapped = mappedIpv4(ipv6);
  return mapped ? isPrivateIpv4(mapped) : false;
}

export function isValidProviderEndpoint(value: unknown, provider: ProviderId): boolean {
  if (!isBoundedString(value, MAX_ENDPOINT_LENGTH) || !value.trim()) return false;
  if (hasControl(value, isStoredControl)) return false;
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    return false;
  }
  if (url.protocol !== 'https:' && url.protocol !== 'http:') return false;
  if (url.username || url.password || url.search || url.hash) return false;
  const hostname = url.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  if (!hostname) return false;
  const loopback = isLoopbackHost(hostname);
  const localProvider = provider === 'ollama' || provider === 'lmstudio';
  if (url.protocol === 'http:' && !localProvider) return false;
  if (localProvider && url.protocol === 'http:' && !loopback) return false;
  if (isPrivateHost(hostname) && !(localProvider && loopback)) return false;
  return true;
}

/**
 * The agent's interface service, which is local by definition.
 *
 * It carries the whole conversation and the whole of the agent's state, and it
 * takes unauthenticated commands by design — the agent trusts its own owner, the
 * way it trusts a terminal on the same machine. That is only a defensible
 * position while the address is genuinely this machine, so loopback is required
 * and plain `http` is required with it: there is no remote agent service to
 * reach, and accepting a remote origin would be accepting a place to forward
 * everything the agent thinks.
 *
 * A path is refused for the same reason the local bridge refuses one: the client
 * composes its own routes under the origin, and a base carrying `/somewhere`
 * would silently produce addresses nobody asked for.
 */
export function isValidAgentEndpoint(value: unknown): boolean {
  if (!isBoundedString(value, MAX_ENDPOINT_LENGTH) || !value.trim()) return false;
  if (hasControl(value, isStoredControl)) return false;
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    return false;
  }
  if (url.protocol !== 'http:') return false;
  if (url.username || url.password || url.search || url.hash) return false;
  if (url.pathname !== '/' && url.pathname !== '') return false;
  const hostname = url.hostname.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase();
  return hostname.length > 0 && isLoopbackHost(hostname);
}
