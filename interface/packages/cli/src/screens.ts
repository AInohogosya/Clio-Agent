import {
  createTranslator,
  languageLabel,
  providerFor,
  sanitizeTerminalText,
  type ChatMessage,
  type Language,
  type ModelDiscoveryResult,
  type Settings,
  type SharedState,
  type TranslationKey,
} from '@project-phone/core';
import { padWidth, truncateWidth, type Palette } from './palette.js';
import {
  banner,
  bulletList,
  clampWidth,
  formatDateTime,
  helpColumn,
  keyValue,
  meter,
  note,
  panel,
  renderMessage,
  rule,
  sectionLabel,
  type RenderOptions,
} from './render.js';
import { CONFIG_FIELDS, type ConfigField } from './args.js';
import { hasEnvironmentApiKey } from './config.js';
import { readConfigField } from './config-fields.js';

/** Where the live credential comes from, in the order a reader should care about. */
export function credentialSourceOf(state: SharedState): 'none' | 'file' | 'environment' {
  if (hasEnvironmentApiKey()) return 'environment';
  return state.credential.present ? 'file' : 'none';
}

export function credentialSummary(state: SharedState, palette: Palette, language: Language): string {
  const t = createTranslator(language);
  const source = credentialSourceOf(state);
  if (source === 'environment') return palette.good(t('syncKeyEnvironment'));
  if (source === 'file') {
    return `${palette.good('●')} ${palette.muted(t('syncKeyOnFile'))} ${palette.faint(state.credential.hint)}`;
  }
  return palette.warn(`○ ${t('syncKeyNone')}`);
}

const LABEL_WIDTH = 16;

export function settingsRows(state: SharedState, options: RenderOptions): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const settings = state.settings;
  const definition = providerFor(settings.provider);
  const configured = Boolean(settings.model && settings.baseUrl);
  return [
    keyValue(t('cliProvider'), `${palette.inkText(definition.label)} ${palette.faint(settings.provider)}`, palette, width),
    keyValue(t('cliModel'), palette.inkText(settings.model || t('cliNotConfigured')), palette, width),
    keyValue(t('cliEndpoint'), palette.muted(settings.baseUrl || t('cliNoEndpoint')), palette, width),
    keyValue(t('cliKey'), credentialSummary(state, palette, language), palette, width),
    keyValue(t('cliLanguage'), palette.muted(languageLabel(settings.language)), palette, width),
    keyValue(t('cliTheme'), palette.muted(settings.theme), palette, width),
    keyValue(t('cliAccent'), `${palette.accentText(settings.accent)}`, palette, width),
    keyValue(t('configuration'), configured ? palette.good(`● ${t('cliConfigured')}`) : palette.warn(`○ ${t('cliNotConfigured')}`), palette, width),
  ];
}

export function statusScreen(state: SharedState, options: RenderOptions, extra: { debug: boolean; configFile: string }): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const lines: string[] = [];
  lines.push('');
  lines.push(...banner(options, state, 'offline'));
  lines.push('');
  lines.push(`  ${sectionLabel(t('configuration'), palette)}`);
  lines.push(...settingsRows(state, options));
  lines.push('');
  lines.push(`  ${sectionLabel(t('syncTitle'), palette)}`);
  lines.push(
    keyValue(
      t('cliTransportFile'),
      `${palette.good('●')} ${palette.muted(t('syncBridgeLive'))}`,
      palette,
      width,
    ),
  );
  lines.push(keyValue(t('syncRevision'), palette.inkText(String(state.revision)), palette, width, LABEL_WIDTH));
  lines.push(
    keyValue(
      t('cliUpdatedAt'),
      palette.muted(state.updatedAt > 0 ? formatDateTime(state.updatedAt, language) : '—'),
      palette,
      width,
    ),
  );
  lines.push(keyValue(t('cliConfigFile'), palette.faint(extra.configFile), palette, width, LABEL_WIDTH));
  lines.push('');
  lines.push(`  ${sectionLabel(t('cliActivity'), palette)}`);
  const meterWidth = Math.max(8, width - LABEL_WIDTH - 9);
  const last = state.messages.at(-1);
  lines.push(
    `  ${palette.faint(padWidth(t('cliMessages'), LABEL_WIDTH))}  ${palette.inkText(padWidth(String(state.messages.length), 3))}  `
    + meter(state.messages.length, Math.max(state.messages.length, 1), meterWidth, palette, 'accent'),
  );
  lines.push(
    `  ${palette.faint(padWidth(t('cliLastActivity'), LABEL_WIDTH))}  ${palette.muted(
      last ? formatDateTime(last.createdAt, language) : '—',
    )}`,
  );
  if (extra.debug) {
    lines.push('');
    lines.push(`  ${sectionLabel(t('debug'), palette)}`);
    lines.push(`  ${palette.faint(t('debugOn'))} · ${palette.faint(`origin=${state.origin ?? 'none'} transport=${state.transport}`)}`);
  }
  lines.push('');
  lines.push(`  ${note(t('cliSyncNote'), palette, 'accent')}`);
  lines.push('');
  return lines;
}

export function historyScreen(state: SharedState, options: RenderOptions, limit: number): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const messages = state.messages.slice(-Math.max(1, limit));
  const lines: string[] = [];
  lines.push('');
  lines.push(...panel({
    width,
    tone: 'accent',
    title: t('cliTranscript'),
    badge: `${messages.length}/${state.messages.length}`,
    body: [],
  }, palette));
  lines.push('');
  if (messages.length === 0) {
    lines.push(`  ${note(t('cliNoMessages'), palette, 'muted')}`);
    lines.push('');
    return lines;
  }
  for (const message of messages) {
    lines.push(...renderMessage(message, options, 2));
    lines.push('');
  }
  return lines;
}

export function modelsScreen(
  state: SharedState,
  result: ModelDiscoveryResult,
  options: RenderOptions,
): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const lines: string[] = [];
  const sourceLabel = result.source === 'remote' ? t('cliRemote') : t('cliOffline');
  lines.push('');
  lines.push(...panel({
    width,
    title: t('discovery'),
    subtitle: sanitizeTerminalText(state.settings.baseUrl),
    badge: sourceLabel,
    badgeTone: result.source === 'remote' ? 'success' : 'warning',
    body: [
      `  ${meter(result.models.length, Math.max(result.models.length, 1), Math.max(8, width - 6), palette, result.source === 'remote' ? 'success' : 'warning')}`,
      `  ${palette.faint(t('cliModelCount', { count: result.models.length }))}`,
    ],
    footer: result.source === 'remote' ? t('discovered') : t('offlineFallback'),
  }, palette));
  lines.push('');
  if (result.models.length === 0) {
    lines.push(`  ${note(t('noModels'), palette, 'danger')}`);
    lines.push('');
    return lines;
  }
  const active = state.settings.model;
  const shown = result.models.slice(0, 200);
  for (const model of shown) {
    const isActive = model === active;
    const marker = isActive ? palette.accentText('▸') : ' ';
    const name = truncateWidth(sanitizeTerminalText(model), width - 12);
    lines.push(`  ${marker} ${isActive ? palette.accentText(name) : palette.muted(name)}${isActive ? palette.faint('  ●') : ''}`);
  }
  if (result.models.length > shown.length) {
    lines.push(`  ${palette.faint(`+${result.models.length - shown.length}`)}`);
  }
  lines.push('');
  return lines;
}

export function configScreen(state: SharedState, options: RenderOptions, field?: string, value?: string): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const lines: string[] = [];
  lines.push('');
  if (field && value !== undefined) {
    lines.push(...panel({
      width,
      tone: 'accent',
      title: t('cliSettings'),
      badge: field,
      body: [keyValue(field, palette.inkText(value), palette, width, LABEL_WIDTH)],
      footer: t('cliSavedShared'),
    }, palette));
    lines.push('');
    return lines;
  }
  const rows: Array<[string, string]> = [];
  for (const name of CONFIG_FIELDS) {
    const field = name as ConfigField;
    rows.push([name, configValue(state, field, palette)]);
  }
  lines.push(`  ${sectionLabel(t('cliConfigList'), palette)}`);
  for (const [name, rendered] of rows) {
    lines.push(`  ${palette.accentText(padWidth(name, 12))}${rendered}`);
  }
  lines.push('');
  lines.push(`  ${rule(Math.max(10, width - 4), palette)}`);
  lines.push(`  ${note(t('cliConfigGet'), palette, 'muted')}  ${palette.faint('phone config get provider')}`);
  lines.push(`  ${note(t('cliConfigSet'), palette, 'muted')}  ${palette.faint('phone config set model gpt-4o-mini')}`);
  lines.push(`  ${note(t('cliConfigUnset'), palette, 'muted')}  ${palette.faint('phone config unset accent')}`);
  lines.push('');
  return lines;
}

/**
 * How each field is painted in the settings list. The value itself always comes
 * from `readConfigField`, so the list and `phone config get` cannot disagree
 * about what a field holds.
 */
const FIELD_PAINTERS: Record<ConfigField, (value: string, state: SharedState, palette: Palette) => string> = {
  interface: (value, _state, palette) => (value === 'agent' ? palette.accentText(value) : palette.muted(value)),
  agentUrl: (value, _state, palette) => palette.muted(value),
  agentPerson: (value, _state, palette) => palette.inkText(value),
  provider: (value, _state, palette) => palette.inkText(value),
  model: (value, _state, palette) => palette.inkText(value),
  baseUrl: (value, _state, palette) => palette.muted(value),
  apiKey: (value, _state, palette) => (value
    ? `${palette.good('set')} ${palette.faint(value)}`
    : palette.warn('unset')),
  language: (value, state, palette) => palette.muted(`${value} (${languageLabel(state.settings.language)})`),
  theme: (value, _state, palette) => palette.muted(value),
  accent: (value, _state, palette) => palette.accentText(value),
};

export function configValue(state: SharedState, field: ConfigField, palette: Palette): string {
  return FIELD_PAINTERS[field](readConfigField(state, field), state, palette);
}

export function helpScreen(options: RenderOptions, version: string): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const commands: Array<[string, TranslationKey]> = [
    ['phone', 'cliCommandTui'],
    ['phone setup', 'cliRunSetup'],
    ['phone send "…"', 'cliCommandSend'],
    ['phone send --channel <name>', 'cliCommandSendChannel'],
    ['phone channels', 'cliCommandChannels'],
    ['phone channels set <door>.<field>', 'cliCommandChannelsSet'],
    ['phone status', 'cliCommandStatus'],
    ['phone agent', 'cliCommandAgent'],
    ['phone agent pause|resume|stop', 'cliCommandAgentLifecycle'],
    ['phone agent undo <id>', 'cliCommandAgentUndo'],
    ['phone agent cancel <id>', 'cliCommandAgentCancel'],
    ['phone models', 'cliCommandModels'],
    ['phone config', 'cliCommandConfig'],
    ['phone history', 'cliCommandHistory'],
    ['phone clear', 'cliCommandClear'],
    ['phone help', 'cliCommandHelp'],
    ['phone version', 'cliCommandVersion'],
  ];
  const flags: Array<[string, TranslationKey]> = [
    ['--debug', 'cliOptDebug'],
    ['--json', 'cliOptJson'],
    ['--no-color', 'cliOptNoColor'],
    ['--lang <code>', 'cliOptLanguage'],
    ['--width <n>', 'cliOptWidth'],
    ['--channel <name>', 'cliOptChannel'],
    ['--to <address>', 'cliOptTo'],
  ];
  const translated = (entries: Array<[string, TranslationKey]>): Array<[string, string]> =>
    entries.map(([key, label]) => [key, t(label)]);
  const lines: string[] = [];
  lines.push('');
  lines.push(...panel({
    width,
    tone: 'accent',
    title: `◉ ${t('appName')}`,
    subtitle: `${t('appTagline')} · ${version}`,
    body: [
      '',
      `  ${palette.bold(t('cliCommands'))}`,
      ...helpColumn(translated(commands), palette, 18),
      '',
      `  ${palette.bold(t('cliOptions'))}`,
      ...helpColumn(translated(flags), palette, 18),
      '',
      `  ${palette.bold(t('cliExamples'))}`,
      ...bulletList([
        palette.muted('echo "hello" | phone send'),
        palette.muted('phone send "what is new?" --json'),
        palette.muted('phone agent --json'),
        palette.muted('phone config set provider ollama'),
        palette.muted('phone models | less'),
        palette.muted('PROJECT_PHONE_API_KEY=sk-… phone status'),
      ], palette),
    ],
    footer: t('cliSecretViaEnv'),
  }, palette));
  lines.push('');
  return lines;
}

export function outgoingScreen(message: string, options: RenderOptions): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const lines: string[] = [];
  lines.push('');
  lines.push(...panel({
    width,
    tone: 'accent',
    title: t('liveLine'),
    badge: t('cliUser'),
    badgeTone: 'accent',
    body: renderMessage(
      {
        id: 'outgoing',
        role: 'user',
        text: message,
        createdAt: Date.now(),
      },
      options,
      2,
    ),
  }, palette));
  lines.push('');
  return lines;
}

export function replyScreen(reply: ChatMessage | undefined, options: RenderOptions): string[] {
  const { palette, language } = options;
  const t = createTranslator(language);
  const lines: string[] = [];
  if (!reply) {
    lines.push(`  ${note(t('cliNothingToShow'), palette, 'warning')}`);
    lines.push('');
    return lines;
  }
  lines.push(...renderMessage(reply, options, 2));
  lines.push('');
  return lines;
}

export function clearScreen(options: RenderOptions, removed: number): string[] {
  const { palette, language } = options;
  const t = createTranslator(language);
  return [
    '',
    `  ${note(t('cliCleared'), palette, 'success')} ${palette.faint(`(${removed})`)}`,
    '',
  ];
}

export function renderLines(lines: string[]): string {
  return `${lines.join('\n')}\n`;
}

export function screenOptions(
  settings: Settings,
  palette: Palette,
  width?: number,
  languageOverride?: Language,
): RenderOptions {
  return {
    palette,
    width: clampWidth(width),
    language: languageOverride ?? settings.language,
  };
}

export function jsonPayload(state: SharedState, extra: Record<string, unknown> = {}): string {
  return `${JSON.stringify({
    revision: state.revision,
    updatedAt: state.updatedAt,
    origin: state.origin,
    transport: state.transport,
    settings: { ...state.settings, apiKey: '' },
    credential: state.credential,
    credentialSource: credentialSourceOf(state),
    messages: state.messages,
    ...extra,
  }, null, 2)}\n`;
}
