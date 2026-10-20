import stringWidth from 'string-width';
import { DEFAULT_ACCENT, type Theme } from '@project-phone/core';

export type ColorDepth = 0 | 4 | 8 | 24;

export interface Rgb {
  r: number;
  g: number;
  b: number;
}

const ANSI_PATTERN = /[\u001B\u009B][[\]()#;?]*(?:[0-9]{1,4}(?:;[0-9]{0,4})*)?[0-9A-ORZcf-nqry=><]/g;

export function stripAnsi(value: string): string {
  return value.replace(ANSI_PATTERN, '');
}

export function displayWidth(value: string): number {
  return stringWidth(stripAnsi(value));
}

/** Escape sequences and code points, one per match: the tokens a truncation walks. */
const TOKEN_PATTERN = new RegExp(`${ANSI_PATTERN.source}|.`, 'gu');

function isEscape(token: string): boolean {
  const code = token.charCodeAt(0);
  return code === 0x1b || code === 0x9b;
}

export function truncateWidth(value: string, limit: number, ellipsis = '…'): string {
  if (limit <= 0) return '';
  if (displayWidth(value) <= limit) return value;
  // Truncation keeps the styling a caller applied: escape sequences cost no
  // columns, so the plain characters are budgeted and every escape — the
  // opening codes before the cut and the closing reset after it — is carried
  // through. Stripping them instead would render a long value plainer than a
  // short one, and drop the reset that ends the style.
  const budget = Math.max(0, limit - displayWidth(ellipsis));
  const tokens = value.match(TOKEN_PATTERN) ?? [];
  let kept = '';
  let width = 0;
  let overflow = false;
  let tail = '';
  for (const token of tokens) {
    if (isEscape(token)) {
      if (overflow) tail += token;
      else kept += token;
      continue;
    }
    if (overflow) continue;
    const next = width + stringWidth(token);
    if (next > budget) {
      overflow = true;
      continue;
    }
    kept += token;
    width = next;
  }
  return kept + tail + ellipsis;
}

export function padWidth(value: string, width: number, align: 'left' | 'right' | 'center' = 'left'): string {
  const missing = width - displayWidth(value);
  if (missing <= 0) return value;
  if (align === 'right') return `${' '.repeat(missing)}${value}`;
  if (align === 'center') {
    const left = Math.floor(missing / 2);
    return `${' '.repeat(left)}${value}${' '.repeat(missing - left)}`;
  }
  return `${value}${' '.repeat(missing)}`;
}

function clampChannel(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(255, Math.round(value)));
}

export function hexToRgb(value: string): Rgb {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value.trim());
  if (!match) return hexToRgb(DEFAULT_ACCENT);
  const digits = match[1]!;
  const full = digits.length === 3 ? digits.split('').map((digit) => digit + digit).join('') : digits;
  return {
    r: Number.parseInt(full.slice(0, 2), 16),
    g: Number.parseInt(full.slice(2, 4), 16),
    b: Number.parseInt(full.slice(4, 6), 16),
  };
}

export function rgbToHex({ r, g, b }: Rgb): string {
  const part = (channel: number) => clampChannel(channel).toString(16).padStart(2, '0');
  return `#${part(r)}${part(g)}${part(b)}`.toUpperCase();
}

export function mix(base: Rgb, over: Rgb, weight: number): Rgb {
  const ratio = Math.max(0, Math.min(1, weight));
  return {
    r: base.r + (over.r - base.r) * ratio,
    g: base.g + (over.g - base.g) * ratio,
    b: base.b + (over.b - base.b) * ratio,
  };
}

/** Composites a translucent colour over an opaque backdrop, the way the browser does. */
export function overlay(foreground: Rgb, alpha: number, backdrop: Rgb): Rgb {
  return mix(backdrop, foreground, Math.max(0, Math.min(1, alpha)));
}

function channelLuminance(channel: number): number {
  const value = clampChannel(channel) / 255;
  return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance({ r, g, b }: Rgb): number {
  return 0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b);
}

export function contrastRatio(first: Rgb, second: Rgb): number {
  const a = relativeLuminance(first);
  const b = relativeLuminance(second);
  const lighter = Math.max(a, b);
  const darker = Math.min(a, b);
  return (lighter + 0.05) / (darker + 0.05);
}

const CUBE_STEPS = [0, 95, 135, 175, 215, 255];

function nearestCubeIndex(value: number): number {
  let best = 0;
  let distance = Number.POSITIVE_INFINITY;
  for (let index = 0; index < CUBE_STEPS.length; index += 1) {
    const delta = Math.abs(CUBE_STEPS[index]! - clampChannel(value));
    if (delta < distance) {
      distance = delta;
      best = index;
    }
  }
  return best;
}

function ansi256Index(rgb: Rgb): number {
  const r = nearestCubeIndex(rgb.r);
  const g = nearestCubeIndex(rgb.g);
  const b = nearestCubeIndex(rgb.b);
  if (r === g && g === b) {
    const level = Math.max(0, Math.min(23, Math.round((clampChannel(rgb.r) - 8) / 10)));
    return 232 + level;
  }
  return 16 + 36 * r + 6 * g + b;
}

const BASE_HUES: Array<[number, number, number]> = [
  [0, 0, 0],
  [205, 0, 0],
  [0, 205, 0],
  [205, 205, 0],
  [0, 0, 205],
  [205, 0, 205],
  [0, 205, 205],
  [205, 205, 205],
];

function ansi16Index(rgb: Rgb): number {
  const bright = relativeLuminance(rgb) > 0.32 ? 60 : 0;
  let best = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  for (let index = 0; index < BASE_HUES.length; index += 1) {
    const [r, g, b] = BASE_HUES[index]!;
    const distance = (rgb.r - r) ** 2 + (rgb.g - g) ** 2 + (rgb.b - b) ** 2;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = index;
    }
  }
  return bright + best;
}

function paint(rgb: Rgb, depth: ColorDepth, background: boolean): string {
  const lead = background ? 48 : 38;
  if (depth === 24) {
    return `\u001B[${lead};2;${rgb.r};${rgb.g};${rgb.b}m`;
  }
  if (depth === 8) {
    return `\u001B[${lead};5;${ansi256Index(rgb)}m`;
  }
  if (depth === 4) {
    return `\u001B[${lead};5;${ansi16Index(rgb)}m`;
  }
  return '';
}

function foreground(rgb: Rgb, depth: ColorDepth): string {
  if (depth === 24) return `\u001B[38;2;${clampChannel(rgb.r)};${clampChannel(rgb.g)};${clampChannel(rgb.b)}m`;
  if (depth === 8) return `\u001B[38;5;${ansi256Index(rgb)}m`;
  if (depth === 4) {
    const index = ansi16Index(rgb);
    return index < 8 ? `\u001B[3${index}m` : `\u001B[9${index - 8}m`;
  }
  return '';
}

export function detectColorDepth(
  env: NodeJS.ProcessEnv = process.env,
  stream: { isTTY?: boolean } = process.stdout,
): ColorDepth {
  const force = (env.FORCE_COLOR ?? '').trim().toLowerCase();
  if (force === '0' || force === 'false' || env.NO_COLOR !== undefined) return 0;
  if (force === '1' || force === '2') return 8;
  if (force === '3' || force === 'true') return 24;
  if (env.TERM === 'dumb') return 0;
  if (!stream.isTTY) return 0;
  const colorterm = (env.COLORTERM ?? '').toLowerCase();
  if (colorterm === 'truecolor' || colorterm === '24bit') return 24;
  const term = (env.TERM ?? '').toLowerCase();
  if (/-256(color)?$/.test(term)) return 8;
  if (term === 'xterm-kitty' || term === 'wezterm' || term === 'alacritty') return 24;
  return 4;
}

export interface ThemeTokens {
  canvas: string;
  canvasRaised: string;
  canvasWell: string;
  ink: string;
  inkMuted: string;
  inkFaint: string;
  line: string;
  lineStrong: string;
  success: string;
  warning: string;
  danger: string;
  onAccent: string;
}

const DARK_TOKENS: ThemeTokens = {
  canvas: '#101210',
  canvasRaised: '#171A17',
  canvasWell: '#0C0E0C',
  ink: '#F0EEE8',
  inkMuted: '#989C93',
  inkFaint: '#646A61',
  line: '#20241F',
  lineStrong: '#333831',
  success: '#34D399',
  warning: '#FBBF24',
  danger: '#FB7185',
  onAccent: '#17120E',
};

const LIGHT_TOKENS: ThemeTokens = {
  canvas: '#EFEDE7',
  canvasRaised: '#FBFAF7',
  canvasWell: '#E5E2DA',
  ink: '#1B1A16',
  inkMuted: '#4E4B43',
  inkFaint: '#645E53',
  line: '#DBD8D1',
  lineStrong: '#C6C2BA',
  success: '#047857',
  warning: '#B45309',
  danger: '#BE123C',
  onAccent: '#FFFFFF',
};

export interface Palette {
  depth: ColorDepth;
  theme: Theme;
  accent: string;
  canvas: string;
  canvasRaised: string;
  canvasWell: string;
  ink: string;
  inkMuted: string;
  inkFaint: string;
  line: string;
  lineStrong: string;
  accentSoft: string;
  accentFaint: string;
  success: string;
  warning: string;
  danger: string;
  onAccent: string;
  color: (value: string) => string;
  bg: (value: string) => string;
  code: (value: string) => string;
  bgCode: (value: string) => string;
  reset: string;
  bold: (value: string) => string;
  dim: (value: string) => string;
  italic: (value: string) => string;
  underline: (value: string) => string;
  inverse: (value: string) => string;
  accentText: (value: string) => string;
  inkText: (value: string) => string;
  muted: (value: string) => string;
  faint: (value: string) => string;
  good: (value: string) => string;
  warn: (value: string) => string;
  bad: (value: string) => string;
  label: (value: string) => string;
  badge: (value: string) => string;
  pill: (value: string, tone?: Tone) => string;
  style: (spec: StyleName, value: string) => string;
}

export type Tone = 'accent' | 'success' | 'warning' | 'danger' | 'muted' | 'plain';
export type StyleName = Tone;

/**
 * The tones a transcript line can be painted in: a `Tone` for anything that
 * carries meaning, plus the three shades of ink for body text, which are
 * quieter than any of the semantic colours.
 */
export type LineTone = Tone | 'ink' | 'faint' | 'dim';

function wrap(depth: ColorDepth, codes: string[], value: string): string {
  if (depth === 0 || !value) return value;
  return `\u001B[${codes.join(';')}m${value}\u001B[0m`;
}

export function createPalette(accent: string, theme: Theme, depth: ColorDepth): Palette {
  const tokens = theme === 'light' ? LIGHT_TOKENS : DARK_TOKENS;
  const accentRgb = hexToRgb(accent);
  const canvasRgb = hexToRgb(tokens.canvas);
  const accentSoft = overlay(accentRgb, 0.16, canvasRgb);
  const accentFaint = overlay(accentRgb, 0.08, canvasRgb);
  const onAccent = contrastRatio(hexToRgb(tokens.onAccent), accentRgb) >= 3.2
    ? tokens.onAccent
    : (theme === 'light' ? '#1B1A16' : '#0C0E0C');
  const accentHex = rgbToHex(accentRgb);

  const off = depth === 0 ? '' : '\u001B[0m';
  const paint2 = (color: string, value: string): string =>
    `${foreground(hexToRgb(color), depth)}${value}${off}`;

  const style = (name: Tone, value: string): string => {
    if (depth === 0) return value;
    switch (name) {
      case 'accent':
        return paint2(accentHex, value);
      case 'success':
        return paint2(tokens.success, value);
      case 'warning':
        return paint2(tokens.warning, value);
      case 'danger':
        return paint2(tokens.danger, value);
      case 'muted':
        return paint2(tokens.inkMuted, value);
      default:
        return value;
    }
  };

  return {
    depth,
    theme,
    accent: accentHex,
    canvas: tokens.canvas,
    canvasRaised: tokens.canvasRaised,
    canvasWell: tokens.canvasWell,
    ink: tokens.ink,
    inkMuted: tokens.inkMuted,
    inkFaint: tokens.inkFaint,
    line: tokens.line,
    lineStrong: tokens.lineStrong,
    accentSoft: rgbToHex(accentSoft),
    accentFaint: rgbToHex(accentFaint),
    success: tokens.success,
    warning: tokens.warning,
    danger: tokens.danger,
    onAccent,
    color: (value) => paint(hexToRgb(value), depth, false) + value + off,
    bg: (value) => paint(hexToRgb(value), depth, true) + value + off,
    code: (value) => paint(hexToRgb(value), depth, false),
    bgCode: (value) => paint(hexToRgb(value), depth, true),
    reset: off,
    bold: (value) => wrap(depth, ['1'], value),
    dim: (value) => wrap(depth, ['2'], value),
    italic: (value) => wrap(depth, ['3'], value),
    underline: (value) => wrap(depth, ['4'], value),
    inverse: (value) => wrap(depth, ['7'], value),
    accentText: (value) => style('accent', value),
    inkText: (value) => paint2(tokens.ink, value),
    muted: (value) => style('muted', value),
    faint: (value) => paint2(tokens.inkFaint, value),
    good: (value) => style('success', value),
    warn: (value) => style('warning', value),
    bad: (value) => style('danger', value),
    label: (value) => wrap(depth, ['1'], style('muted', value)),
    badge: (value) => paint2(accentHex, value),
    pill: (value, tone = 'accent') => style(tone, value),
    style,
  };
}

export function toneHex(palette: Palette, tone: Tone): string {
  switch (tone) {
    case 'success':
      return palette.success;
    case 'warning':
      return palette.warning;
    case 'danger':
      return palette.danger;
    case 'muted':
      return palette.inkMuted;
    case 'plain':
      return palette.ink;
    default:
      return palette.accent;
  }
}

/** The colour a transcript line is drawn in, for surfaces that paint by name. */
export function lineToneHex(palette: Palette, tone: LineTone): string {
  if (tone === 'ink') return palette.ink;
  if (tone === 'faint') return palette.inkFaint;
  if (tone === 'dim') return palette.inkMuted;
  return toneHex(palette, tone);
}

export const ACCENT_PRESETS: string[] = [
  '#F97316',
  '#D97706',
  '#EA580C',
  '#C2410C',
  '#E11D48',
  '#DB2777',
  '#A855F7',
  '#4F46E5',
  '#0EA5E9',
  '#0D9488',
  '#16A34A',
  '#CA8A04',
];

export function nearestAccent(accent: string): number {
  const target = hexToRgb(accent);
  let best = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  ACCENT_PRESETS.forEach((preset, index) => {
    const candidate = hexToRgb(preset);
    const distance = (candidate.r - target.r) ** 2 + (candidate.g - target.g) ** 2 + (candidate.b - target.b) ** 2;
    if (distance < bestDistance) {
      bestDistance = distance;
      best = index;
    }
  });
  return best;
}

export function accentStep(accent: string, direction: 1 | -1): string {
  const index = nearestAccent(accent);
  const next = (index + direction + ACCENT_PRESETS.length) % ACCENT_PRESETS.length;
  return ACCENT_PRESETS[next]!;
}

export const GLYPHS = {
  rounded: {
    topLeft: '╭', topRight: '╮', bottomLeft: '╰', bottomRight: '╯',
    horizontal: '─', vertical: '│', top: '┌', tee: '┬', bottom: '└', pipe: '┤',
  },
  single: {
    topLeft: '┌', topRight: '┐', bottomLeft: '└', bottomRight: '┘',
    horizontal: '─', vertical: '│', top: '┌', tee: '┬', bottom: '└', pipe: '┤',
  },
  double: {
    topLeft: '╔', topRight: '╗', bottomLeft: '╚', bottomRight: '╝',
    horizontal: '═', vertical: '║', top: '╒', tee: '╦', bottom: '╚', pipe: '╡',
  },
} as const;

export type GlyphStyle = keyof typeof GLYPHS;

const UNICODE_RICH = process.platform !== 'win32'
  && (process.env.TERM ?? '') !== 'linux'
  && !/^(C|POSIX)$/.test(process.env.TERM ?? '');

export function glyphs(): Record<keyof typeof GLYPHS.single, string> {
  return UNICODE_RICH ? GLYPHS.rounded : GLYPHS.single;
}

export const BULLETS = ['●', '◆', '▸', '▎', '·', '•', '‣', '⁃'];
export const SPINNER_FRAMES = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
export const BAR_FULL = '█';
export const BAR_PARTIALS = ['', '▏', '▎', '▍', '▌', '▋', '▊', '▉'];
export const ASCII_BAR_FULL = '#';
export const ASCII_SPINNER = ['-', '\\', '|', '/'];
