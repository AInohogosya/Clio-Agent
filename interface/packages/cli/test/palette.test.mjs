import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import {
  ACCENT_PRESETS,
  accentStep,
  contrastRatio,
  createPalette,
  detectColorDepth,
  displayWidth,
  hexToRgb,
  mix,
  nearestAccent,
  overlay,
  padWidth,
  relativeLuminance,
  rgbToHex,
  stripAnsi,
  toneHex,
  truncateWidth,
} from '../dist/palette.js';
import { createSettings } from '../../core/dist/index.js';
import { clampWidth, keyValue, meter, panel, renderMessage, wrapText } from '../dist/render.js';

const cjk = 'こんにちは';
const emoji = '👋';

test('hex parsing is tolerant of shorthand and casing', () => {
  assert.deepEqual(hexToRgb('#F97316'), { r: 249, g: 115, b: 22 });
  assert.deepEqual(hexToRgb('f97316'), { r: 249, g: 115, b: 22 });
  assert.deepEqual(hexToRgb('#fff'), { r: 255, g: 255, b: 255 });
  assert.deepEqual(hexToRgb('nonsense'), hexToRgb('#F97316'));
  assert.equal(rgbToHex({ r: 249, g: 115, b: 22 }), '#F97316');
  assert.equal(rgbToHex({ r: 300, g: -5, b: 0 }), '#FF0000');
});

test('translucent accents composite onto the canvas like the browser does', () => {
  const canvas = hexToRgb('#101210');
  const accent = hexToRgb('#F97316');
  const soft = overlay(accent, 0.16, canvas);
  assert.ok(soft.r > canvas.r && soft.r < accent.r);
  assert.deepEqual(mix(canvas, accent, 0), canvas);
  assert.deepEqual(mix(canvas, accent, 1), accent);
});

test('contrast is computed with the WCAG relative luminance formula', () => {
  assert.equal(relativeLuminance({ r: 255, g: 255, b: 255 }), 1);
  assert.equal(relativeLuminance({ r: 0, g: 0, b: 0 }), 0);
  const ratio = contrastRatio(hexToRgb('#000000'), hexToRgb('#ffffff'));
  assert.ok(Math.abs(ratio - 21) < 0.01);
  assert.ok(contrastRatio(hexToRgb('#F97316'), hexToRgb('#101210')) > 4);
});

test('display width counts wide characters as two columns', () => {
  assert.equal(displayWidth('abc'), 3);
  assert.equal(displayWidth(cjk), 10);
  assert.equal(displayWidth(emoji), 2);
  assert.equal(displayWidth(`\u001B[31mred\u001B[0m`), 3);
  assert.equal(displayWidth('\u001B[31mred\u001B[0m'), 3);
  // A literal string without ESC is not a colour sequence and keeps its width.
  assert.equal(displayWidth('[31mred[0m'), 10);
  assert.equal(displayWidth('\u001B[38;5;200mwide\u001B[0m'), 4);
});

test('stripping removes colour sequences', () => {
  assert.equal(stripAnsi('\u001B[38;2;1;2;3mhi\u001B[0m'), 'hi');
  assert.equal(stripAnsi('plain'), 'plain');
});

test('truncation respects display width and adds an ellipsis', () => {
  assert.equal(truncateWidth('abcdefgh', 5), 'abcd…');
  assert.equal(truncateWidth('abc', 10), 'abc');
  assert.ok(displayWidth(truncateWidth(cjk, 6)) <= 6);
  assert.ok(truncateWidth(cjk, 6).endsWith('…'));
  assert.equal(truncateWidth('anything', 0), '');
});

test('padding aligns mixed-width text into equal columns', () => {
  assert.equal(displayWidth(padWidth('abc', 10)), 10);
  assert.equal(displayWidth(padWidth(cjk, 12)), 12);
  assert.equal(padWidth('ab', 6, 'right'), '    ab');
  assert.equal(displayWidth(padWidth('ab', 6, 'center')), 6);
});

test('colour depth detection honours the environment and the stream', () => {
  assert.equal(detectColorDepth({ NO_COLOR: '1' }, { isTTY: true }), 0);
  assert.equal(detectColorDepth({ FORCE_COLOR: '0' }, { isTTY: true }), 0);
  assert.equal(detectColorDepth({ TERM: 'dumb' }, { isTTY: true }), 0);
  assert.equal(detectColorDepth({}, { isTTY: false }), 0);
  assert.equal(detectColorDepth({ FORCE_COLOR: '1' }, { isTTY: false }), 8);
  assert.equal(detectColorDepth({ COLORTERM: 'truecolor' }, { isTTY: true }), 24);
  assert.equal(detectColorDepth({ TERM: 'xterm-256color' }, { isTTY: true }), 8);
  assert.equal(detectColorDepth({ TERM: 'xterm' }, { isTTY: true }), 4);
});

test('at depth zero no escape sequence is ever emitted', () => {
  const palette = createPalette('#F97316', 'dark', 0);
  const textMethods = [
    palette.accentText, palette.inkText, palette.faint, palette.muted, palette.good,
    palette.warn, palette.bad, palette.label, palette.badge, palette.bold, palette.dim,
    palette.italic, palette.underline, palette.inverse, palette.color, palette.bg,
  ];
  for (const method of textMethods) assert.equal(method('a'), 'a', method.name);
  assert.equal(palette.pill('a', 'success'), 'a');
  assert.equal(palette.style('warning', 'a'), 'a');
  // The escape-code accessors contribute nothing when colour is off.
  assert.equal(palette.code('a'), '');
  assert.equal(palette.bgCode('a'), '');
  assert.equal(palette.reset, '');
  assert.ok(!panel({ width: 40 }, palette).join('').includes('\u001B'));
});

test('at truecolor every paint method emits a reset', () => {
  const palette = createPalette('#F97316', 'dark', 24);
  for (const sample of [palette.accentText('a'), palette.faint('a'), palette.bold('a'), palette.muted('a')]) {
    assert.match(sample, /^\u001B\[/);
    assert.match(sample, /\u001B\[0m$/);
  }
  assert.equal(palette.code('#F97316'), '\u001B[38;2;249;115;22m');
  assert.equal(palette.bgCode('#000000'), '\u001B[48;2;0;0;0m');
});

test('deeper palettes degrade without leaking an escape', () => {
  for (const depth of [4, 8, 24]) {
    const palette = createPalette('#0EA5E9', 'light', depth);
    assert.equal(stripAnsi(palette.accentText('x')), 'x');
    assert.equal(stripAnsi(palette.faint('x')), 'x');
  }
});

test('escape sequences are stripped and reduced without losing the text', () => {
  const bold = `\u001B[1m${'x'}\u001B[0m`;
  assert.equal(stripAnsi(bold), 'x');
  assert.equal(displayWidth(bold), 1);
  const truecolor = `\u001B[38;2;249;115;22m${'y'}\u001B[0m`;
  assert.equal(stripAnsi(truecolor), 'y');
  const palette = createPalette('#F97316', 'dark', 24);
  assert.equal(stripAnsi(palette.accentText('z')), 'z');
});

test('the accent is carried through the palette and its soft variants', () => {
  const dark = createPalette('#0EA5E9', 'dark', 24);
  assert.equal(dark.accent, '#0EA5E9');
  assert.equal(dark.canvas, '#101210');
  assert.equal(dark.ink, '#F0EEE8');
  const light = createPalette('#0EA5E9', 'light', 24);
  assert.equal(light.canvas, '#EFEDE7');
  assert.equal(light.ink, '#1B1A16');
});

test('tone names resolve to distinct colours', () => {
  const palette = createPalette('#F97316', 'dark', 24);
  const tones = ['accent', 'success', 'warning', 'danger', 'muted', 'plain'];
  const seen = new Set(tones.map((tone) => toneHex(palette, tone)));
  assert.equal(seen.size, tones.length);
});

test('accent presets cycle and nearest-match', () => {
  assert.equal(nearestAccent('#F97316'), 0);
  assert.equal(accentStep('#F97316', 1), ACCENT_PRESETS[1]);
  assert.equal(accentStep(ACCENT_PRESETS[0], -1), ACCENT_PRESETS.at(-1));
  assert.equal(ACCENT_PRESETS.every((value) => /^#[0-9A-F]{6}$/.test(value)), true);
});

test('wrapping breaks on whitespace and hard-breaks long tokens', () => {
  assert.deepEqual(wrapText('one two three', 7), ['one two', 'three']);
  assert.deepEqual(wrapText('supercalifragilistic', 6), ['superc', 'alifra', 'gilist', 'ic']);
  assert.deepEqual(wrapText('a\n\nb', 10), ['a', '', 'b']);
  assert.equal(wrapText('anything', 0).length, 1);
  // The escape byte is removed, so the sequence can no longer drive the
  // terminal; the remaining characters stay as inert text.
  const escape = String.fromCharCode(27);
  const cleaned = wrapText('a' + escape + '[31mb', 10);
  assert.equal(cleaned.length, 1);
  assert.equal(cleaned[0].includes(escape), false);
  // A carriage return is normalised, and a newline is kept as a line break.
  assert.deepEqual(wrapText('a\r\nb', 10), ['a', 'b']);
  assert.deepEqual(wrapText('a\nb', 10), ['a', 'b']);
});

test('a panel is rectangular at every width and colour depth', () => {
  for (const width of [34, 40, 60, 84, 108]) {
    for (const depth of [0, 4, 8, 24]) {
      const palette = createPalette('#F97316', 'dark', depth);
      const lines = panel({
        width,
        title: 'Title',
        subtitle: 'subtitle',
        badge: 'live',
        body: ['', 'body line', 'another'],
        footer: 'footer',
      }, palette);
      const widths = new Set(lines.map((line) => displayWidth(line)));
      assert.equal(widths.size, 1, `width=${width} depth=${depth} produced ${[...widths]}`);
      assert.equal([...widths][0], width, `width=${width} depth=${depth}`);
    }
  }
});

test('a key-value row is exactly the requested width', () => {
  for (const width of [40, 60, 84, 100]) {
    for (const depth of [0, 24]) {
      const palette = createPalette('#F97316', 'dark', depth);
      const line = keyValue('Provider', 'OpenAI', palette, width);
      assert.equal(displayWidth(line), width, `width=${width} depth=${depth}`);
    }
  }
});

test('a meter is a fixed width at both extremes', () => {
  for (const depth of [0, 24]) {
    const palette = createPalette('#F97316', 'dark', depth);
    for (const [value, max] of [[0, 10], [5, 10], [10, 10], [99, 10], [-4, 10], [1, 0]]) {
      assert.equal(displayWidth(meter(value, max, 20, palette)), 20, `${value}/${max}`);
    }
  }
});

test('a rendered message never exceeds the available width', () => {
  const palette = createPalette('#F97316', 'dark', 0);
  for (const width of [30, 50, 84]) {
    const lines = renderMessage({
      id: 'm1',
      role: 'assistant',
      text: 'A fairly long reply that has to wrap across several lines to fit the pane.',
      createdAt: Date.now(),
      source: 'provider',
      status: 'sent',
    }, { palette, width, language: 'en' }, 2);
    for (const line of lines) {
      assert.ok(displayWidth(line) <= width, `"${line}" is ${displayWidth(line)} > ${width}`);
    }
  }
});

test('clampWidth keeps output inside usable bounds', () => {
  assert.equal(clampWidth(10), 32);
  assert.equal(clampWidth(500), 108);
  assert.equal(clampWidth(84), 84);
  assert.equal(clampWidth(Number.NaN), 84);
  assert.equal(clampWidth(undefined), 84);
});

test('the palette follows the settings it is built from', () => {
  const settings = createSettings({ accent: '#4F46E5', theme: 'light' });
  const palette = createPalette(settings.accent, settings.theme, 24);
  assert.equal(palette.accent, settings.accent);
  assert.equal(palette.theme, 'light');
  assert.equal(palette.canvas, '#EFEDE7');
});
