import { classNames } from './Primitives.js';
import { Icon } from './Icon.js';
import type { ChatMessage } from '@project-phone/core';

/**
 * One chat bubble, as both panes draw it.
 *
 * `ChatScreen` (direct chat) and `AgentScreen` (agent operator) showed the same
 * avatar blocks, sender/clock header and bubble, written out twice — one
 * implementation owns them now. The one deliberate difference is who the sender
 * is: the caller resolves it, because the agent pane reads
 * `message.author ?? conversation?.label` (never a hard-coded "You" for an
 * arrival on another door) while the direct pane reads `message.author ?? you`.
 * A system message gets the spark avatar on the direct pane only, because only
 * that pane ever draws one.
 *
 * The reader's density and clock preferences reach the row as props: compact
 * tightens the bubble and the header, and no-clock turns the header into a name.
 */
export function MessageRow({
  message,
  sender,
  clock,
  system,
  truncateSender,
  showTimestamps = true,
  density = 'comfortable',
}: {
  message: ChatMessage;
  sender: string;
  clock: string;
  system?: boolean;
  /**
   * Cap the sender line at a fixed width, for the pane where a sender can be
   * a conversation label rather than a short name. Off on the direct pane,
   * whose senders are short, so a long one still wraps instead of vanishing.
   */
  truncateSender?: boolean;
  showTimestamps?: boolean;
  density?: 'comfortable' | 'compact';
}): React.ReactElement {
  const isUser = message.role === 'user';
  // Opt-in, because only the direct pane ever draws one: the agent pane must
  // keep reading a system arrival exactly as it did, as an ordinary bubble.
  const isSystem = system === true;
  const compact = density === 'compact';
  return (
    <div className={classNames('flex', compact ? 'gap-2' : 'gap-3', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser ? (
        <div className={classNames(
          'mt-1 flex shrink-0 items-center justify-center rounded-xl border border-[var(--accent-line)] bg-[var(--accent-soft)] accent-text shadow-sm',
          compact ? 'h-7 w-7' : 'h-9 w-9',
          isSystem && 'border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-faint)]',
        )}>
          <Icon name={isSystem ? 'spark' : 'cpu'} size={compact ? 13 : 16} />
        </div>
      ) : null}
      <div className={classNames('flex max-w-[min(86%,680px)] flex-col', isUser ? 'items-end' : 'items-start')}>
        <div className={classNames(
          'flex items-center gap-2 text-[0.68rem] font-medium text-[var(--ink-faint)]',
          compact ? 'mb-0.5' : 'mb-1.5',
          isUser ? 'justify-end' : 'justify-start',
        )}>
          <span className={classNames(truncateSender && 'max-w-[16rem] truncate')}>{sender}</span>
          {showTimestamps ? <span className="rounded-full bg-[var(--canvas-well)] px-2 py-0.5">{clock}</span> : null}
        </div>
        <div className={classNames(
          'whitespace-pre-wrap rounded-3xl text-sm leading-6 shadow-sm',
          compact ? 'px-3.5 py-2' : 'px-5 py-3.5',
          isUser
            ? 'rounded-br-lg border border-[var(--accent-line)] bg-gradient-to-b from-[var(--accent-soft)] to-[var(--canvas-well)] text-[var(--ink)]'
            : 'rounded-bl-lg border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink)]',
        )}>
          {message.text}
        </div>
      </div>
      {isUser ? (
        <div className={classNames(
          'mt-1 flex shrink-0 items-center justify-center rounded-xl border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-muted)] shadow-sm',
          compact ? 'h-7 w-7' : 'h-9 w-9',
        )}>
          <Icon name="home" size={compact ? 13 : 16} />
        </div>
      ) : null}
    </div>
  );
}

/** The three-dot pulse while the other side is answering, with its label. */
export function PendingIndicator({ label }: { label: string }): React.ReactElement {
  return (
    <div className="flex items-center gap-3 text-xs text-[var(--ink-faint)]">
      <div className="flex gap-1 rounded-lg border border-[var(--line)] bg-[var(--canvas-well)] px-3 py-2.5">
        <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)]" />
        <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)] [animation-delay:180ms]" />
        <span className="signal-pulse h-1.5 w-1.5 rounded-full bg-[var(--accent)] [animation-delay:360ms]" />
      </div>
      <span>{label}</span>
    </div>
  );
}
