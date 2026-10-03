import type { TranslationKey } from '../i18n.js';
import type { Language } from '../types.js';
import type { AgentIntention } from './types.js';

/**
 * How an agent's state and numbers are read.
 *
 * These live in the kernel rather than in either interface because a terminal and
 * a browser showing the same agent must not invent two vocabularies for it. A
 * reader who moves between them should not have to learn that `DELIBERATING` is
 * drawn orange in one place and blue in the other, and a state name a program
 * stored with underscores should read the same way wherever it appears.
 */

export type Tone = 'accent' | 'success' | 'warning' | 'danger' | 'muted';

export type ToneKey = Tone;

/**
 * A life state, coloured by what it means to a reader.
 *
 * A state name is not a colour: `ACTING` and `SLEEPING` are both "busy" to a
 * program and are not the same thing to a person. The table is declared once here
 * so both surfaces read it the same way — a dashboard that coloured states as it
 * found them would make a change of state look like a change of mood.
 */
const STATE_TONES: Record<string, Tone> = {
  DREAMING: 'muted',
  IDLE: 'muted',
  PERCEIVING: 'muted',
  ATTENDING: 'accent',
  DELIBERATING: 'accent',
  REFLECTING: 'accent',
  DECIDING: 'accent',
  ACTING: 'success',
  ACTING_TOOL: 'success',
  RECORDING: 'success',
  CONSOLIDATING: 'muted',
  RESTING: 'muted',
  SLEEPING: 'muted',
  BLOCKED: 'warning',
  PAUSED: 'warning',
  STOPPED: 'danger',
};

export function stateTone(state: string): Tone {
  return STATE_TONES[state] ?? 'muted';
}

const INTENTION_TONES: Record<string, Tone> = {
  active: 'accent',
  running: 'accent',
  blocked: 'warning',
  paused: 'warning',
  pending: 'muted',
  done: 'success',
  completed: 'success',
  abandoned: 'muted',
  cancelled: 'muted',
  failed: 'danger',
};

export function intentionTone(status: string): Tone {
  return INTENTION_TONES[status] ?? 'muted';
}

const ACTION_TONES: Record<string, Tone> = {
  ok: 'success',
  done: 'success',
  succeeded: 'success',
  running: 'accent',
  pending: 'accent',
  denied: 'warning',
  refused: 'warning',
  undone: 'muted',
  error: 'danger',
  failed: 'danger',
};

export function actionTone(status: string): Tone {
  return ACTION_TONES[status] ?? 'muted';
}

/** A state read in a sentence, with the underscores a database name leaves in. */
export function humanizeState(state: string): string {
  return state.replace(/_/g, ' ').toLowerCase();
}

export function formatClock(timestamp: number | string | null, language: Language): string {
  const value = typeof timestamp === 'number' ? timestamp : timestamp ? Date.parse(timestamp) : Number.NaN;
  if (!Number.isFinite(value)) return '--:--';
  try {
    return new Intl.DateTimeFormat(language, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(value);
  } catch {
    return '--:--';
  }
}

export function formatDay(value: string, language: Language): string {
  try {
    return new Intl.DateTimeFormat(language, { month: 'short', day: 'numeric' }).format(Date.parse(`${value}T00:00:00`));
  } catch {
    return value;
  }
}

export function formatUsd(value: number, language: Language, decimals = 2): string {
  const safe = Number.isFinite(value) ? value : 0;
  try {
    return new Intl.NumberFormat(language, {
      style: 'currency',
      currency: 'USD',
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(safe);
  } catch {
    return `$${safe.toFixed(decimals)}`;
  }
}

/** A duration in the coarsest unit that still says something. */
export function formatSpan(milliseconds: number): string {
  const total = Math.max(0, Math.round(milliseconds / 1000));
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h${minutes % 60 ? ` ${minutes % 60}m` : ''}`;
  return `${Math.floor(hours / 24)}d${hours % 24 ? ` ${hours % 24}h` : ''}`;
}

export interface DeadlineLabel {
  text: string;
  overdue: boolean;
}

export function deadlineLabel(
  deadline: string | null,
  now: number,
  t: (key: TranslationKey, values?: Record<string, string | number>) => string,
): DeadlineLabel | null {
  if (!deadline) return null;
  const at = Date.parse(deadline);
  if (!Number.isFinite(at)) return null;
  const remaining = at - now;
  if (remaining < 0) return { text: t('intentionOverdue', { time: formatSpan(-remaining) }), overdue: true };
  return { text: t('intentionDue', { time: formatSpan(remaining) }), overdue: false };
}

/** A lifecycle reading in one phrase. The most restrictive flag set is the one named. */
export function controlSummary(
  control: { paused: boolean; pause_actions: boolean; stopped: boolean; emergency: boolean; preview: boolean },
  t: (key: TranslationKey, values?: Record<string, string | number>) => string,
): { text: string; tone: Tone } {
  if (control.emergency) return { text: t('controlStateEmergency'), tone: 'danger' };
  if (control.stopped) return { text: t('controlStateStopped'), tone: 'danger' };
  if (control.paused) return { text: t('controlStateHeld'), tone: 'warning' };
  if (control.pause_actions) return { text: t('controlStateActionsHeld'), tone: 'warning' };
  // Below `pause_actions` rather than beside it, and after it for one reason: both
  // gate every tool, so an agent that cannot act must never be reported as
  // running — but a pause is somebody having stopped this, and that outranks a
  // mode somebody chose to look at it in.
  if (control.preview) return { text: t('controlStatePreview'), tone: 'warning' };
  return { text: t('controlStateRunning'), tone: 'success' };
}

/**
 * Flattens the intention tree, parents before children, for a flat list.
 *
 * Total on purpose. A caller may hand this a node that never went through the
 * reader — a hand-built view in a test, a snapshot from an older shape — and a
 * missing `children` must read as "no children" rather than throw halfway through
 * drawing a panel.
 */
export function flattenIntentions(nodes: readonly AgentIntention[] | null | undefined): AgentIntention[] {
  const out: AgentIntention[] = [];
  const seen = new Set<AgentIntention>();
  const walk = (list: readonly AgentIntention[] | null | undefined): void => {
    if (!Array.isArray(list)) return;
    for (const node of list) {
      // The reader refuses a parent pointer that closes a loop, but a tree that
      // arrived some other way might contain one, and recursing into it forever
      // would hang a surface rather than fail it.
      if (!node || seen.has(node)) continue;
      seen.add(node);
      out.push(node);
      walk(node.children);
    }
  };
  walk(nodes);
  return out;
}
