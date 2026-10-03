import { useCallback, type RefObject, type WheelEvent } from 'react';

/**
 * Lets a wheel turn a surface even where the pointer is not already over the
 * part that scrolls.
 *
 * A screen that pins a header and scrolls the body beneath it answers to the
 * wheel in only one of the two places the reader can put the pointer. Scroll over
 * the text and it moves; scroll over the header, or over the margin beside a
 * centred column, and nothing happens at all — so reading to the bottom of a
 * settings page means first finding the content with the pointer, which is a
 * small thing to ask for and an irritating one to be stuck with, and not one the
 * reader can see the answer to.
 *
 * Nothing here re-implements scrolling. The body stays the only scroller and the
 * header stays pinned; this hands a wheel that landed *outside* the body to the
 * body, and leaves every wheel that landed inside it to the browser. That is what
 * keeps a textarea scrolled to its end mid-edit, and a list nested in the page,
 * chained onward the way they already are — the pointer is only translated into a
 * scroll where the pointer is not already the answer.
 */

// `WheelEvent.DOM_DELTA_LINE` without the global: this module is about a
// property of the event, not about the browser's idea of a line.
const LINE = 1;
const PIXELS_PER_LINE = 16;

/**
 * Wheel deltas arrive in pixels on a mouse and a trackpad but in lines from
 * anything that reports the older `DOM_DELTA_LINE`, and a page scrolled by one
 * pixel per notch reads as a broken page rather than a slow one.
 */
function toPixels(deltaY: number, deltaMode: number): number {
  return deltaMode === LINE ? deltaY * PIXELS_PER_LINE : deltaY;
}

/**
 * Returns a wheel handler to put on the surface around a scroll container.
 *
 * The handler is a no-op over the container, so it is safe to hang on a parent of
 * the whole screen: wheel events bubble, and the ones that matter never reach
 * the branch that moves anything.
 */
export function useWheelScroll(container: RefObject<HTMLElement>) {
  return useCallback((event: WheelEvent<HTMLElement>) => {
    const element = container.current;
    // Nothing to scroll, or something nearer the reader already had an opinion
    // about this wheel and said so.
    if (!element || event.defaultPrevented) return;
    // Over the content the browser knows what to do, including chaining out of
    // whatever scrolls inside it. Only a wheel that landed elsewhere is ours.
    if (element.contains(event.target as Node | null)) return;

    const distance = toPixels(event.deltaY, event.deltaMode);
    if (distance === 0) return;
    // At the end of the content a wheel should do nothing rather than shove the
    // page somewhere it cannot come back from, which is what forwarding past the
    // edge would otherwise look like.
    const atTop = element.scrollTop <= 0;
    const atBottom = element.scrollTop + element.clientHeight >= element.scrollHeight - 1;
    if ((distance < 0 && atTop) || (distance > 0 && atBottom)) return;

    // No `preventDefault`: the pointer is over chrome that cannot scroll and
    // neither can anything above it (`body` is `overflow: hidden`), so the
    // browser's own scrolling has nothing left to move and there is no second
    // scroll to cancel. A React wheel listener is passive anyway.
    element.scrollBy({ top: distance });
  }, [container]);
}