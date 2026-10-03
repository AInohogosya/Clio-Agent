import { isLanguage, type Language } from '@project-phone/core';

/** Machine-readable failure codes. Translated centrally in `errors.ts`. */
export type ErrorCode =
  | 'sensitive_argument'
  | 'door_secret_argument'
  | 'door_invalid_address'
  | 'unknown_command'
  | 'invalid_value'
  | 'unknown_field'
  | 'not_resettable'
  | 'input_timeout'
  | 'input_too_large'
  | 'input_failed'
  | 'missing_message'
  | 'missing_key'
  | 'invalid_provider'
  | 'config_cleanup_failed'
  | 'legacy_key_cleanup_failed'
  | 'config_too_large'
  | 'unsafe_config_target'
  | 'insecure_config_permissions'
  | 'config_not_owned_by_user'
  | 'not_configured'
  | 'agent_target_required'
  | 'agent_target_unexpected'
  | 'channel_required'
  | 'channel_unknown'
  | 'channel_closed'
  | 'channel_unaddressed'
  | 'aborted';

export class CliError extends Error {
  readonly code: ErrorCode;
  readonly values: Record<string, string | number>;

  constructor(code: ErrorCode, values: Record<string, string | number> = {}) {
    super(code);
    this.name = 'CliError';
    this.code = code;
    this.values = values;
  }
}

export const COMMAND_NAMES = [
  'tui',
  'setup',
  'send',
  'channels',
  'status',
  'agent',
  'models',
  'config',
  'history',
  'clear',
  'help',
  'version',
] as const;

export type CommandName = (typeof COMMAND_NAMES)[number];

export type ConfigAction = 'list' | 'get' | 'set' | 'unset';

/**
 * What `phone channels` can be asked to do.
 *
 * The same four verbs `phone config` has, and the same grammar, because they are
 * the same question: what is this, set that, take it back. A door used to be
 * readable and not writable, which left the terminal as the one surface with no
 * way in — a bot token was available from a settings screen and a file edit, and
 * from nowhere a person at a terminal could reach.
 */
export type DoorAction = 'list' | 'get' | 'set' | 'unset';

/**
 * What `phone agent` can be asked to do.
 *
 * The lifecycle verbs are the agent's own, spelled the same way as the ones its
 * bridge and its browser interface use, so a script can `phone agent pause` and a
 * person can press the same verb in the terminal without learning a second name
 * for it. `show` is the default because asking an agent how it is should not
 * require remembering a verb.
 */
export const AGENT_ACTIONS = [
  'show',
  'pause-actions',
  'pause',
  'resume',
  'stop',
  'emergency',
  'undo',
  'cancel',
] as const;

export type AgentAction = (typeof AGENT_ACTIONS)[number];

export function isAgentAction(value: string): value is AgentAction {
  return (AGENT_ACTIONS as readonly string[]).includes(value);
}

export interface CliOptions {
  command: CommandName;
  configAction: ConfigAction;
  doorAction: DoorAction;
  agentAction: AgentAction;
  /** The id an `undo` or a `cancel` was aimed at. */
  target?: string;
  field?: string;
  value?: string;
  message?: string;
  /**
   * The door to send through, from `--channel`.
   *
   * A name rather than a resolved id, and deliberately not checked against the
   * deployment's doors here: the parser has no link to ask, so a name it could
   * not judge would be refused for a reason the reader cannot act on. It is
   * resolved — or refused with the list of what would have worked — where the
   * doors are actually known.
   */
  channel?: string;
  /** The destination on that door, from `--to`. */
  to?: string;
  json: boolean;
  debug: boolean;
  noColor: boolean;
  language?: Language;
  width?: number;
  fromStdin: boolean;
}

/** Fields that may be read or written with `phone config`. */
export const CONFIG_FIELDS = [
  'interface',
  'agentUrl',
  'agentPerson',
  'provider',
  'model',
  'baseUrl',
  'apiKey',
  'language',
  'theme',
  'accent',
] as const;

export type ConfigField = (typeof CONFIG_FIELDS)[number];

export function isConfigField(value: string): value is ConfigField {
  return (CONFIG_FIELDS as readonly string[]).includes(value);
}

const SECRET_FIELDS = new Set<string>(['apikey']);

function defaultOptions(): CliOptions {
  return {
    command: 'tui',
    configAction: 'list',
    doorAction: 'list',
    agentAction: 'show',
    json: false,
    debug: false,
    noColor: false,
    fromStdin: false,
  };
}

function parsePositiveInteger(value: string, field: string): number {
  if (!/^\d{1,4}$/.test(value)) throw new CliError('invalid_value', { field });
  return Number.parseInt(value, 10);
}

/**
 * Parses the command line into a normalised plan.
 *
 * `--help` and `--version` are accepted because every tool answers them, and a
 * bare first word that is not a known command is treated as the message to send,
 * so `phone "hello"` works. There is nothing else to accept: a flag that does
 * not change what the program does is a flag nobody can trust.
 */
export function parseArguments(argv: string[]): CliOptions {
  const options = defaultOptions();
  const positional: string[] = [];
  let explicitCommand = false;
  let messageProvided = false;

  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index] ?? '';

    if (argument === '--') {
      positional.push(...argv.slice(index + 1).map((value) => value ?? ''));
      break;
    }

    if (!argument.startsWith('-') || argument === '-') {
      positional.push(argument);
      continue;
    }

    const [flag, inlineValue] = splitFlag(argument);
    switch (flag) {
      case '--help':
      case '-h':
        setCommand(options, 'help');
        explicitCommand = true;
        break;
      case '--version':
      case '-v':
        setCommand(options, 'version');
        explicitCommand = true;
        break;
      case '--debug':
        options.debug = true;
        break;
      case '--json':
        options.json = true;
        break;
      case '--no-color':
      case '--no-colour':
        options.noColor = true;
        break;
      case '--color':
      case '--colour':
        options.noColor = false;
        break;
      case '--lang':
      case '--language': {
        const value = inlineValue ?? argv[++index];
        if (!isLanguage(value)) throw new CliError('invalid_value', { field: 'language' });
        options.language = value;
        break;
      }
      case '--width': {
        const value = inlineValue ?? argv[++index];
        options.width = parsePositiveInteger(String(value ?? ''), 'width');
        break;
      }
      case '--channel':
      case '--via': {
        const value = (inlineValue ?? argv[++index]) ?? '';
        if (!value.trim()) throw new CliError('invalid_value', { field: 'channel' });
        options.channel = value.trim();
        break;
      }
      case '--to': {
        const value = (inlineValue ?? argv[++index]) ?? '';
        if (!value.trim()) throw new CliError('invalid_value', { field: 'to' });
        options.to = value.trim();
        break;
      }
      default:
        if (flag.startsWith('--lang=') || flag.startsWith('--language=')) {
          const value = flag.slice(flag.indexOf('=') + 1);
          if (!isLanguage(value)) throw new CliError('invalid_value', { field: 'language' });
          options.language = value;
          break;
        }
        if (flag.startsWith('--width=')) {
          options.width = parsePositiveInteger(flag.slice(flag.indexOf('=') + 1), 'width');
          break;
        }
        if (flag.startsWith('--channel=') || flag.startsWith('--via=')) {
          const value = flag.slice(flag.indexOf('=') + 1);
          if (!value.trim()) throw new CliError('invalid_value', { field: 'channel' });
          options.channel = value.trim();
          break;
        }
        if (flag.startsWith('--to=')) {
          const value = flag.slice(flag.indexOf('=') + 1);
          if (!value.trim()) throw new CliError('invalid_value', { field: 'to' });
          options.to = value.trim();
          break;
        }
        if (SECRET_FLAG.test(flag)) throw new CliError('sensitive_argument');
        throw new CliError('unknown_command');
    }
  }

  const first = positional[0];
  if (first !== undefined && !explicitCommand) {
    if (isCommandName(first)) {
      setCommand(options, first);
      explicitCommand = true;
      positional.shift();
    } else {
      // `phone hello there` reads as one message, not a command plus an argument.
      setCommand(options, 'send');
      options.message = positional.join(' ').trim();
      messageProvided = true;
      positional.length = 0;
    }
  }

  if (options.command === 'send' && !messageProvided) {
    const rest = positional.join(' ').trim();
    if (rest) {
      options.message = rest;
      messageProvided = true;
      positional.length = 0;
    } else if (!options.fromStdin && !process.stdin.isTTY) {
      options.fromStdin = true;
    }
  }

  if (options.command === 'agent') {
    const verb = (positional[0] ?? 'show').toLowerCase();
    if (!isAgentAction(verb)) throw new CliError('unknown_command');
    options.agentAction = verb;
    positional.shift();
    const target = positional.join(' ').trim();
    // An undo or a cancel is aimed at one row, and a verb that takes no id is
    // given one. Both are said plainly: "unknown command" for `phone agent undo`
    // would send the reader looking for a misspelling that is not there.
    if ((verb === 'undo' || verb === 'cancel') && !target) {
      throw new CliError('agent_target_required', { action: verb });
    }
    if (verb !== 'undo' && verb !== 'cancel' && target) {
      throw new CliError('agent_target_unexpected', { action: verb });
    }
    if (target) options.target = target;
  }

  if (options.command === 'config') {
    const action = positional[0] ?? 'list';
    if (action !== 'list' && action !== 'get' && action !== 'set' && action !== 'unset') {
      throw new CliError('unknown_command');
    }
    options.configAction = action;
    const field = positional[1];
    if (field !== undefined) {
      options.field = field;
    }
    const value = positional.slice(2).join(' ').trim();
    if (action === 'set') {
      if (field === undefined) throw new CliError('unknown_command');
      // A secret is never accepted as a value: a key on a command line is in
      // the shell history and the process list. `unset` carries nothing — it
      // removes the key — so it is allowed through, which is what makes a key
      // removable from this surface at all; refusing it would leave an error
      // advising "the interactive prompt" for an operation that needs none.
      if (SECRET_FIELDS.has(field.toLowerCase())) throw new CliError('sensitive_argument');
      if (!value) throw new CliError('unknown_command');
      options.value = value;
    }
    if (action === 'get' && field === undefined) throw new CliError('unknown_command');
    if (action === 'unset' && field === undefined) throw new CliError('unknown_command');
  }

  // The same four verbs for a door, and the same refusals: a field name is
  // required for all but the list, and `set` needs a value. What a value may be is
  // decided where the doors are known — `phone config` cannot check whether a
  // field exists and neither can this, so both hand it to the reader of the
  // table, which is the one place the doors are.
  if (options.command === 'channels') {
    const action = positional[0] ?? 'list';
    if (action !== 'list' && action !== 'get' && action !== 'set' && action !== 'unset') {
      throw new CliError('unknown_command');
    }
    options.doorAction = action;
    const field = positional[1];
    if (field !== undefined) {
      options.field = field;
    }
    const value = positional.slice(2).join(' ').trim();
    if (action === 'set') {
      if (field === undefined || !value) throw new CliError('unknown_command');
      options.value = value;
    }
    if (action === 'get' && field === undefined) throw new CliError('unknown_command');
    if (action === 'unset' && field === undefined) throw new CliError('unknown_command');
  }

  // Which door a message goes out of only means anything to the two commands
  // that send one. `phone config list --channel telegram` would be a flag that
  // changes nothing, and a flag that changes nothing is a flag nobody can trust
  // — so it is refused here rather than ignored further in, where the reader
  // would have no way to tell it had been dropped.
  if ((options.channel !== undefined || options.to !== undefined)
    && options.command !== 'send' && options.command !== 'tui') {
    throw new CliError('unknown_command');
  }
  // An address without a door has nothing to be an address *on*. The reverse is
  // fine and common: `--channel telegram` alone is the ordinary case, and the
  // contact book is expected to know the address.
  if (options.to !== undefined && options.channel === undefined) {
    throw new CliError('channel_required');
  }

  return options;
}

const SECRET_FLAG = /^--(?:api-?key|token|secret|password|prompt)(?:=|$)/i;

function splitFlag(argument: string): [string, string | undefined] {
  const separator = argument.indexOf('=');
  if (separator === -1) return [argument, undefined];
  return [argument.slice(0, separator), argument.slice(separator + 1)];
}

function isCommandName(value: string): value is CommandName {
  return (COMMAND_NAMES as readonly string[]).includes(value);
}

function setCommand(options: CliOptions, command: CommandName): void {
  const rank = (name: CommandName): number => COMMAND_NAMES.indexOf(name);
  if (options.command === 'tui' || rank(command) < rank(options.command)) {
    options.command = command;
  }
}
