import { useCallback, useEffect, useLayoutEffect, useRef, type ReactNode } from 'react';
import { MAX_PROMPT_LENGTH } from '@project-phone/core';
import { IconButton } from './Primitives';

/** A CSS length in pixels, or `null` when the browser will not name one. */
function length(value: string): number | null {
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

/**
 * A field that is as tall as what has been written into it.
 *
 * A textarea has no opinion about its own height: `rows` decides where it
 * starts, and nothing after that moves it. Everything past the last line on
 * screen is a line the person wrote, cannot read back, and cannot aim at while
 * they are still writing it — a composer three rows tall turns a long thought
 * into a scroll fight with a box too small to see what is in it.
 *
 * So the height is measured and written back. The measurement is taken with the
 * height let go, and from `scrollHeight` rather than from the box: a textarea's
 * automatic height is the height of `rows`, not the height of its text, so the
 * box itself reports the same number whether the message is one line or forty.
 * `scrollHeight` is the one reading of the content that exists. The floor and
 * the ceiling are then read back out of the stylesheet, in the same pixels, so
 * `.composer-field` remains the single place where "as tall as the content, up
 * to a point" is decided — and because the number written back is the height the
 * field will actually render at, the transition eases the real thing rather than
 * a value the stylesheet then clips.
 *
 * It is measured in a layout effect so the height lands before paint. Measured
 * in an ordinary effect, the field spends one frame at a height it has already
 * outgrown, which on the keystroke that wraps a line onto a new one is long
 * enough to watch happen.
 */
function useFieldHeight(value: string) {
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  /** The height the field was last given: the one the next change eases away from. */
  const applied = useRef<number | null>(null);

  const fit = useCallback(() => {
    const field = fieldRef.current;
    if (!field) return;
    // A field held at a height reports that height back, so the only way to read
    // how tall its content is to let the field go. Nothing is painted in between
    // — this runs before the browser draws — so the field is never seen at
    // whatever height the text alone would have chosen.
    field.style.height = 'auto';
    const wanted = field.scrollHeight;
    const style = window.getComputedStyle(field);
    // A stylesheet the browser will not give a number to is not a ceiling this
    // can honour, and guessing one would be worse than leaving the clipping to
    // the same stylesheet that asked for it.
    const floor = length(style.minHeight) ?? 0;
    const ceiling = length(style.maxHeight) ?? wanted;
    const height = Math.min(Math.max(wanted, floor), ceiling);
    // A field given less height than its text needs a way to reach the rest of
    // that text; a field given all of it must not carry a scrollbar for nothing.
    // Compared against the height being written rather than against the box: with
    // the height let go, the box is one row tall whatever the text says, so it
    // cannot answer this.
    const scrolls = wanted - height > 1;
    // A transition can only ease away from a length, and `auto` is not one. So
    // the height the field already has is put back and the box read once more,
    // which is what commits that length as the start of the change below.
    // Without it the field arrives at each height instead of easing into it,
    // and a field that arrives looks broken even when it is exactly right.
    if (applied.current !== null) {
      field.style.height = `${applied.current}px`;
      field.getBoundingClientRect();
    }
    field.style.height = `${height}px`;
    field.style.overflowY = scrolls ? 'auto' : 'hidden';
    applied.current = height;
  }, []);

  useLayoutEffect(() => {
    fit();
  }, [fit, value]);

  /**
   * Text rewraps when the field gets narrower, and the height that was right for
   * the old width is wrong for the new one — nothing fires a keystroke for that,
   * and nothing re-renders either. Width is the only thing about this element's
   * own size that is not an answer to a question asked here, so it is the only
   * one worth watching.
   */
  useEffect(() => {
    const field = fieldRef.current;
    if (!field || typeof ResizeObserver === 'undefined') return;
    let width = field.offsetWidth;
    const observer = new ResizeObserver(() => {
      if (field.offsetWidth === width) return;
      width = field.offsetWidth;
      fit();
    });
    observer.observe(field);
    return () => observer.disconnect();
  }, [fit]);

  return fieldRef;
}

interface ComposerProps {
  /** What has been typed so far. The surface owns the draft; this owns the box. */
  value: string;
  onChange: (value: string) => void;
  /** Sending. The surface re-anchors its transcript first, then sends. */
  onSubmit: () => void;
  onInterrupt: () => void;
  /** Whether a reply is in flight, which is what makes Escape mean interrupt. */
  pending: boolean;
  /**
   * Whether this line can be written to at all.
   *
   * A conversation on a door the agent no longer has open. Refused here rather
   * than at the send, because a field that accepts a question and then reports
   * nothing is the reading that makes a reader think the agent has gone quiet —
   * and the reason is said above the field, where they are already looking.
   */
  disabled?: boolean;
  /**
   * The field's wording when it is empty, and its accessible name.
   *
   * One string for both, because a field labelled one thing and hinting another
   * gives a screen reader a name nobody can see.
   */
  placeholder: string;
  /** The send button's wording, in the reader's language. */
  sendLabel: string;
  /** Anything the surface wants under the field, such as its shortcut hints. */
  children?: ReactNode;
}

/**
 * The message composer.
 *
 * Both surfaces that can be talked to send through this one field, because a
 * composer is where a person decides whether to finish a sentence or send it,
 * and two fields that disagree about that is one of them getting it wrong. The
 * keys it answers to are the ones the interface promises: Enter sends, Shift+Enter
 * is a new line, Escape interrupts a reply in flight — on every surface, so
 * interrupting does not depend on which one happens to be mounted.
 */
export function Composer({ value, onChange, onSubmit, onInterrupt, pending, disabled, placeholder, sendLabel, children }: ComposerProps) {
  const fieldRef = useFieldHeight(value);

  return (
    <form
      className="shrink-0 border-t border-[var(--line)] p-3 sm:p-4"
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
    >
      <div className="instrument-well flex items-end gap-2 rounded-xl p-2 focus-within:border-[var(--accent-line)]">
        <textarea
          aria-label={placeholder}
          className="composer-field scrollbar-thin flex-1 resize-none bg-transparent px-2 py-2 text-sm leading-6 text-[var(--ink)] outline-none placeholder:text-[var(--ink-faint)]"
          disabled={disabled}
          maxLength={MAX_PROMPT_LENGTH}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault();
              onSubmit();
            }
            // The key the footer names: interrupting a reply in flight must not
            // depend on which screen is mounted.
            if (event.key === 'Escape' && pending) {
              event.preventDefault();
              onInterrupt();
            }
          }}
          placeholder={placeholder}
          ref={fieldRef}
          rows={1}
          value={value}
        />
        <IconButton
          aria-label={sendLabel}
          className="shrink-0"
          disabled={disabled || !value.trim()}
          icon="send"
          label={sendLabel}
          type="submit"
          variant="primary"
        />
      </div>
      {children}
    </form>
  );
}