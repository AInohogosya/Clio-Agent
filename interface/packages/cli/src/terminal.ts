/**
 * Full-screen terminal ownership.
 *
 * The alternate screen and the cursor are process-wide state, so entering and
 * leaving them is bookkeeping rather than drawing. The failure this exists to
 * prevent is leaving a terminal in the alternate screen with a hidden cursor:
 * the shell looks frozen and the scrollback is gone, and the only cure is a new
 * terminal window.
 *
 * Two things make that safe. The restore is registered against process exit as
 * well as the normal path, so a crash or a signal still puts the terminal back.
 * And it runs at most once, so the signal handler and the `finally` cannot
 * double-leave.
 */

const ENTER = '\u001B[?1049h';
const LEAVE = '\u001B[?1049l';
const HIDE_CURSOR = '\u001B[?25l';
const SHOW_CURSOR = '\u001B[?25h';

export interface FullScreen {
  restore: () => void;
}

export function enterFullScreen(
  write: (text: string) => void = (text) => process.stdout.write(text),
): FullScreen {
  let restored = false;
  const restore = () => {
    if (restored) return;
    restored = true;
    process.off('exit', restore);
    process.off('SIGINT', restore);
    process.off('SIGTERM', restore);
    process.off('SIGHUP', restore);
    write(`${SHOW_CURSOR}${LEAVE}`);
  };
  process.on('exit', restore);
  for (const signal of ['SIGINT', 'SIGTERM', 'SIGHUP'] as const) {
    // Listening rather than handling: the point is only to restore, and
    // swallowing a signal that something else may care about would be worse.
    process.on(signal, () => {
      restore();
      process.exit(1);
    });
  }
  write(`${ENTER}${HIDE_CURSOR}`);
  return { restore };
}
