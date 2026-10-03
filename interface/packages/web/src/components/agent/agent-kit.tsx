import type { ReactNode } from 'react';
import {
  actionTone,
  channelLabel,
  createTranslator,
  deadlineLabel,
  formatClock,
  formatDay,
  formatUsd,
  humanizeState,
  intentionTone,
  stateTone,
  type AgentConversation,
  type AgentIntention,
  type Language,
} from '@project-phone/core';
import { classNames } from '../Primitives';
import { Icon } from '../Icon';

/**
 * The web half of the agent's presentation.
 *
 * Only layout lives here. The colours, the wording, the number formats and the
 * list of who the agent is talking to come from the kernel, so the browser and
 * the terminal cannot disagree about what `DELIBERATING` looks like, what a
 * deadline reads as, or which of four correspondents a message came from.
 */

const TONE_TEXT: Record<string, string> = {
  accent: 'accent-text',
  success: 'text-[var(--ok)]',
  warning: 'text-[var(--warn)]',
  danger: 'text-[var(--danger)]',
  muted: 'text-[var(--ink-faint)]',
};

const TONE_DOT: Record<string, string> = {
  accent: 'bg-[var(--accent)]',
  success: 'bg-[var(--ok-dot)]',
  warning: 'bg-[var(--warn-dot)]',
  danger: 'bg-[var(--danger-dot)]',
  muted: 'bg-[var(--ink-faint)]',
};

export type AgentT = ReturnType<typeof createTranslator>;

export { TONE_DOT, TONE_TEXT, actionTone, channelLabel, deadlineLabel, formatClock, formatDay, formatUsd, humanizeState, intentionTone, stateTone };

export interface IntentionNodeProps {
  node: AgentIntention;
  depth: number;
  t: AgentT;
  now: number;
  language: Language;
  onClose: (id: string) => void;
}

/**
 * One intention, and the intentions under it.
 *
 * Recursion rather than a flat list because a goal with steps under it is not a
 * list: the steps are how it gets done, and a board that flattens them hides the
 * only thing that makes a plan readable. Depth is bounded by the tree the reader
 * built, which refuses a parent pointer that closes a loop.
 */
export function IntentionNode({ node, depth, t, now, language, onClose }: IntentionNodeProps) {
  const tone = intentionTone(node.status);
  const deadline = deadlineLabel(node.deadline, now, t);
  const closable = node.status !== 'done' && node.status !== 'completed'
    && node.status !== 'cancelled' && node.status !== 'abandoned';
  return (
    <>
      <li
        className="group rounded-lg border border-transparent px-2 py-1.5 transition hover:border-[var(--line)] hover:bg-[var(--canvas-well)]"
        style={{ marginLeft: depth * 14 }}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className={classNames('h-1.5 w-1.5 shrink-0 rounded-full', TONE_DOT[tone])} />
              <span className="truncate text-sm font-medium text-[var(--ink)]">{node.title || node.id}</span>
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[0.68rem] text-[var(--ink-faint)]">
              <span className={TONE_TEXT[tone]}>{humanizeState(node.status)}</span>
              {node.kind ? <span>{node.kind}</span> : null}
              {node.priority > 0 ? <span>p{node.priority.toFixed(2)}</span> : null}
              {node.commissioned_by ? <span>{t('intentionBy')} {node.commissioned_by}</span> : null}
              {node.origin ? <span>{t('intentionOrigin')} {node.origin}</span> : null}
              {deadline ? (
                <span className={deadline.overdue ? 'text-[var(--danger)]' : undefined}>{deadline.text}</span>
              ) : null}
              {node.budget_usd !== null ? (
                <span>
                  {t('intentionSpend', {
                    spent: formatUsd(node.spent_usd ?? 0, language),
                    budget: formatUsd(node.budget_usd, language),
                  })}
                </span>
              ) : null}
            </div>
            {node.desired_end_state ? (
              <p className="mt-1 text-[0.68rem] leading-4 text-[var(--ink-faint)]">
                <span className="opacity-70">{t('intentionDesired')}:</span> {node.desired_end_state}
              </p>
            ) : null}
          </div>
          {closable ? (
            <button
              className="shrink-0 self-center rounded-md border border-transparent px-2 py-1 text-[0.68rem] text-[var(--ink-faint)] opacity-0 transition hover:border-[var(--danger-line)] hover:text-[var(--danger)] group-hover:opacity-100 focus:opacity-100"
              onClick={() => onClose(node.id)}
              title={t('intentionClose')}
              type="button"
            >
              {t('intentionClose')}
            </button>
          ) : null}
        </div>
      </li>
      {node.children.length > 0 ? (
        <ul>
          {node.children.map((child) => (
            <IntentionNode
              depth={depth + 1}
              key={child.id}
              language={language}
              node={child}
              now={now}
              onClose={onClose}
              t={t}
            />
          ))}
        </ul>
      ) : null}
    </>
  );
}

export function EmptyNote({ children }: { children: ReactNode }) {
  return <p className="px-1 py-6 text-center text-xs leading-5 text-[var(--ink-faint)]">{children}</p>;
}

/**
 * Who the agent is talking to, and the one this surface is talking to.
 *
 * People, not doors. A door is how a conversation travels; a person is who it is
 * with, and an agent that can be written to from four channels has several
 * conversations going at once — which is why this is a list of names and why the
 * names are the thing that is drawn: a reader opening a transcript full of
 * arrivals is asking "who said this", and a list of vendors answers a different
 * question.
 *
 * The label is the sender as that door spells them, and the address goes with it
 * in the tooltip rather than being invented away: a correspondent who has
 * renamed themselves is named as they are, and the address is still there for
 * the reader who has two of the same name.
 *
 * There is no "all". A pane that shows every conversation at once cannot label
 * a bubble, because the label is what the pane selects on — and a transcript
 * drawn as one undifferentiated stream is exactly the thing that made four
 * correspondents read as the reader talking to themselves.
 *
 * The selected conversation decides where a reply goes, not just what is drawn.
 * That is the whole reason this sits next to the composer and not in a settings
 * page: a filter that only filtered would be a reading tool, and this one is a
 * statement about which conversation a question belongs to.
 *
 * `closed` is said out loud rather than hidden. That door is in the transcript —
 * a conversation happened there — and the agent has since stopped having it, so
 * the two remaining readings of an empty reply box are "nothing has been said"
 * and "you cannot speak there any more", and only one of them is true.
 */
export function PeopleFilter({
  conversations, closed, door, selected, onSelect, t,
}: {
  conversations: readonly AgentConversation[];
  /** Doors the selected conversation is on, so the reader can name it. */
  door: string | null;
  closed: readonly string[];
  selected: string | null;
  onSelect: (id: string) => void;
  t: AgentT;
}) {
  if (conversations.length < 2) return null;
  return (
    <div className="flex min-w-0 items-center gap-2">
      <span className="shrink-0 text-[0.62rem] uppercase tracking-[0.14em] text-[var(--ink-faint)]">
        {t('peopleFilter')}
      </span>
      <div aria-label={t('peopleFilter')} className="scrollbar-thin flex min-w-0 shrink items-center gap-1.5 overflow-x-auto" role="group">
        {conversations.map((conversation) => {
          const active = conversation.id === selected;
          return (
            <button
              aria-pressed={active}
              className={classNames(
                'inline-flex min-w-0 shrink-0 items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[0.68rem] font-medium transition',
                active
                  ? 'border-[var(--accent-line)] bg-[var(--accent-soft)] accent-text'
                  : 'border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-faint)] hover:text-[var(--ink-muted)]',
              )}
              key={conversation.id}
              onClick={() => onSelect(conversation.id)}
              title={`${conversation.label} · ${channelLabel(conversation.channel)}`}
              type="button"
            >
              <Icon name="globe" size={12} />
              <span className="max-w-[11rem] truncate">{conversation.label}</span>
            </button>
          );
        })}
      </div>
      {door ? (
        <p className="shrink-0 text-[0.65rem] text-[var(--ink-faint)]">
          {t('channelVia', { channel: channelLabel(door) })}
          {closed.includes(door) ? ` — ${t('channelClosed')}` : null}
        </p>
      ) : null}
    </div>
  );
}

/**
 * A spend bar against the cap the agent itself enforces.
 *
 * The bar is drawn only when a cap was reported. A bar filled to a hundred per
 * cent against a cap of zero is a claim, and it would be a false one.
 */
export function SpendBar({ spent, cap, tone = 'accent' }: { spent: number; cap: number; tone?: 'accent' | 'warning' | 'danger' }) {
  const ratio = cap > 0 ? Math.max(0, Math.min(1, spent / cap)) : 0;
  const fill = tone === 'danger' ? 'bg-[var(--danger)]' : tone === 'warning' ? 'bg-[var(--warn)]' : 'bg-[var(--accent)]';
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--canvas-well)]" role="presentation">
      <div className={classNames('h-full rounded-full', fill)} style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
    </div>
  );
}
