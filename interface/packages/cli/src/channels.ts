import {
  addressFor,
  channelLabel,
  createTranslator,
  findDoor,
  sanitizeTerminalText,
  type AgentDoor,
  type ChatMessage,
  type Language,
  type TranslationKey,
} from '@project-phone/core';
import { CliError, type ErrorCode } from './args.js';
import { describeError } from './errors.js';
import { padWidth, truncateWidth, type Palette } from './palette.js';
import { keyValue, note, sectionLabel, type RenderOptions } from './render.js';

/**
 * The doors a terminal can talk through, and how one of them is chosen.
 *
 * The terminal had none of this, and it was the half of the interface that could
 * reach the agent by no door but the local one. Everything here is the vocabulary
 * the browser already uses — `AgentDoor`, `channelLabel`, `reconcileDoors` —
 * because a deployment with Telegram open is one fact, and two surfaces naming it
 * differently would be two vocabularies for one agent.
 */

/** The doors that deliver to whoever is reading, so they need no address. */
const LOCAL_DOORS = new Set(['web', 'cli']);

/**
 * Whether a door has to be told where a person is before it can answer them.
 *
 * The same split the bridge makes, restated here because the refusal has to
 * happen *before* a question is queued rather than after a deliberation whose
 * answer is thrown away. A reader who typed a question into a door the deployment
 * has no address for is owed that answer in one sentence, not a question, a long
 * pause, and silence.
 */
export function doorNeedsAddress(channel: string): boolean {
  return !LOCAL_DOORS.has(channel.trim().toLowerCase());
}

export interface DoorChoice {
  ok: true;
  /** The door as the deployment spells it, which is what a send must name. */
  channel: string;
  /** Where that door reaches this person, or `null` for a local door. */
  address: string | null;
}

export interface DoorRefusal {
  ok: false;
  code: ErrorCode;
  values: Record<string, string | number>;
}

/**
 * The door a `--channel` named, and where it reaches this person.
 *
 * Every refusal here is one a reader can act on, and that is the whole design:
 *
 *   - a name that is not open answers with the names that are, because "unknown
 *     channel" with no list leaves the only question a reader has — *which* —
 *     unanswered;
 *   - a door that is on but whose allowlist is empty is refused, because it looks
 *     configured and answers nobody, so a question sent into it is a question
 *     the agent deliberates on and the reader never gets answered;
 *   - a door that needs an address and has none for this person is refused before
 *     the send rather than after it.
 *
 * A local door resolves with no address at all, which is what it means to be
 * local: the transcript is the destination and the person is the address.
 */
export function resolveDoor(
  doors: readonly AgentDoor[],
  personId: string,
  wanted?: string,
  to?: string,
): DoorChoice | DoorRefusal {
  const name = wanted?.trim() ?? '';
  if (!name) {
    // No `--channel` means the local line, and the local line is the only door
    // that always works: it needs nothing configured and nothing looked up.
    return { ok: true, channel: 'web', address: to?.trim() || null };
  }
  const found = findDoor(doors, name);
  if (!found.ok) {
    return {
      ok: false,
      code: 'channel_unknown',
      values: { name, offered: found.offered.join(', ') },
    };
  }
  const door = found.door;
  if (!door.admits) {
    return { ok: false, code: 'channel_closed', values: { channel: channelLabel(door.id) } };
  }
  const address = to?.trim() || addressFor(door, personId);
  if (!address && doorNeedsAddress(door.id)) {
    return {
      ok: false,
      code: 'channel_unaddressed',
      values: { person: personId, channel: channelLabel(door.id) },
    };
  }
  return { ok: true, channel: door.id, address };
}

/** The refusal as the `CliError` the one-shot commands report. */
export function doorRefusalError(refusal: DoorRefusal): CliError {
  return new CliError(refusal.code, refusal.values);
}

/** The refusal as the sentence a reader sees. */
export function doorRefusalText(refusal: DoorRefusal, language: Language): string {
  return describeError(refusal.code, language, refusal.values);
}

/**
 * The doors a deployment has open, and the ones a transcript holds that it does
 * not.
 *
 * The difference is the part a reader cannot work out on their own: a door the
 * transcript holds and the deployment no longer offers is *closed* — a
 * conversation happened there and the agent can no longer be spoken to on it.
 * Those are listed rather than hidden, because "nothing has been said" and "you
 * cannot speak there any more" look identical from outside a filter.
 */
export function doorSummary(doors: readonly AgentDoor[], messages: readonly ChatMessage[]): {
  offered: AgentDoor[];
  closed: string[];
} {
  const spoken = new Set<string>();
  for (const message of messages) {
    const channel = typeof message.channel === 'string' ? message.channel.trim() : '';
    if (channel) spoken.add(channel);
  }
  const offered = doors.filter((door) => door.enabled && door.admits);
  const open = new Set(offered.map((door) => door.id));
  return {
    offered: [...offered].sort((a, b) => a.id.localeCompare(b.id)),
    closed: [...spoken].filter((channel) => !open.has(channel)).sort(),
  };
}

const LABEL_WIDTH = 14;

/**
 * `phone channels` — what the agent can be reached on, and where.
 *
 * Three facts per door, and each is one a reader is about to need: whether it is
 * open, whether it will answer anybody, and whom it can actually post to. The
 * third is what turns the second from a warning into something to act on — a door
 * that admits people and knows no address for *you* is still a door you cannot
 * use, and saying so here is cheaper than finding out after a deliberation.
 */
export function channelsScreen(
  doors: readonly AgentDoor[],
  current: string,
  options: RenderOptions,
  closed: readonly string[] = [],
): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const lines: string[] = [];
  lines.push('');
  lines.push(`  ${sectionLabel(t('cliChannelsTitle'), palette)}`);
  if (doors.length === 0) {
    lines.push(`  ${note(t('cliChannelsEmpty'), palette, 'warning')}`);
  }
  for (const door of doors) {
    const label = channelLabel(door.id);
    const inUse = door.id === current;
    const mark = inUse ? palette.accentText('●') : door.admits ? palette.good('●') : palette.warn('○');
    // The raw id beside the label, because the label is a word a reader says and
    // the id is the word the config file and `--channel` use. A reader who has to
    // open a file to discover what to type has been asked to do the tool's job.
    const state = inUse
      ? `${mark} ${palette.accentText(t('cliChannelsCurrent'))}`
      : `${mark} ${palette.faint(door.id)}`;
    lines.push(keyValue(label, state, palette, width, LABEL_WIDTH));
    if (!door.admits) {
      lines.push(keyValue('', palette.warn(t('cliChannelsAdmitsNobody')), palette, width, LABEL_WIDTH));
      continue;
    }
    if (door.contacts.length === 0) {
      const none = doorNeedsAddress(door.id) ? t('cliChannelsNoAddress') : t('cliChannelsHere');
      lines.push(keyValue('', palette.faint(none), palette, width, LABEL_WIDTH));
      continue;
    }
    for (const contact of door.contacts) {
      lines.push(keyValue(
        '',
        `${palette.muted(contact.person)} ${palette.faint('→')} ${palette.inkText(contact.address)}`,
        palette,
        width,
        LABEL_WIDTH,
      ));
    }
  }
  if (closed.length > 0) {
    lines.push('');
    // The label is a noun and the sentence underneath it. Using the sentence as
    // the column heading truncated it into nonsense ("This channel …"), which is
    // worse than either: it named neither the fact nor the doors it was about.
    lines.push(keyValue(
      t('cliChannelsClosedTitle'),
      palette.faint(closed.map(channelLabel).join(', ')),
      palette,
      width,
      LABEL_WIDTH,
    ));
    lines.push(`  ${palette.faint(truncateWidth(t('channelClosed'), width - 4))}`);
  }
  lines.push('');
  lines.push(`  ${note(t('cliChannelsSwitch'), palette)}`);
  lines.push(`  ${palette.faint(truncateWidth('phone send --channel <name> "…"', width))}`);
  // The same line the full-screen panel carries, and for the same reason: this
  // screen answers which doors are open, so a reader who finds none for Telegram
  // has to be told how one gets opened rather than left to conclude the agent
  // cannot be reached that way.
  lines.push(`  ${palette.faint(truncateWidth('phone channels set telegram.enabled true', width))}`);
  lines.push('');
  return lines;
}

/**
 * One door, as a single line.
 *
 * The TUI panel has a fixed width and no room for a table, so this is the whole
 * of what fits: which door, and whether it is the one replies are going out of.
 * The contacts and the refusals live in the composer's own line and in `phone
 * channels`, where there is space for them.
 */
export function doorLine(
  door: AgentDoor,
  current: boolean,
  palette: Palette,
  width: number,
): string {
  const mark = current ? palette.accentText('●') : door.admits ? palette.good('●') : palette.warn('○');
  const label = current ? palette.accentText(channelLabel(door.id)) : channelLabel(door.id);
  return `  ${mark} ${truncateWidth(label, Math.max(4, width - 6))}`;
}

/** One door and its state, for the TUI, where the reason has to fit on one line. */
export function doorStateNote(
  door: AgentDoor,
  personId: string,
  palette: Palette,
  width: number,
  t: (key: TranslationKey) => string,
): string {
  if (!door.admits) return truncateWidth(palette.warn(t('cliChannelsAdmitsNobody')), width);
  const address = addressFor(door, personId);
  if (!address) {
    return truncateWidth(
      palette.warn(doorNeedsAddress(door.id) ? t('cliChannelsNoAddress') : t('cliChannelsHere')),
      width,
    );
  }
  return truncateWidth(`${palette.muted(personId)} ${palette.faint('→')} ${palette.inkText(address)}`, width);
}

/**
 * What the bridge reported, as the plain data `--json` prints.
 *
 * `closed` is kept out of `channels` on purpose: a closed door is one the
 * deployment does not have open, so listing it among the channels would be
 * offering a reader a door the next command will refuse.
 */
export function channelsJson(
  doors: readonly AgentDoor[],
  current: string,
  closed: readonly string[] = [],
): Record<string, unknown> {
  return {
    current,
    channels: doors.map((door) => ({
      id: door.id,
      label: channelLabel(door.id),
      enabled: door.enabled,
      admits: door.admits,
      needs_address: doorNeedsAddress(door.id),
      contacts: door.contacts.map((contact) => ({ person: contact.person, address: contact.address })),
    })),
    closed: [...closed],
  };
}

/** The door's name, as the composer shows it. Sanitised like every other word. */
export function channelBadge(channel: string): string {
  return sanitizeTerminalText(channelLabel(channel), 24);
}

/** Pads a door name to a column, for callers that lay several out themselves. */
export function padDoorLabel(value: string, width: number): string {
  return padWidth(truncateWidth(value, width), width);
}
