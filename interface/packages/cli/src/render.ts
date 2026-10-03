import {
  createTranslator,
  sanitizeTerminalText,
  type ChatMessage,
  type ConnectionStatus,
  type Language,
  type SharedState,
} from '@project-phone/core';
import {
  BULLETS,
  BAR_FULL,
  BAR_PARTIALS,
  ASCII_BAR_FULL,
  displayWidth,
  glyphs,
  padWidth,
  truncateWidth,
  type Palette,
  type Tone,
} from './palette.js';

export interface RenderOptions {
  palette: Palette;
  width: number;
  language: Language;
}

export const MIN_WIDTH = 32;
export const MAX_WIDTH = 108;

export function clampWidth(value: number | undefined, fallback = 84): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback;
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, Math.floor(value)));
}

export function detectWidth(columns?: number): number {
  if (typeof columns === 'number' && Number.isFinite(columns)) return clampWidth(columns);
  const env = process.env.COLUMNS ? Number.parseInt(process.env.COLUMNS, 10) : Number.NaN;
  if (Number.isFinite(env)) return clampWidth(env);
  if (process.stdout.isTTY && typeof process.stdout.columns === 'number') return clampWidth(process.stdout.columns);
  return 84;
}

function plain(value: string): string {
  return sanitizeTerminalText(value);
}

export function rule(width: number, palette: Palette, tone: 'line' | 'lineStrong' = 'line'): string {
  const body = glyphs().horizontal.repeat(Math.max(0, width));
  if (palette.depth === 0) return body;
  return palette.code(tone === 'lineStrong' ? palette.lineStrong : palette.line) + body + palette.reset;
}

export function sectionLabel(label: string, palette: Palette): string {
  return palette.label(plain(label).toUpperCase());
}

export interface PanelOptions {
  title?: string;
  subtitle?: string;
  badge?: string;
  badgeTone?: Tone;
  width: number;
  body?: string[];
  footer?: string;
  tone?: 'line' | 'accent' | 'lineStrong';
}

/** A rounded "instrument panel": the terminal twin of `.instrument-panel`. */
export function panel(options: PanelOptions, palette: Palette): string[] {
  const glyph = glyphs();
  const width = Math.max(MIN_WIDTH + 2, options.width);
  const inner = width - 2;
  const content = inner - 2;
  const toneColor = options.tone === 'accent'
    ? palette.accent
    : options.tone === 'lineStrong' ? palette.lineStrong : palette.line;

  const paint = (text: string): string => (palette.depth === 0 ? text : palette.code(toneColor) + text + palette.reset);
  // Body rows are truncated as well as padded. A caller that put something long
  // in a cell — a focus title, a marker strip, a model name — would otherwise
  // push the right-hand border off the edge of the panel and wrap the whole box,
  // which is how a panel that fits on one screen ends up taller than the screen.
  const row = (rendered: string): string =>
    `${paint(glyph.vertical)} ${padWidth(truncateWidth(rendered, content), content)} ${paint(glyph.vertical)}`;
  const divider = (): string => `${paint(glyph.vertical)}${paint(glyph.horizontal.repeat(Math.max(0, inner)))}${paint(glyph.vertical)}`;
  const lines: string[] = [];

  if (options.title) {
    const title = truncateWidth(plain(options.title), Math.max(4, content - 8));
    let remaining = content - displayWidth(title) - 1;
    if (options.badge) {
      const badge = ` ${truncateWidth(plain(options.badge), Math.max(1, content - displayWidth(title) - 10))} `;
      remaining -= displayWidth(badge);
      const tone = options.badgeTone ?? 'accent';
      lines.push(
        `${paint(glyph.topLeft)}${paint(glyph.horizontal)} ${palette.bold(palette.inkText(title))} `
        + `${palette.bgCode(palette.accentSoft)}${palette.style(tone, badge)}${palette.reset}`
        + `${paint(glyph.horizontal.repeat(Math.max(0, remaining)))}${paint(glyph.topRight)}`,
      );
    } else {
      lines.push(
        `${paint(glyph.topLeft)}${paint(glyph.horizontal)} ${palette.bold(palette.inkText(title))} `
        + `${paint(glyph.horizontal.repeat(Math.max(0, remaining)))}${paint(glyph.topRight)}`,
      );
    }
  } else {
    lines.push(`${paint(glyph.topLeft)}${paint(glyph.horizontal.repeat(Math.max(0, inner)))}${paint(glyph.topRight)}`);
  }

  if (options.subtitle) {
    lines.push(row(truncateWidth(palette.faint(plain(options.subtitle)), content)));
  }

  for (const line of options.body ?? []) {
    for (const rendered of String(line).split('\n')) {
      lines.push(row(rendered));
    }
  }

  if (options.footer) {
    lines.push(divider());
    lines.push(row(truncateWidth(palette.faint(plain(options.footer)), content)));
  }

  lines.push(`${paint(glyph.bottomLeft)}${paint(glyph.horizontal.repeat(Math.max(0, inner)))}${paint(glyph.bottomRight)}`);
  return lines;
}

export function keyValue(label: string, value: string, palette: Palette, width: number, labelWidth = 14): string {
  const text = padWidth(truncateWidth(plain(label), labelWidth), labelWidth);
  const gap = Math.max(1, width - labelWidth - 4);
  return `  ${palette.faint(text)}  ${padWidth(truncateWidth(value, gap), gap)}`;
}

export function meter(value: number, max: number, width: number, palette: Palette, tone: Tone = 'accent'): string {
  const safeMax = Math.max(1, max);
  const ratio = Math.max(0, Math.min(1, value / safeMax));
  const cells = Math.max(0, width);
  const filledExact = ratio * cells;
  const filled = Math.floor(filledExact);
  const partialIndex = Math.floor((filledExact - filled) * 8);
  const full = palette.depth === 0 ? ASCII_BAR_FULL : BAR_FULL;
  const partial = palette.depth === 0 ? ASCII_BAR_FULL : (BAR_PARTIALS[partialIndex] ?? '');
  const empty = palette.depth === 0 ? '.' : '·';
  const filledPart = full.repeat(Math.min(filled, cells));
  const partialPart = filled >= cells ? '' : partial;
  const rest = empty.repeat(Math.max(0, cells - filledPart.length - partialPart.length));
  return palette.style(tone, filledPart + partialPart) + palette.faint(rest);
}

export function bulletList(items: string[], palette: Palette, indent = 2): string[] {
  return items.map((item, index) => {
    const marker = BULLETS[index % BULLETS.length] ?? '·';
    return `${' '.repeat(indent)}${palette.accentText(marker)} ${item}`;
  });
}

export function statusTone(status: ConnectionStatus): Tone {
  if (status === 'online') return 'success';
  if (status === 'thinking' || status === 'connecting') return 'accent';
  return 'muted';
}

export function statusLabelKey(status: ConnectionStatus): 'connected' | 'thinking' | 'offline' {
  if (status === 'online') return 'connected';
  if (status === 'thinking' || status === 'connecting') return 'thinking';
  return 'offline';
}

export function formatTime(timestamp: number, language: Language): string {
  try {
    return new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit' }).format(
      Number.isFinite(timestamp) ? timestamp : Date.now(),
    );
  } catch {
    return '--:--';
  }
}

export function formatDateTime(timestamp: number, language: Language): string {
  try {
    return new Intl.DateTimeFormat(language, {
      year: 'numeric', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
    }).format(Number.isFinite(timestamp) ? timestamp : Date.now());
  } catch {
    return '-';
  }
}

export interface MessageVoice {
  isUser: boolean;
  author: string;
  tone: Tone;
  glyph: string;
}

/**
 * How a message presents itself.
 *
 * Both renderers — the plain-text one below and the full-screen transcript —
 * ask this, so the two surfaces cannot disagree about who is speaking.
 */
export function messageVoice(message: ChatMessage, language: Language): MessageVoice {
  const t = createTranslator(language);
  const isUser = message.role === 'user';
  return {
    isUser,
    author: message.role === 'system' ? t('system') : isUser ? t('you') : t('assistant'),
    tone: isUser ? 'accent' : 'success',
    glyph: message.role === 'system' ? '◇' : isUser ? '▸' : '◆',
  };
}

/**
 * Wraps text to a width. Newlines are split out before sanitising, because the
 * terminal sanitiser strips control characters and would otherwise flatten a
 * multi-line message into one long line.
 */
export function wrapText(value: string, width: number): string[] {
  if (width <= 0) return [''];
  const lines: string[] = [];
  const paragraphs = (typeof value === 'string' ? value : '').replace(/\r\n?/g, '\n').split('\n');
  for (const paragraph of paragraphs) {
    const text = plain(paragraph);
    if (text.trim() === '') {
      lines.push('');
      continue;
    }
    let current = '';
    for (const word of text.split(/\s+/).filter(Boolean)) {
      // A token wider than the column is broken across lines rather than cut.
      for (const chunk of hardBreak(word, width)) {
        if (current === '') {
          current = chunk;
          continue;
        }
        // Measured in display columns, not code units: a wide character is two
        // columns and one or two units, and a wrapper that counted units would
        // emit lines up to twice the requested width for a CJK message.
        if (displayWidth(current) + 1 + displayWidth(chunk) <= width) {
          current = `${current} ${chunk}`;
          continue;
        }
        lines.push(current);
        current = chunk;
      }
    }
    if (current) lines.push(current);
  }
  return lines.length > 0 ? lines : [''];
}

function hardBreak(value: string, width: number): string[] {
  if (displayWidth(value) <= width) return [value];
  const chunks: string[] = [];
  let rest = value;
  while (displayWidth(rest) > width) {
    // Walk characters until the width budget is spent, so a wide character is
    // never split in two: `slice` on code units would cut a surrogate pair in
    // half and count a two-column character as one.
    let used = 0;
    let cut = 0;
    for (const character of rest) {
      const next = used + displayWidth(character);
      if (next > width) break;
      used = next;
      cut += character.length;
    }
    // A character wider than the column still has to go somewhere, or this
    // loop would never advance.
    if (cut === 0) cut = [...rest][0]!.length;
    chunks.push(rest.slice(0, cut));
    rest = rest.slice(cut);
  }
  if (rest) chunks.push(rest);
  return chunks;
}

export function renderMessage(
  message: ChatMessage,
  options: RenderOptions,
  gutter = 0,
): string[] {
  const { palette, width, language } = options;
  const voice = messageVoice(message, language);
  const available = Math.max(12, width - gutter - 2);
  const body = wrapText(message.text, available - 2);
  const head = palette.style(voice.tone, `${voice.glyph} ${voice.author}`)
    + palette.faint(` · ${formatTime(message.createdAt, language)}`);
  const lines = [`${' '.repeat(gutter)}${head}`];
  body.forEach((line, index) => {
    const marker = index === 0 ? palette.style(voice.tone, '│') : ' ';
    const text = voice.isUser ? palette.inkText(line) : palette.muted(line);
    lines.push(`${' '.repeat(gutter)}${marker} ${padWidth(text, available - 2)}`);
  });
  return lines;
}

export function banner(options: RenderOptions, state: SharedState, connection: ConnectionStatus): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const tone = statusTone(connection);
  const label = t(statusLabelKey(connection));
  const title = t('appName');
  return panel({
    width,
    tone: 'accent',
    title: `◉ ${title}`,
    subtitle: t('appTagline'),
    badge: `● ${label}`,
    badgeTone: tone,
    body: [],
  }, palette);
}

export function note(value: string, palette: Palette, tone: Tone = 'muted'): string {
  const glyph = tone === 'danger' ? '✕' : tone === 'warning' ? '!' : tone === 'success' ? '✓' : '·';
  return `${palette.style(tone, glyph)} ${palette.muted(plain(value))}`;
}

export function helpColumn(entries: Array<[string, string]>, palette: Palette, keyWidth = 16): string[] {
  return entries.map(([key, description]) => {
    const padded = padWidth(key, keyWidth);
    // A key longer than the column still keeps one space before its
    // description: a help line that abuts reads as one word, and truncating
    // the key instead would make the command it names unusable.
    const gap = displayWidth(key) >= keyWidth ? ' ' : '';
    return `  ${palette.accentText(padded)}${gap}${palette.muted(plain(description))}`;
  });
}
