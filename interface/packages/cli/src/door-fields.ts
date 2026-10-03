import type { AgentChannelSetup, AgentDoorSetup, AgentDoorWrite } from '@project-phone/core';
import { CliError } from './args.js';

/**
 * `phone channels set` in one place: which field a name means, what a value may
 * be, and what a field reads back as.
 *
 * The fields are not a table here. They are whatever the agent reports its doors
 * having, because a hard-coded list is a second answer to "which fields does this
 * deployment have" and the day a door gains a credential this file does not, the
 * terminal would refuse a name the browser accepts. A name the agent does not
 * know is refused with the names it does.
 *
 * A credential is the one field that cannot be *set* from here, and that is the
 * rule `phone config` already applies to an API key: a secret on a command line
 * is in the shell history and in the process list. Removing one is allowed,
 * because removal says nothing. So the two ways to put a token in are the
 * settings screen and the variable the agent already reads — and `get` says
 * which of the two this machine has.
 */

export const DOOR_FIELDS = ['enabled', 'acceptFromAnyone', 'allowed', 'address'] as const;

/**
 * The fields a door has: the four every door has, plus whatever credentials
 * that one needs. Read off the agent's own answer rather than kept here, so a
 * door that gains a credential is a door this surface knows about on the next
 * read.
 *
 * `address` and `acceptFromAnyone` are the two that are conditional, and both are
 * conditional on the door rather than on taste: the shared receiver posts to
 * nothing and answers to nobody, so a field for either would be a command that
 * reported a save which changed nothing. They are absent from the table, and an
 * absent field is refused.
 */
export function doorFields(door: AgentDoorSetup): string[] {
  const plain = DOOR_FIELDS.filter((field) => {
    // `address` needs somewhere to reach the person and `acceptFromAnyone` needs
    // a door that admits people at all. Both are answered from the door's own
    // reported shape rather than from a list of exceptions, so a door that gains
    // or loses an allowlist gets this right without this file being told.
    if (field === 'address') return Boolean(door.prefix);
    if (field === 'acceptFromAnyone') return Boolean(door.allowlist);
    return true;
  });
  return [...plain, ...door.credentials.map((entry) => entry.key)];
}

/** The door and field a `door.field` name means, or a refusal naming what does. */
export function readDoorTarget(
  setup: AgentChannelSetup,
  target: string,
): { door: AgentDoorSetup; field: string } {
  const wanted = target.trim();
  const cut = wanted.indexOf('.');
  if (cut <= 0) throw new CliError('unknown_field', { field: wanted });
  const doorId = wanted.slice(0, cut).trim().toLowerCase();
  const field = wanted.slice(cut + 1).trim();
  const door = setup.doors.find((entry) => entry.id.toLowerCase() === doorId);
  if (!door) {
    // The same values `channels.ts` refuses an unknown door with, so a reader who
    // mistyped one name is given the same sentence on both paths. `{name}` and
    // `{offered}` are the placeholders that sentence has.
    throw new CliError('channel_unknown', {
      name: doorId,
      offered: setup.doors.map((entry) => entry.id).join(', '),
    });
  }
  if (!field || !doorFields(door).includes(field)) {
    throw new CliError('unknown_field', { field: wanted });
  }
  return { door, field };
}

/**
 * One `set`, as a write for the agent.
 *
 * Only the named field is in it, because a save that mentioned the others would
 * clear them: the person changing an allowlist must not lose the token beside it
 * because one command mentioned the wrong key.
 */
export function doorWrite(
  setup: AgentChannelSetup,
  target: string,
  value: string,
): AgentDoorWrite {
  const { door, field } = readDoorTarget(setup, target);
  if (door.credentials.some((entry) => entry.key === field)) {
    throw new CliError('door_secret_argument');
  }
  if (field === 'enabled' || field === 'acceptFromAnyone') {
    // The same grammar as `enabled`, and refused rather than coerced for the same
    // reason: `phone channels set telegram.acceptFromAnyone maybe` must not become
    // a door that answers the public because the word was not on a list.
    const on = /^(1|true|yes|on)$/i.test(value.trim());
    if (!on && !/^(0|false|no|off)$/i.test(value.trim())) {
      throw new CliError('invalid_value', { field: target });
    }
    return field === 'enabled'
      ? { id: door.id, person: setup.person, enabled: on }
      : { id: door.id, person: setup.person, acceptFromAnyone: on };
  }
  if (field === 'allowed') {
    return { id: door.id, person: setup.person, allowed: splitIds(value) };
  }
  // Checked here as well as on the agent's side, so a typo is a sentence rather
  // than a round trip — and the prefix is the one the adapters refuse to send to
  // anything else, so an address without it is a conversation that would be
  // deliberated on and then refused at the last step.
  const address = value.trim();
  if (door.prefix && !address.startsWith(door.prefix)) {
    throw new CliError('door_invalid_address', { prefix: door.prefix, field: target });
  }
  return { id: door.id, person: setup.person, address };
}

/** One `unset`, which for every field means "as a fresh install has it". */
export function doorClear(setup: AgentChannelSetup, target: string): AgentDoorWrite {
  const { door, field } = readDoorTarget(setup, target);
  if (door.credentials.some((entry) => entry.key === field)) {
    return { id: door.id, person: setup.person, credentials: { [field]: null } };
  }
  if (field === 'enabled') return { id: door.id, person: setup.person, enabled: false };
  if (field === 'acceptFromAnyone') {
    // A fresh install's answer, which is the narrow one — so `unset` closes a
    // public door rather than opening it, the same direction every other field
    // here goes.
    return { id: door.id, person: setup.person, acceptFromAnyone: false };
  }
  if (field === 'allowed') return { id: door.id, person: setup.person, allowed: [] };
  return { id: door.id, person: setup.person, address: null };
}

/**
 * One field's value as plain text, for `phone channels get`.
 *
 * A credential reads as *where it is* and never as what it says: the last four
 * characters, the variable it may instead live in, or nothing. That is the same
 * answer `phone config get apiKey` gives, and for the same reason.
 */
export function doorValue(setup: AgentChannelSetup, target: string): string {
  const { door, field } = readDoorTarget(setup, target);
  const credential = door.credentials.find((entry) => entry.key === field);
  if (credential) {
    if (credential.envPresent) return `environment:${credential.env}`;
    return credential.present ? credential.hint : '';
  }
  if (field === 'enabled') return String(door.enabled);
  if (field === 'acceptFromAnyone') return String(door.acceptFromAnyone);
  if (field === 'allowed') return door.allowed.join(' ');
  return door.address;
}

/**
 * Ids as a person types them, as the list a door admits.
 *
 * Split on whitespace and commas, because a list pasted out of a chat is
 * separated by whichever of those whatever it was copied from used — and one
 * entry of `819012345678, 819012345679` is an id no door recognises, which is a
 * door that is open and admits nobody.
 */
function splitIds(value: string): string[] {
  return value.split(/[\s,]+/).map((entry) => entry.trim()).filter(Boolean);
}
