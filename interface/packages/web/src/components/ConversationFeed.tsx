import { useCallback, useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { Button, classNames } from './Primitives';

/**
 * The reader's place in a transcript.
 *
 * Following the conversation is the default, and the view stops following the
 * moment the reader scrolls up. A feed that re-anchors on every arrival cannot
 * be scrolled: the view is pulled back down each time a token lands, so reading
 * anything but the last line is impossible.
 */
export function isNearBottom(element: HTMLElement, slack = 48): boolean {
  return element.scrollHeight - element.scrollTop - element.clientHeight <= slack;
}

export interface ConversationFeedBinding {
  /** The scrolling element itself. */
  feedRef: RefObject<HTMLDivElement>;
  /** The element inside it that grows as the transcript grows. */
  contentRef: RefObject<HTMLDivElement>;
  onScroll: () => void;
  /** Whether the newest line is already on screen. What the button is for. */
  atBottom: boolean;
  /** Follow the newest line again, from wherever the reader is. */
  toLatest: () => void;
}

/**
 * Keeps a transcript where a messaging app would keep it.
 *
 * Three things are true at once, and any one of them missing is enough to make
 * a conversation unreadable: the feed opens on the newest line, it stays there
 * while the newest line is moving — a stored message, a token of a reply still
 * arriving — and it lets go the moment the reader scrolls back. The first two
 * are the same effect written twice; the third is what keeps the first two from
 * becoming a leash.
 *
 * The button is the way back once following has stopped. It appears only while
 * there is something below the fold, so a reader who is at the newest line is
 * not left reading a permanent control for a problem they do not have, and the
 * one who has scrolled up is never left guessing whether more arrived.
 */
export function useConversationFeed(): ConversationFeedBinding {
  const feedRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const [atBottom, setAtBottom] = useState(true);

  const toLatest = useCallback(() => {
    const element = feedRef.current;
    if (!element) return;
    following.current = true;
    element.scrollTop = element.scrollHeight;
    setAtBottom(true);
  }, []);

  const onScroll = useCallback(() => {
    const element = feedRef.current;
    if (!element) return;
    const near = isNearBottom(element);
    following.current = near;
    setAtBottom(near);
  }, []);

  // Deliberately without a dependency list. This is a standing promise rather
  // than a reaction to a named value: a transcript grows in more than one way
  // — a message stored, a reply streaming in, a filter narrowing the view, the
  // pane itself being resized — and every one of them belongs at the bottom.
  useEffect(() => {
    const element = feedRef.current;
    if (!element || !following.current) return;
    element.scrollTop = element.scrollHeight;
  });

  // Content that grows without saying anything new: a late webfont swapping in,
  // a line rewrapping, a phone's address bar sliding away. The bottom moves and
  // nothing fires a scroll event, so a feed followed only by the effect above
  // drifts a little further behind the conversation with every such moment.
  useEffect(() => {
    const content = contentRef.current;
    if (!content || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      const element = feedRef.current;
      if (element && following.current) element.scrollTop = element.scrollHeight;
    });
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  return { feedRef, contentRef, onScroll, atBottom, toLatest };
}

interface ConversationFeedProps {
  /**
   * The feed this component is to draw. It is passed in rather than made here
   * because the surface owns more than the view of a conversation: sending a
   * message is also a reason to follow the newest one, and that decision belongs
   * to whoever owns the composer, not to the element that scrolls.
   */
  feed: ConversationFeedBinding;
  /** The jump button's wording, in the reader's language. */
  label: string;
  /** Classes for the scrolling element, where padding and spacing belong. */
  className?: string;
  /** Spacing and padding for the transcript itself. */
  contentClassName?: string;
  children: ReactNode;
}

/**
 * A scrolling transcript with the conversation's own scroll behaviour.
 *
 * The growing element is separate from the scrolling one because the button has
 * to sit still while the transcript moves under it, and because only the former
 * can be measured for growth. It is a flex column for one more reason: a child
 * that wants to fill an empty pane cannot ask for `100%` of a parent whose
 * height is whatever its content turned out to be, but it can ask to grow.
 */
export function ConversationFeed({ feed, label, className, contentClassName, children }: ConversationFeedProps) {
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div
        className={classNames('scrollbar-thin min-h-0 flex-1 overflow-y-auto', className)}
        onScroll={feed.onScroll}
        ref={feed.feedRef}
      >
        <div className={classNames('flex min-h-full flex-col', contentClassName)} ref={feed.contentRef}>
          {children}
        </div>
      </div>
      {feed.atBottom ? null : (
        <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center px-4">
          <Button
            className="pointer-events-auto shadow-emboss"
            icon="arrowDown"
            onClick={feed.toLatest}
            title={label}
            variant="quiet"
          >
            {label}
          </Button>
        </div>
      )}
    </div>
  );
}
