#!/usr/bin/env node
import { createInterface } from 'node:readline';
import { pathToFileURL } from 'node:url';
import React from 'react';
import { render } from 'ink';
import {
  AgentClient,
  AgentRequestError,
  baseModelFailureKey,
  channelFailureKey,
  channelLabel,
  createAgentTransport,
  createSettings,
  createTranslator,
  discoverProviderModels,
  DuplexClient,
  MAX_PROMPT_LENGTH,
  sanitizeTerminalText,
  toChatMessages,
  type AgentControlAction,
  type AgentView,
  type Language,
  type Settings,
  type TranslationKey,
} from '@project-phone/core';
import { CliError, parseArguments, type CliOptions, type ConfigField } from './args.js';
import { doorClear, doorValue, doorWrite } from './door-fields.js';
import { describeError, isKnownErrorCode } from './errors.js';
import {
  channelsJson,
  channelsScreen,
  doorRefusalError,
  doorSummary,
  resolveDoor,
  type DoorChoice,
} from './channels.js';
import {
  configPath,
  createPhoneClient,
  hasEnvironmentApiKey,
  readConfig,
  saveConfig,
  type PhoneConfig,
  type PhoneSession,
} from './config.js';
import { readConfigField, setConfigField, unsetConfigField } from './config-fields.js';
import { agentScreen, controlLabel } from './agent-screen.js';
import { cleanMessage, isInteractive, promptForMessage, readPipedLines } from './input.js';
import { createPalette, detectColorDepth, ASCII_SPINNER, SPINNER_FRAMES, type ColorDepth, type Palette } from './palette.js';
import { clampWidth, detectWidth, keyValue, type RenderOptions } from './render.js';
import {
  clearScreen,
  configScreen,
  helpScreen,
  historyScreen,
  jsonPayload,
  modelsScreen,
  outgoingScreen,
  renderLines,
  replyScreen,
  screenOptions,
  statusScreen,
} from './screens.js';
import { enterFullScreen } from './terminal.js';
import { Tui } from './tui.js';
import { AgentTui } from './agent-tui.js';

// The version designation on its own, and the product's name derived from it, so the
// two cannot drift: a release edits this line and the name, the help footer and the
// `--version` answer all follow. The date is a fact about the build rather than
// something read from the clock, because a copy of it on another machine has that
// machine's clock and this machine's contents.
export const VERSION = '3 Beta 1';
export const PRODUCT_NAME = `Clio Agent ${VERSION}`;
export const RELEASE_DATE = '2026-10-03';
export const RELEASE_TIMEZONE = 'JST';

const DEFAULT_HISTORY_LIMIT = 20;

interface Runtime {
  options: CliOptions;
  config: PhoneConfig;
  palette: Palette;
  render: RenderOptions;
  depth: ColorDepth;
}

function resolveDepth(options: CliOptions): ColorDepth {
  if (options.json || options.noColor) return 0;
  return detectColorDepth();
}

function buildRuntime(options: CliOptions): Runtime {
  const config = readConfig();
  const depth = resolveDepth(options);
  const language: Language = options.language ?? config.settings.language;
  const palette = createPalette(config.settings.accent, config.settings.theme, depth);
  const render = screenOptions(config.settings, palette, options.width ?? detectWidth(), language);
  return { options, config, palette, render, depth };
}

function out(text: string): void {
  process.stdout.write(text);
}

/**
 * Failures, reported honestly.
 *
 * A recognised code is translated. Anything else is printed as the message it
 * is: looking an arbitrary string up in a table of sentences used to turn every
 * unexpected failure — a missing directory, a refused socket, a provider error —
 * into the same four words, which is how a misconfiguration became impossible
 * to diagnose from the terminal.
 */
function reportError(error: unknown, language: Language): void {
  const text = error instanceof CliError
    ? describeError(error.code, language, error.values)
    : error instanceof Error
      // A recognised code is translated; anything else is the message it is.
      ? isKnownErrorCode(error.message)
        ? describeError(error.message, language)
        : (error.message || describeError('unknown_command', language))
      : describeError('unknown_command', language);
  process.stderr.write(`${sanitizeTerminalText(text)}\n`);
  process.exitCode = 1;
}

function storedLanguage(): Language {
  try {
    return readConfig().settings.language;
  } catch {
    return 'en';
  }
}

/**
 * The reason this process cannot reach a provider, or `''` when it can.
 *
 * Surfaced before a message is sent so that "nothing came back" is never the
 * first sign that there is no key.
 */
function providerProblem(client: DuplexClient, language: Language): string {
  const reason = client.unusableReason();
  return reason ? createTranslator(language)('cliNotConfiguredDetail', { reason }) : '';
}

/**
 * Why nothing will be sent, checked *before* the message is.
 *
 * For a provider that is the credential and the endpoint, and the client already
 * knows both. For an agent it is the link: there is no key on that screen to be
 * missing, so the reason has to be the one thing that is actually wrong — an
 * interface service that is not running, or an interface service with no agent
 * behind it. Reading it up front means `phone send` reports it instead of queueing
 * a question nobody is going to answer.
 *
 * Both halves matter, and the second is the one a reachable service hides: the
 * bridge answers just as happily with nothing running behind it as with the
 * agent, so a link that loads is not a link with somebody at the end of it. A
 * queued question to a machine with no agent on it is a row nobody will ever
 * answer, and the reader waits out the whole stall budget to be told the agent
 * had gone quiet.
 */
async function sessionProblem(session: PhoneSession, language: Language): Promise<string> {
  if (!session.agent) return providerProblem(session.client, language);
  const t = createTranslator(language);
  if (session.agent.getStatus() === 'online' && !session.agent.isAgentAbsent()) return '';
  const loaded = await session.agent.refresh();
  if (loaded && !session.agent.isAgentAbsent()) return '';
  return t('cliAgentUnreachable');
}

/**
 * The door `--channel` named, or a refusal a reader can act on.
 *
 * The one thing this has to get right is the ordering: a question is queued and a
 * deliberation begins, and a reply that has nowhere to go is discovered at the
 * *last* step — after the agent has thought about it. So the door is resolved
 * against the deployment's own list first, and a name that is not open, a door
 * whose allowlist is empty and a person with no address on it are all refused here
 * rather than arriving in the transcript as a question that is never answered.
 *
 * Only meaningful for an agent: a direct-provider session has one destination and
 * no doors, so `--channel` on one is ignored rather than refused — the flag names
 * where the *agent* is reached, and there is no agent to reach.
 */
async function resolveDestination(options: CliOptions): Promise<DoorChoice> {
  const fallback: DoorChoice = { ok: true, channel: 'web', address: null };
  if (options.channel === undefined && options.to === undefined) return fallback;
  const settings = createSettings(readConfig().settings);
  if (settings.interface !== 'agent') return fallback;
  const agent = new AgentClient({ url: settings.agentUrl, personId: settings.agentPerson });
  try {
    const choice = resolveDoor(await agent.doors(), settings.agentPerson, options.channel, options.to);
    if (!choice.ok) throw doorRefusalError(choice);
    return choice;
  } finally {
    agent.dispose();
  }
}

/**
 * `phone channels` — the doors the agent has open, and who each can reach.
 *
 * A command of its own rather than a flag on `phone status`, because it is
 * answerable without a running agent and its answer is the input to the flags
 * that are not: `--channel` takes a name, and the names are here.
 */
async function runChannels(runtime: Runtime): Promise<number> {
  const { options, config, render } = runtime;
  const settings = createSettings(config.settings);
  if (settings.interface !== 'agent') {
    const problem = createTranslator(render.language)('interfaceDirectHint');
    if (options.json) out(`${JSON.stringify({ error: problem }, null, 2)}\n`);
    else out(renderLines([`  ${problem}`]));
    return 1;
  }
  if (options.doorAction !== 'list') return runDoor(runtime, settings);
  const agent = new AgentClient({ url: settings.agentUrl, personId: settings.agentPerson });
  try {
    const loaded = await agent.refresh().catch(() => false);
    const doors = await agent.doors();
    // The transcript is the agent's own, and it is the only place a door that has
    // been closed since it was last open can still be seen — so a door that
    // stopped being offered is named rather than dropped.
    const messages = loaded ? toChatMessages(agent.getView().messages) : [];
    const { offered, closed } = doorSummary(doors, messages);
    const current = options.channel?.trim() || agent.getChannel();
    if (options.json) {
      out(`${JSON.stringify(channelsJson(doors, current, closed), null, 2)}\n`);
      return 0;
    }
    if (!loaded) {
      out(renderLines([
        `  ${createTranslator(render.language)('cliAgentUnreachable')}`,
        ...channelsScreen(offered, current, render, closed),
      ]));
      return 1;
    }
    out(renderLines(channelsScreen(offered, current, render, closed)));
    return 0;
  } finally {
    agent.dispose();
  }
}

/**
 * `phone channels get` / `set` / `unset` — turning a door on from a terminal.
 *
 * The doors are asked of the agent rather than kept in this file, so the fields
 * named here are the ones that deployment really has. Only the named field is
 * sent: a save that mentioned the others would clear them, and a person changing
 * an allowlist must not lose the token beside it because one command named the
 * wrong key.
 *
 * A credential cannot be *set* from here, for the reason `phone config` refuses
 * one: a secret on a command line is in the shell history and the process list.
 * The sentence says where it goes instead, and `get` says which of the two routes
 * this machine already has. Removing one is allowed, because removal says nothing.
 */
async function runDoor(runtime: Runtime, settings: Settings): Promise<number> {
  const { options, render } = runtime;
  const t = createTranslator(render.language);
  const agent = new AgentClient({ url: settings.agentUrl, personId: settings.agentPerson });
  // Declared out here so a refusal thrown before the answer is read still has a
  // door to name — the prefix is the one thing that makes an address refusal
  // actionable.
  let setup: Awaited<ReturnType<AgentClient['channelSetup']>> = null;
  const target = options.field ?? '';
  try {
    setup = await agent.channelSetup();
    if (setup === null) {
      const problem = t('doorUnreachable');
      if (options.json) out(`${JSON.stringify({ error: problem }, null, 2)}\n`);
      else out(renderLines([`  ${problem}`, '']));
      return 1;
    }
    if (options.doorAction === 'get') {
      const value = doorValue(setup, target);
      if (options.json) out(`${JSON.stringify({ field: target, value }, null, 2)}\n`);
      else out(`${value}\n`);
      return 0;
    }
    const write = options.doorAction === 'set'
      ? doorWrite(setup, target, options.value ?? '')
      : doorClear(setup, target);
    const written = await agent.saveDoor(write);
    if (options.json) {
      out(`${JSON.stringify({ field: target, value: doorValue(written, target), setup: written }, null, 2)}\n`);
      return 0;
    }
    // What the agent says it now has, rather than what was typed — the same line
    // `phone config` prints, and the same reason: a form reporting its own
    // optimistic value is how a setting that did not land looks like one that did.
    out(renderLines([
      keyValue(target, doorValue(written, target) || t('doorKeyNone'), render.palette, render.width, 24),
      `  ${t('doorSaved')}`,
      '',
    ]));
    return 0;
  } catch (error) {
    if (error instanceof CliError) throw error;
    if (error instanceof AgentRequestError) {
      // The door's own prefix, for the one refusal that is about an address: a
      // sentence that says only "that is not an address" leaves the reader to work
      // out what an address looks like on the door they named.
      const prefix = setup?.doors.find((door) => door.id === String(target).split('.')[0]?.trim())?.prefix ?? '';
      const sentence = t(channelFailureKey(error), { prefix });
      if (options.json) out(`${JSON.stringify({ error: sentence }, null, 2)}\n`);
      else out(renderLines([`  ${sentence}`, '']));
      return 1;
    }
    throw error;
  } finally {
    agent.dispose();
  }
}

/**
 * One message, then the shared transcript is updated with whatever the line
 * produced. The client is disposed in a `finally` so an interrupted run leaves
 * no request behind.
 */
async function runSend(runtime: Runtime): Promise<number> {
  const { options, config, render } = runtime;
  // Resolved before the message is even read, so `--channel nonsense` says what
  // would have worked rather than first asking the reader for the message it is
  // about to refuse.
  const destination = await resolveDestination(options);
  const message = options.message ? cleanMessage(options.message) : await promptForMessage();
  const session = createPhoneClient(config, destination);
  const { client } = session;
  client.connect();
  try {
    const blocked = await sessionProblem(session, render.language);
    if (blocked) {
      if (options.json) {
        out(`${JSON.stringify({ error: blocked }, null, 2)}\n`);
      } else {
        out(`${renderLines([`  ${blocked}`])}\n`);
      }
      return 1;
    }
    if (!options.json) {
      // The door is named on the way out, not only in the answer. A question sent
      // to somebody's phone and a question sent to this terminal are the same two
      // words on screen otherwise, and the reader is the only one who knows
      // which of the two just happened. The label rather than the id, because
      // `slack` is a word a person says and `channel` is the word the config uses.
      const via = destination.channel === 'web'
        ? ''
        : `  ${createTranslator(render.language)('channelViaLabel', { channel: channelLabel(destination.channel) })}`;
      out(`${renderLines(outgoingScreen(message, render))}${via ? `\n${via}` : ''}\n`);
      const t = createTranslator(render.language);
      // The same frame rule the TUI keeps: a braille spinner is a glyph a
      // depth-0 session cannot be trusted to render, so the fallback is the
      // one the palette's own constants exist for.
      const frame = runtime.palette.depth === 0 ? ASCII_SPINNER[0]! : SPINNER_FRAMES[0]!;
      out(`  ${runtime.palette.accentText(frame)} ${runtime.palette.accentText(t('cliThinking'))}\n`);
    }
    const reply = await client.sendMessage(message);
    const failure = client.getSnapshot().lastError;
    if (options.json) {
      out(`${JSON.stringify({
        message: { role: 'user', text: message },
        reply: reply ? { role: 'assistant', text: reply.text } : null,
        channel: destination.channel,
        ...(destination.address ? { address: destination.address } : {}),
        error: failure,
      }, null, 2)}\n`);
    } else {
      out(renderLines(replyScreen(reply, render)));
      if (!reply && failure) {
        process.stderr.write(`${sanitizeTerminalText(failure)}\n`);
      }
    }
    const snapshot = client.getSnapshot();
    // In agent mode the agent's store is the record of this conversation, so the
    // shared file keeps only what the terminal owns: the settings. Writing the
    // transcript back would create a second copy the agent never sees.
    saveConfig(
      config.settings.interface === 'agent'
        ? { ...config, settings: snapshot.settings }
        : { ...config, settings: snapshot.settings, messages: snapshot.messages },
      { origin: 'cli' },
    );
    return reply ? 0 : 1;
  } finally {
    session.agent?.dispose();
    client.dispose();
  }
}

/**
 * `phone agent` — ask the agent how it is, or move it.
 *
 * The verbs are the agent's own, and every one of them is *queued* rather than
 * performed: the interface service records the request and the agent decides
 * whether to act on it. The output says "queued" for that reason, because a line
 * that claimed an agent had stopped when it had only been asked to would be a
 * lie about the one thing an operator is checking.
 */
async function runAgent(runtime: Runtime): Promise<number> {
  const { options, config, render } = runtime;
  const settings = config.settings;
  if (settings.interface !== 'agent') {
    const problem = createTranslator(render.language)('interfaceDirectHint');
    if (options.json) {
      out(`${JSON.stringify({ error: problem }, null, 2)}\n`);
    } else {
      out(renderLines([`  ${problem}`]));
    }
    return 1;
  }

  const agent = new AgentClient({ url: settings.agentUrl, personId: settings.agentPerson });
  const transport = createAgentTransport({ resolve: () => agent });
  const verb = options.agentAction;
  const t = createTranslator(render.language);

  try {
    if (verb === 'show') {
      const loaded = await agent.refresh();
      if (!loaded) {
        const reason = t('cliAgentUnreachable');
        if (options.json) out(`${JSON.stringify({ error: reason, url: settings.agentUrl }, null, 2)}\n`);
        else out(renderLines([`  ${reason}`, `  ${settings.agentUrl}`, '']));
        return 1;
      }
      if (options.json) {
        out(`${JSON.stringify({ endpoint: settings.agentUrl, ...agentViewJson(agent.getView()) }, null, 2)}\n`);
      } else {
        out(renderLines(agentScreen(agent.getView(), render)));
      }
      return 0;
    }

    if (verb === 'pause-actions' || verb === 'pause' || verb === 'resume' || verb === 'stop' || verb === 'emergency') {
      const action: AgentControlAction = verb === 'pause-actions'
        ? 'pause_actions'
        : verb === 'pause'
          ? 'pause_all'
          : verb === 'resume'
            ? 'resume'
            : verb === 'stop'
              ? 'stop'
              : 'emergency_stop';
      const control = await transport.control(action);
      if (options.json) {
        out(`${JSON.stringify({ queued: action, control }, null, 2)}\n`);
      } else {
        out(renderLines([
          '',
          `  ${render.palette.accentText('✓')} ${t('cliAgentQueued', { action: controlLabel(action, t) })}`,
          `  ${render.palette.faint(agent.getView().presence.state)}`,
          '',
        ]));
      }
      return 0;
    }

    const target = options.target ?? '';
    if (verb === 'undo') {
      await transport.undoAction(target);
      const action = agent.getView().actions.find((entry) => entry.id === target);
      const text = t('cliAgentUndoQueued', { tool: action?.tool ?? target });
      if (options.json) out(`${JSON.stringify({ queued: 'undo', action_id: target }, null, 2)}\n`);
      else out(renderLines(['', `  ${render.palette.accentText('✓')} ${text}`, '']));
      return 0;
    }

    await transport.closeIntention(target);
    const title = findIntention(agent.getView(), target)?.title ?? target;
    if (options.json) out(`${JSON.stringify({ queued: 'cancel', intention_id: target }, null, 2)}\n`);
    else out(renderLines(['', `  ${render.palette.accentText('✓')} ${t('cliAgentCloseQueued', { title })}`, '']));
    return 0;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (options.json) out(`${JSON.stringify({ error: message }, null, 2)}\n`);
    else out(renderLines([`  ${message}`, '']));
    return 1;
  } finally {
    agent.dispose();
  }
}

function findIntention(view: AgentView, id: string): { title: string } | undefined {
  const stack = [...view.intentions];
  while (stack.length) {
    const node = stack.shift()!;
    if (node.id === id || node.title === id) return node;
    stack.push(...node.children);
  }
  return undefined;
}

/** The agent's own state as plain data, for `--json`. */
function agentViewJson(view: AgentView): Record<string, unknown> {
  return {
    state: view.presence.state,
    focus: view.presence.focus,
    updatedAt: view.presence.ts,
    control: view.control,
    // What it can think with, and what it falls back to. Reported because a
    // gateway that finds no usable model is otherwise indistinguishable from an
    // agent that has decided to say nothing.
    model: view.model,
    catalogue: view.catalogue,
    cycles: view.presence.recentCycles,
    intentions: flattenIntentionTree(view),
    actions: view.actions,
    thoughts: view.thoughts,
    budget: view.budget,
    guardian: view.guardian,
    messages: view.messages,
  };
}

function flattenIntentionTree(view: AgentView): unknown[] {
  const out: unknown[] = [];
  const walk = (nodes: AgentView['intentions']): void => {
    for (const node of nodes) {
      out.push({ ...node, children: undefined });
      walk(node.children);
    }
  };
  walk(view.intentions);
  return out;
}

async function runModels(runtime: Runtime): Promise<void> {
  const result = await discoverProviderModels(runtime.config.settings);
  if (runtime.options.json) {
    out(`${JSON.stringify({ source: result.source, models: result.models, error: result.error ?? null }, null, 2)}\n`);
    return;
  }
  out(renderLines(modelsScreen(runtime.config, result, runtime.render)));
}

/**
 * The fields that describe a base model, in either mode.
 *
 * In `direct` they are this kit's own settings. In `agent` they are the agent's
 * base model, which lives in the agent's configuration — and writing them to the
 * shared file alone would have the terminal report a change the gateway never
 * sees, which is the one outcome worse than refusing the command.
 */
const BASE_MODEL_FIELDS = new Set<ConfigField>(['provider', 'model', 'baseUrl', 'apiKey']);

async function runConfig(runtime: Runtime): Promise<number> {
  const { options, config, render } = runtime;
  const field = options.field ?? '';

  if (options.configAction === 'get') {
    out(`${readConfigField(config, field)}\n`);
    return 0;
  }
  if (options.configAction === 'list') {
    if (options.json) {
      out(jsonPayload(config));
      return 0;
    }
    out(renderLines(configScreen(config, render)));
    return 0;
  }

  const next = options.configAction === 'set'
    ? setConfigField(config, field, options.value ?? '')
    : unsetConfigField(config, field);
  const saved = saveConfig(next, { origin: 'cli' });
  if (options.json) {
    out(jsonPayload(saved));
    return 0;
  }

  // Pushed before anything is printed, so what is on the screen is what the
  // agent ended up with. A key is not settable from a command line at all — the
  // argument parser refuses it before here — so this can only ever move a model,
  // an endpoint or a vendor, and it leaves the stored key where it is.
  if (saved.settings.interface === 'agent' && BASE_MODEL_FIELDS.has(field as ConfigField)) {
    const t = createTranslator(render.language);
    const pushed = await pushBaseModel(runtime, saved.settings, options.configAction === 'unset' && field === 'apiKey');
    if (!pushed.ok) {
      out(renderLines([`  ${pushed.key === 'baseModelKeyRequired' ? t('baseModelKeyFromCli') : t(pushed.key)}`, '']));
      // The file is written either way, and it is right; what did not happen is
      // the part the agent depends on. Saying so is the difference between a
      // setting that took effect and one that silently did not.
      return 1;
    }
  }

  const t = createTranslator(render.language);
  const cleared = field === 'apiKey' && options.configAction === 'unset';
  out(renderLines(configScreen(saved, render, field, cleared ? t('cliKeyCleared') : readConfigField(saved, field))));
  return 0;
}

/**
 * Hands the base model to the agent, over the same link every other agent
 * command uses.
 *
 * The shared file is written first and either way: it is what the terminal and
 * the browser share, and a base model that could not be pushed is still the
 * value this surface believes in. What the exit code reports is the push, because
 * that is the part the agent actually depends on.
 *
 * The answer comes back as a translation key rather than as a status, because
 * `http_404` is not something a person can act on. A 404 in particular means the
 * interface service answering is an older build without this route, which is a
 * different thing from an agent that is merely down.
 */
async function pushBaseModel(
  runtime: Runtime,
  settings: Settings,
  clearKey: boolean,
): Promise<{ ok: true } | { ok: false; key: TranslationKey }> {
  const agent = new AgentClient({ url: settings.agentUrl, personId: settings.agentPerson });
  try {
    await agent.saveBaseModel({
      provider: settings.provider,
      model: settings.model,
      baseUrl: settings.baseUrl,
      apiKey: settings.apiKey,
      clearKey,
    });
    return { ok: true };
  } catch (error) {
    return { ok: false, key: baseModelFailureKey(error) };
  } finally {
    agent.dispose();
  }
}

function runHistory(runtime: Runtime): void {
  if (runtime.options.json) {
    out(jsonPayload(runtime.config, { limit: DEFAULT_HISTORY_LIMIT }));
    return;
  }
  out(renderLines(historyScreen(runtime.config, runtime.render, DEFAULT_HISTORY_LIMIT)));
}

function runClear(runtime: Runtime): void {
  const removed = runtime.config.messages.length;
  const saved = saveConfig({ ...runtime.config, messages: [] }, { origin: 'cli' });
  if (runtime.options.json) {
    out(jsonPayload(saved, { removed }));
    return;
  }
  out(renderLines(clearScreen(runtime.render, removed)));
}

function runStatus(runtime: Runtime): void {
  if (runtime.options.json) {
    out(jsonPayload(runtime.config, {
      configFile: configPath(),
      hasEnvironmentKey: hasEnvironmentApiKey(),
      debug: runtime.options.debug,
      version: VERSION,
      language: resolveLanguageOrDefault(runtime.options),
    }));
    return;
  }
  out(renderLines(statusScreen(runtime.config, runtime.render, {
    debug: runtime.options.debug,
    configFile: configPath(),
  })));
}

function runHelp(runtime: Runtime): void {
  const { options, render } = runtime;
  if (options.json) {
    out(`${JSON.stringify({
      version: VERSION,
      commands: ['tui', 'setup', 'send', 'channels', 'status', 'agent', 'models', 'config', 'history', 'clear', 'help', 'version'],
    }, null, 2)}\n`);
    return;
  }
  out(renderLines(helpScreen(render, VERSION)));
}

async function launchTui(runtime: Runtime, mode: 'chat' | 'setup'): Promise<void> {
  if (!isInteractive()) {
    if (mode === 'setup') {
      out(renderLines(helpScreen(runtime.render, VERSION)));
      return;
    }
    await runSession(runtime);
    return;
  }
  // A pty can report zero columns before it is sized, so fall back deliberately.
  const columns = runtime.options.width ?? (process.stdout.columns > 0 ? process.stdout.columns : detectWidth());
  const rows = process.stdout.rows > 0 ? process.stdout.rows : 24;
  const width = clampWidth(columns);
  const height = Math.max(12, Math.min(rows, 200));
  const screen = enterFullScreen();
  try {
    // One full-screen surface, chosen by the setting rather than by the command.
    // `phone setup` is the one case that overrides it: a first run must be able
    // to reach the setup before it has an agent to talk to.
    const useAgent = runtime.config.settings.interface === 'agent' && mode !== 'setup';
    const element = useAgent
      ? React.createElement(AgentTui, {
        debug: runtime.options.debug,
        depth: runtime.depth,
        height,
        initialConfig: runtime.config,
        width,
      })
      : React.createElement(Tui, {
        debug: runtime.options.debug,
        depth: runtime.depth,
        height,
        initialConfig: runtime.config,
        mode,
        width,
      });
    const instance = render(element, { exitOnCtrlC: false });
    await instance.waitUntilExit();
  } finally {
    screen.restore();
  }
}

async function* sessionLines(): AsyncGenerator<string> {
  if (!process.stdin.isTTY) {
    for (const line of await readPipedLines(MAX_PROMPT_LENGTH)) yield line;
    return;
  }
  const rl = createInterface({ input: process.stdin, output: process.stderr, prompt: '❯ ' });
  try {
    for await (const line of rl) {
      if (line.trim()) yield line;
    }
  } finally {
    rl.close();
  }
}

/**
 * The fallback when there is no TTY: a line-oriented session, so `phone` still
 * works inside a pipe or a script rather than refusing to start.
 */
async function runSession(runtime: Runtime): Promise<void> {
  const { config, render, options } = runtime;
  // Resolved once for the whole session, not per line: the door is a property of
  // this session, and asking the bridge about it before every line of a piped
  // file would make a hundred-line script a hundred round trips to find out
  // something that cannot have changed underneath it.
  const destination = await resolveDestination(options);
  const session = createPhoneClient(config, destination);
  const { client } = session;
  client.connect();
  try {
    const blocked = await sessionProblem(session, render.language);
    if (blocked) {
      if (options.json) {
        out(`${JSON.stringify({ error: blocked }, null, 2)}\n`);
      } else {
        out(`${renderLines([`  ${blocked}`])}\n`);
      }
      process.exitCode = 1;
      return;
    }
    for await (const line of sessionLines()) {
      const message = cleanMessage(line);
      const reply = await client.sendMessage(message);
      const failure = client.getSnapshot().lastError;
      if (options.json) {
        out(`${JSON.stringify({ role: 'user', text: message, reply: reply?.text ?? null, error: failure })}\n`);
      } else {
        out(renderLines(outgoingScreen(message, render)));
        out(renderLines(replyScreen(reply, render)));
        if (!reply && failure) {
          process.stderr.write(`${sanitizeTerminalText(failure)}\n`);
          process.exitCode = 1;
        }
      }
    }
    const snapshot = client.getSnapshot();
    saveConfig(
      config.settings.interface === 'agent'
        ? { ...config, settings: snapshot.settings }
        : { ...config, settings: snapshot.settings, messages: snapshot.messages },
      { origin: 'cli' },
    );
  } finally {
    session.agent?.dispose();
    client.dispose();
  }
  if (!options.json) {
    const t = createTranslator(render.language);
    out(`\n  ${runtime.palette.faint(t('cliReplHint'))}\n\n`);
  }
}

async function run(options: CliOptions): Promise<number> {
  const runtime = buildRuntime(options);
  switch (options.command) {
    case 'help':
      runHelp(runtime);
      return 0;
    case 'version':
      if (options.json) out(`${JSON.stringify({ version: VERSION })}\n`);
      else out(`${PRODUCT_NAME} — released ${RELEASE_DATE} (${RELEASE_TIMEZONE})\n`);
      return 0;
    case 'status':
      runStatus(runtime);
      return 0;
    case 'agent':
      return runAgent(runtime);
    case 'models':
      await runModels(runtime);
      return 0;
    case 'config':
      return runConfig(runtime);
    case 'history':
      runHistory(runtime);
      return 0;
    case 'clear':
      runClear(runtime);
      return 0;
    case 'send':
      return runSend(runtime);
    case 'channels':
      return runChannels(runtime);
    case 'setup':
      await launchTui(runtime, 'setup');
      return 0;
    default:
      await launchTui(runtime, 'chat');
      return 0;
  }
}

export async function main(argv: string[] = process.argv.slice(2)): Promise<number> {
  let options: CliOptions;
  try {
    options = parseArguments(argv);
  } catch (error) {
    reportError(error, storedLanguage());
    return 1;
  }
  try {
    return await run(options);
  } catch (error) {
    reportError(error, resolveLanguageOrDefault(options));
    return 1;
  }
}

function resolveLanguageOrDefault(options: CliOptions): Language {
  return options.language ?? storedLanguage();
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  void main().then((code) => {
    if (code !== 0) process.exitCode = code;
  });
}

export { run as runCli };
export type { PhoneConfig };
