import {
  controlSummary,
  createTranslator,
  deadlineLabel,
  flattenIntentions,
  formatClock,
  formatUsd,
  humanizeState,
  stateTone,
  type AgentControlAction,
  type AgentView,
} from '@project-phone/core';
import { truncateWidth, type Palette, type Tone } from './palette.js';
import { keyValue, meter, note, panel, rule, sectionLabel, type RenderOptions } from './render.js';

/**
 * The one-shot agent report.
 *
 * The same rows the full-screen panels draw, laid out for a pipe rather than a
 * terminal: `phone agent` is what a person types when they are not going to sit
 * and watch, and a script that has to parse a live TUI is not a script.
 */

/** One mark per tone, so the terminal and the browser read the same colour. */
const MARK: Partial<Record<Tone, string>> = {
  accent: '◆',
  success: '●',
  warning: '▲',
  danger: '■',
  muted: '·',
};

function mark(tone: Tone): string {
  return MARK[tone] ?? MARK.muted ?? '·';
}

const CONTROL_LABELS: Record<AgentControlAction, Parameters<ReturnType<typeof createTranslator>>[0]> = {
  pause_actions: 'controlPauseActions',
  pause_all: 'controlPauseAll',
  resume: 'controlResume',
  stop: 'controlStop',
  emergency_stop: 'controlEmergencyStop',
  // Two words rather than one "Preview": the verb that was queued is what gets
  // reported back, and "Preview" alone would say which setting was touched
  // without saying which way it went.
  preview_on: 'controlPreviewOn',
  preview_off: 'controlPreviewOff',
};

export function controlLabel(action: AgentControlAction, t: ReturnType<typeof createTranslator>): string {
  return t(CONTROL_LABELS[action]);
}

export interface AgentScreenOptions {
  /** Which panel the report is centred on; `all` prints every one of them. */
  focus?: 'all' | 'presence' | 'intentions' | 'actions' | 'budget' | 'guardian';
}

export function agentScreen(view: AgentView, options: RenderOptions, extra: AgentScreenOptions = {}): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const focus = extra.focus ?? 'all';
  const lines: string[] = [''];

  lines.push(...panel({
    width,
    tone: 'accent',
    title: `◉ ${t('agentTitle')}`,
    subtitle: `${view.presence.focus ? view.presence.focus.title : t('presenceIdle')}`,
    badge: view.loaded ? humanizeState(view.presence.state) : t('presenceNever'),
    badgeTone: stateTone(view.presence.state),
    body: [],
  }, palette));
  lines.push('');

  if (focus === 'all' || focus === 'presence') lines.push(...presenceRows(view, options));
  if (focus === 'all' || focus === 'intentions') lines.push(...intentionRows(view, options));
  if (focus === 'all' || focus === 'actions') lines.push(...actionRows(view, options));
  if (focus === 'all' || focus === 'budget') lines.push(...budgetRows(view, options));
  if (focus === 'all' || focus === 'guardian') lines.push(...guardianRows(view, options));

  return lines;
}

function presenceRows(view: AgentView, options: RenderOptions): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const tone = stateTone(view.presence.state);
  const summary = controlSummary(view.control, t);
  const body: string[] = [];
  body.push(keyValue(t('presenceState'), `${mark(tone)} ${humanizeState(view.presence.state)}`, palette, width - 4, 16));
  body.push(keyValue(t('presenceFocus'), truncateWidth(view.presence.focus?.title ?? t('presenceIdle'), width - 22), palette, width - 4, 16));
  body.push(keyValue(t('presenceUpdated'), view.presence.ts ? formatClock(view.presence.ts, language) : t('presenceNever'), palette, width - 4, 16));
  body.push(`  ${palette.style(summary.tone, `${summary.tone === 'success' ? '✓' : '!'} ${summary.text}`)}`);
  if (view.presence.recentCycles.length) {
    // One mark per recent cycle, oldest first, clipped to the panel: a strip
    // longer than the box would wrap it and push the whole report down a
    // screen. The clip is on the *list* and not on the joined string, because
    // each mark already carries its colour escapes and a byte-wise slice of
    // styled text lands mid-sequence and paints the rest of the row wrong.
    const available = Math.max(4, width - 22);
    const marks = view.presence.recentCycles.slice(0, 24).reverse().slice(0, available)
      .map((cycle) => palette.style(stateTone(cycle.state), mark(stateTone(cycle.state))))
      .join('');
    body.push('');
    body.push(palette.faint(`  ${t('presenceCycles')}`));
    body.push(`  ${marks}`);
  }
  return [...panel({ width, title: t('presenceLabel'), body }, palette), ''];
}

function intentionRows(view: AgentView, options: RenderOptions): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const nodes = flattenIntentions(view.intentions);
  if (!nodes.length) {
    return [...panel({ width, title: t('panelIntentions'), badge: '0', body: [palette.faint(`  ${t('intentionsEmpty')}`)] }, palette), ''];
  }
  const body = nodes.slice(0, 20).map((node) => {
    const tone = node.status;
    const budget = node.budget_usd === null
      ? ''
      : palette.faint(`  ${formatUsd(node.spent_usd ?? 0, language)}/${formatUsd(node.budget_usd, language)}`);
    const deadline = deadlineLabel(node.deadline, Date.now(), t);
    const title = `  ${palette.muted('·')} ${truncateWidth(node.title || node.id, Math.max(10, width - 30))}  ${palette.faint(tone)}${budget}`;
    if (!deadline) return title;
    return `${title}\n      ${deadline.overdue ? palette.style('danger', deadline.text) : palette.faint(deadline.text)}`;
  });
  return [...panel({ width, title: t('panelIntentions'), badge: String(nodes.length), body }, palette), ''];
}

function actionRows(view: AgentView, options: RenderOptions): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  if (!view.actions.length) {
    return [...panel({ width, title: t('panelActions'), badge: '0', body: [palette.faint(`  ${t('actionsEmpty')}`)] }, palette), ''];
  }
  const body = view.actions.slice(0, 20).map((action) => {
    const when = action.ts_start ? formatClock(action.ts_start, language) : '--:--';
    const tool = truncateWidth(action.tool, Math.max(8, width - 40));
    const undo = action.undo_ref ? palette.accentText(`  [${t('actionUndo')}]`) : '';
    return `  ${palette.faint(when)} ${truncateWidth(tool, 22)} ${palette.muted(action.status)}${undo}`;
  });
  return [...panel({ width, title: t('panelActions'), badge: String(view.actions.length), body }, palette), ''];
}

function budgetRows(view: AgentView, options: RenderOptions): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const caps = view.budget.caps;
  const total = view.budget.todayByBucket.reduce((sum, row) => sum + row.total, 0);
  const body: string[] = [];
  if (caps.daily_usd > 0) {
    body.push(keyValue(t('budgetToday'), formatUsd(total, language), palette, width - 4, 16));
    body.push(`  ${meter(total, caps.daily_usd, Math.max(8, width - 6), palette, total > caps.daily_usd ? 'danger' : 'accent')}`);
    body.push(palette.faint(`  ${t('budgetOf', { spent: formatUsd(total, language), cap: formatUsd(caps.daily_usd, language) })}`));
  } else {
    body.push(keyValue(t('budgetToday'), formatUsd(total, language), palette, width - 4, 16));
    body.push(palette.faint(`  ${t('budgetNoCap')}`));
  }
  for (const row of view.budget.byModel.slice(0, 8)) {
    body.push(keyValue(truncateWidth(row.model, 20), formatUsd(row.total, language), palette, width - 4, 24));
  }
  return [...panel({ width, tone: 'accent', title: t('panelBudget'), body }, palette), ''];
}

function guardianRows(view: AgentView, options: RenderOptions): string[] {
  const { palette, width, language } = options;
  const t = createTranslator(language);
  const guardian = view.guardian;
  const integrity = guardian.integrity;
  const body: string[] = [];
  body.push(keyValue(
    t('guardianIntegrity'),
    integrity ? (integrity.ok ? t('guardianIntegrityOk') : t('guardianIntegrityFail')) : t('guardianIntegrityStale'),
    palette,
    width - 4,
    20,
  ));
  if (guardian.auditHead) {
    body.push(keyValue(t('guardianAudit'), `#${guardian.auditHead.seq ?? '-'} ${(guardian.auditHead.hash ?? '').slice(0, 12)}`, palette, width - 4, 20));
  }
  body.push(keyValue(t('budgetProtected'), String(guardian.protectedCount), palette, width - 4, 20));
  body.push('');
  body.push(`  ${sectionLabel(t('guardianSnapshots'), palette)}`);
  for (const snapshot of guardian.snapshots.slice(0, 5)) {
    body.push(keyValue(
      truncateWidth(snapshot.name, 24),
      snapshot.created_at ? formatClock(snapshot.created_at, language) : t('presenceNever'),
      palette,
      width - 4,
      26,
    ));
  }
  body.push('');
  body.push(`  ${sectionLabel(t('guardianTrash'), palette)}`);
  for (const entry of guardian.trash.slice(0, 6)) {
    body.push(keyValue(
      truncateWidth(entry.origin, 24),
      entry.retention_until ? formatClock(entry.retention_until, language) : t('presenceNever'),
      palette,
      width - 4,
      26,
    ));
  }
  return [...panel({ width, title: t('panelGuardian'), body }, palette), ''];
}

/** The line printed when a command was queued rather than performed. */
export function queuedNote(action: AgentControlAction, options: RenderOptions): string {
  const t = createTranslator(options.language);
  return `${note(t('cliAgentQueued', { action: controlLabel(action, t) }), options.palette, 'success')}`;
}

export function ruleLine(width: number, palette: Palette): string {
  return rule(Math.max(4, width), palette);
}
