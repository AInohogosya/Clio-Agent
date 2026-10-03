import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { CliError, isConfigField, parseArguments } from '../dist/args.js';

test('no arguments launches the terminal interface', () => {
  const options = parseArguments([]);
  assert.equal(options.command, 'tui');
  assert.equal(options.json, false);
  assert.equal(options.debug, false);
});

test('the flags that answer for themselves still work', () => {
  assert.equal(parseArguments(['--help']).command, 'help');
  assert.equal(parseArguments(['-h']).command, 'help');
  assert.equal(parseArguments(['--version']).command, 'version');
  assert.equal(parseArguments(['-v']).command, 'version');
  assert.equal(parseArguments(['--debug']).debug, true);
});

test('a flag that did nothing is not accepted', () => {
  // `--status` and `--chat` duplicated `status` and `send`, and `--yes` did
  // nothing at all: parsed, documented in the help, and inert. A flag that
  // cannot change what the program does is a flag nobody can trust.
  for (const argv of [['--yes'], ['-y'], ['--status'], ['--chat']]) {
    assert.throws(
      () => parseArguments(argv),
      (error) => error instanceof CliError && error.code === 'unknown_command',
      argv.join(' '),
    );
  }
  assert.equal(parseArguments(['status']).command, 'status');
  assert.equal(parseArguments(['send', 'hi']).message, 'hi');
});

test('a message is read from a pipe when stdin is not a terminal', () => {
  const options = parseArguments(['send']);
  assert.equal(options.command, 'send');
  assert.equal(options.fromStdin, process.stdin.isTTY ? false : true);
});

test('every documented command name is recognised', () => {
  for (const command of ['tui', 'setup', 'send', 'status', 'models', 'config', 'history', 'clear', 'help', 'version']) {
    assert.equal(parseArguments([command]).command, command, command);
  }
});

test('a bare first word is treated as the message to send', () => {
  const options = parseArguments(['hello', 'there']);
  assert.equal(options.command, 'send');
  assert.equal(options.message, 'hello there');
});

test('send accepts an explicit message with spaces and quotes intact', () => {
  assert.equal(parseArguments(['send', 'what  is   new?']).message, 'what  is   new?');
  assert.equal(parseArguments(['send', 'a "quoted" thought']).message, 'a "quoted" thought');
});

test('an explicit command wins over a flag of lower rank', () => {
  assert.equal(parseArguments(['status', '--debug']).command, 'status');
  assert.equal(parseArguments(['--debug', 'status']).command, 'status');
  assert.equal(parseArguments(['--debug', 'status']).debug, true);
});

test('options accept both inline and separated values', () => {
  assert.equal(parseArguments(['--width=100']).width, 100);
  assert.equal(parseArguments(['--width', '100']).width, 100);
  assert.equal(parseArguments(['--lang=ja']).language, 'ja');
  assert.equal(parseArguments(['--lang', 'zh']).language, 'zh');
  assert.equal(parseArguments(['--no-color']).noColor, true);
  assert.equal(parseArguments(['--no-colour']).noColor, true);
  assert.equal(parseArguments(['--json']).json, true);
});

test('a double dash stops option parsing', () => {
  const options = parseArguments(['send', '--', '--not-a-flag']);
  assert.equal(options.message, '--not-a-flag');
});

test('a secret is never accepted as an argument', () => {
  for (const argv of [
    ['--api-key=secret'],
    ['--apikey', 'secret'],
    ['--token=secret'],
    ['--password=secret'],
    ['config', 'set', 'apiKey', 'secret'],
  ]) {
    assert.throws(
      () => parseArguments(argv),
      (error) => {
        assert.ok(error instanceof CliError, `${argv.join(' ')} should raise a CliError`);
        assert.equal(error.code, 'sensitive_argument');
        return true;
      },
      argv.join(' '),
    );
  }
});

test('reading the api key is allowed but never takes a value', () => {
  assert.equal(parseArguments(['config', 'get', 'apiKey']).configAction, 'get');
  assert.equal(parseArguments(['config', 'get', 'apiKey']).field, 'apiKey');
});

test('unsetting the api key carries no secret, so it parses', () => {
  // `unset` removes the key rather than reading one: there is nothing on the
  // command line to keep out of the history, and the refusal's own advice —
  // "use the interactive prompt" — would be wrong for an operation that
  // prompts for nothing.
  const options = parseArguments(['config', 'unset', 'apiKey']);
  assert.equal(options.configAction, 'unset');
  assert.equal(options.field, 'apiKey');
});

test('an unknown flag is rejected with a machine code', () => {
  assert.throws(
    () => parseArguments(['--nope']),
    (error) => {
      assert.equal(error.code, 'unknown_command');
      return true;
    },
  );
});

test('invalid option values are rejected by name', () => {
  assert.throws(
    () => parseArguments(['--lang', 'fr']),
    (error) => {
      assert.equal(error.code, 'invalid_value');
      assert.equal(error.values.field, 'language');
      return true;
    },
  );
  assert.throws(
    () => parseArguments(['--width', 'abc']),
    (error) => {
      assert.equal(error.code, 'invalid_value');
      assert.equal(error.values.field, 'width');
      return true;
    },
  );
});

test('config subcommands parse their action, field and value', () => {
  assert.equal(parseArguments(['config']).configAction, 'list');
  assert.equal(parseArguments(['config', 'list']).configAction, 'list');
  assert.equal(parseArguments(['config', 'get', 'provider']).field, 'provider');
  assert.equal(parseArguments(['config', 'set', 'model', 'gpt-4o', 'mini']).value, 'gpt-4o mini');
  assert.equal(parseArguments(['config', 'unset', 'accent']).field, 'accent');
});

test('an incomplete config invocation is rejected', () => {
  assert.throws(() => parseArguments(['config', 'get']), (error) => error.code === 'unknown_command');
  assert.throws(() => parseArguments(['config', 'unset']), (error) => error.code === 'unknown_command');
  assert.throws(() => parseArguments(['config', 'set', 'model']), (error) => error.code === 'unknown_command');
  assert.throws(() => parseArguments(['config', 'sideways']), (error) => error.code === 'unknown_command');
});

test('stray positionals are ignored after a valueless command', () => {
  assert.equal(parseArguments(['status', 'extra', 'words']).command, 'status');
  assert.equal(parseArguments(['version', 'x']).command, 'version');
});

test('config field names are validated', () => {
  assert.equal(isConfigField('provider'), true);
  assert.equal(isConfigField('apiKey'), true);
  assert.equal(isConfigField('accent'), true);
  assert.equal(isConfigField('autoSignal'), false, 'the auto-signal no longer exists');
  assert.equal(isConfigField('password'), false);
});

test('every remaining config field is a legal target', () => {
  for (const field of ['provider', 'model', 'baseUrl', 'language', 'theme', 'accent']) {
    const set = parseArguments(['config', 'set', field, 'value']);
    assert.equal(set.field, field, field);
    assert.equal(parseArguments(['config', 'get', field]).field, field);
    assert.equal(parseArguments(['config', 'unset', field]).configAction, 'unset', field);
  }
  assert.equal(parseArguments(['config', 'get', 'apiKey']).field, 'apiKey');
});

// ------------------------------------------------------------------- channels

test('a channel may be named for a send', () => {
  const options = parseArguments(['send', '--channel', 'telegram', 'hello']);
  assert.equal(options.command, 'send');
  assert.equal(options.channel, 'telegram');
  assert.equal(options.message, 'hello');
});

test('a channel may be named with the inline spelling too', () => {
  // The two spellings have to agree, or `--channel=telegram` would be the one
  // way to write a destination that silently does nothing.
  const options = parseArguments(['send', '--channel=telegram', '--to=tg:819012345678', 'hello']);
  assert.equal(options.channel, 'telegram');
  assert.equal(options.to, 'tg:819012345678');
});

test('a channel with no message is a session, not a send', () => {
  // `phone --channel telegram` is a terminal talking to Telegram, which is a
  // different and perfectly ordinary thing from asking one question there.
  const options = parseArguments(['--channel', 'telegram']);
  assert.equal(options.command, 'tui');
  assert.equal(options.channel, 'telegram');
});

test('an address with no channel is refused, because an address is on a door', () => {
  assert.throws(
    () => parseArguments(['send', '--to', 'tg:819012345678', 'hello']),
    (error) => error instanceof CliError && error.code === 'channel_required',
  );
});

test('a channel on a command that sends nothing is refused rather than ignored', () => {
  // A flag that changes nothing is a flag nobody can trust, and dropping this one
  // further in would leave the reader with no way to tell it had been dropped.
  for (const argv of [
    ['config', 'list', '--channel', 'telegram'],
    ['history', '--channel', 'telegram'],
    ['clear', '--to', 'tg:1'],
  ]) {
    assert.throws(
      () => parseArguments(argv),
      (error) => error instanceof CliError && error.code === 'unknown_command',
      argv.join(' '),
    );
  }
});

test('an empty channel name is refused rather than read as the local line', () => {
  // `--channel ""` and no `--channel` look identical to a reader and mean
  // opposite things, so the empty one has to fail rather than quietly mean "web".
  for (const argv of [['send', '--channel', '', 'hi'], ['send', '--channel=', 'hi']]) {
    assert.throws(
      () => parseArguments(argv),
      (error) => error instanceof CliError && error.code === 'invalid_value',
      argv.join(' '),
    );
  }
});

test('the channel list is a command of its own', () => {
  // Its own command rather than a flag on `status`, because it is answerable
  // without a running agent and its answer is the input to `--channel`.
  assert.equal(parseArguments(['channels']).command, 'channels');
  assert.equal(parseArguments(['channels', '--json']).json, true);
});
