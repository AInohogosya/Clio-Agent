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
 */
export function MessageRow({
  message,
  sender,
  clock,
  system,
  truncateSender,
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
}): React.ReactElement {
  const isUser = message.role === 'user';
  // Opt-in, because only the direct pane ever draws one: the agent pane must
  // keep reading a system arrival exactly as it did, as an ordinary bubble.
  const isSystem = system === true;
  return (
    <div className={classNames('flex gap-3', isUser ? 'justify-end' : 'justify-start')}>
      {!isUser ? (
        <div className={classNames('mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--canvas-raised)]', isSystem ? 'text-[var(--ink-faint)]' : 'text-[var(--ink-muted)]')}>
          <Icon name={isSystem ? 'spark' : 'cpu'} size={15} />
        </div>
      ) : null}
      <div className={classNames('max-w-[min(86%,680px)]', isUser ? 'items-end' : 'items-start')}>
        <div className={classNames('mb-1 flex items-center gap-2 text-[0.65rem] text-[var(--ink-faint)]', isUser ? 'justify-end' : 'justify-start')}>
          <span className={classNames(truncateSender && 'max-w-[16rem] truncate')}>{sender}</span>
          <span>{clock}</span>
        </div>
        <div className={classNames(
          'whitespace-pre-wrap rounded-2xl px-4 py-3 text-sm leading-6',
          isUser
            ? 'rounded-tr-md border border-[var(--accent-line)] bg-[var(--accent-soft)] text-[var(--ink)]'
            : 'rounded-tl-md border border-[var(--line)] bg-[var(--canvas-well)] text-[var(--ink)]',
        )}>
          {message.text}
        </div>
      </div>
      {isUser ? (
        <div className="mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--canvas-raised)] text-[var(--ink-muted)]">
          <Icon name="home" size={15} />
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
