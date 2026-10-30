import { useEffect, useMemo, useRef, useState } from 'react';
import type { Language } from '@project-phone/core';
import { SectionLabel, classNames } from '../Primitives';
import { EmptyNote, TONE_DOT, TONE_TEXT, type AgentT } from './agent-kit';

/**
 * The background screen, watched live.
 *
 * When the agent delegates desktop work to APEX, the run happens on a surface
 * the person is not watching — hidden mode exists so their display stays
 * theirs while the agent works. This pane is where that work is visible: the
 * relay beside the run streams either real pixels (where the background
 * surface has a raster target) or the live accessibility tree of the hidden
 * surface, which this pane draws as its structure.
 *
 * The view is read-only by construction and stays that way here. The stream
 * carries no input channel, the proxy forwards GETs only, and this component
 * renders with pointer events disabled — so the most a viewer can do with the
 * picture is look at it. That is the whole point of the panel: watching is not
 * touching.
 */

interface ScreenSession {
  status: string;
  detail: string;
  goal: string;
  taskId: string;
  stepsUsed: number;
  platform: string;
  screenMode: string;
  pixelsAvailable: boolean;
  viewKind: 'pixels' | 'semantic';
  readOnly: boolean;
  surface: string;
}

interface ScreenFrame {
  width: number;
  height: number;
  format: string;
  data: string;
  seq: number;
}

interface SurfaceNode {
  id: string;
  role: string;
  name: string;
  value: string;
  window: string;
  bounds: [number, number, number, number] | null;
  focused: boolean;
  enabled: boolean;
  depth: number;
}

interface ScreenSurface {
  originLeft: number;
  originTop: number;
  width: number;
  height: number;
  nodes: SurfaceNode[];
  truncated: boolean;
  seq: number;
}

interface ScreenActivity {
  id: string;
  at: number;
  kind: string;
  label: string;
  detail: string;
}

const MAX_ACTIVITY = 24;

const STATUS_TONE: Record<string, string> = {
  starting: 'accent',
  running: 'accent',
  paused: 'warning',
  stopping: 'warning',
  stopped: 'success',
  error: 'danger',
};

const ACTIVITY_TONE: Record<string, string> = {
  click: 'accent',
  type: 'accent',
  key: 'accent',
  launch: 'accent',
  move: 'muted',
  wait: 'muted',
  observe: 'muted',
  read: 'muted',
  warn: 'warning',
  error: 'danger',
  done: 'success',
};

export function BackgroundScreenPane({ language, t }: { language: Language; t: AgentT }) {
  const [connection, setConnection] = useState<'connecting' | 'live' | 'idle'>('connecting');
  const [session, setSession] = useState<ScreenSession | null>(null);
  const [frame, setFrame] = useState<ScreenFrame | null>(null);
  const [surface, setSurface] = useState<ScreenSurface | null>(null);
  const [activity, setActivity] = useState<ScreenActivity[]>([]);

  useEffect(() => {
    let source: EventSource | null = null;
    let retry: number | undefined;
    let disposed = false;

    const connect = () => {
      if (disposed) return;
      source = new EventSource('/api/apex-screen/events');
      source.onopen = () => setConnection('live');
      source.onmessage = (message) => {
        let event: Record<string, unknown>;
        try {
          event = JSON.parse(message.data) as Record<string, unknown>;
        } catch {
          return;
        }
        switch (event.type) {
          case 'hello':
            setSession(event.session as ScreenSession);
            setConnection('live');
            break;
          case 'status':
            setSession((current) => (current
              ? {
                ...current,
                status: String(event.status ?? current.status),
                detail: String(event.detail ?? ''),
              }
              : current));
            break;
          case 'activity':
            setActivity((current) => [event.activity as ScreenActivity, ...current].slice(0, MAX_ACTIVITY));
            break;
          case 'frame':
            setFrame(event.frame as ScreenFrame);
            break;
          case 'surface':
            setSurface(event.surface as ScreenSurface);
            break;
          case 'bye':
            // The run is over and the relay is closing: show the quiet state
            // and look for the next run on the pane's own cadence.
            source?.close();
            source = null;
            setConnection('idle');
            retry = window.setTimeout(connect, 3000);
            break;
          default:
            break;
        }
      };
      source.onerror = () => {
        // A 503 while no run is active, or a relay that went away mid-stream:
        // either way the honest reading is "nothing to watch right now". This
        // closes and retries at a walking pace rather than hot-reconnecting.
        source?.close();
        source = null;
        setConnection('idle');
        setSession(null);
        setFrame(null);
        setSurface(null);
        setActivity([]);
        retry = window.setTimeout(connect, 3000);
      };
    };
    connect();
    return () => {
      disposed = true;
      window.clearTimeout(retry);
      source?.close();
    };
  }, []);

  const statusTone = session ? STATUS_TONE[session.status] ?? 'muted' : 'muted';
  const hasView = frame !== null || surface !== null;

  return (
    <div className="space-y-3">
      {session ? (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className={classNames('inline-flex items-center gap-1.5 text-xs font-medium', TONE_TEXT[statusTone])}>
              <span className={classNames('h-1.5 w-1.5 rounded-full', TONE_DOT[statusTone], session.status === 'running' && 'signal-pulse')} />
              {t('screenLive')}
            </span>
            <span className="text-[0.62rem] uppercase tracking-[0.14em] text-[var(--ink-faint)]">
              {session.platform} · {session.viewKind === 'pixels' ? 'pixels' : 'semantic'}
            </span>
          </div>
          {session.goal ? (
            <p className="text-xs leading-4 text-[var(--ink-muted)]">
              <span className="text-[0.62rem] uppercase tracking-[0.14em] text-[var(--ink-faint)]">{t('screenGoal')}: </span>
              {session.goal}
            </p>
          ) : null}
          <p className="text-[0.65rem] text-[var(--ink-faint)]">
            {t('screenSteps')}: <span className="font-mono text-[var(--ink-muted)]">{session.stepsUsed}</span>
            {session.detail ? <span> · {session.detail}</span> : null}
          </p>
        </>
      ) : null}

      {connection === 'idle' && !session ? (
        <EmptyNote>{t('screenEmpty')}</EmptyNote>
      ) : (
        /* The view itself: no pointer events, no selection, no context menu
           route in — a window, not a remote control. */
        <div
          aria-hidden="true"
          className="pointer-events-none relative w-full select-none overflow-hidden rounded-xl border border-[var(--line)] bg-[var(--canvas)]"
          style={{ aspectRatio: '16 / 10' }}
        >
          {frame ? (
            <img
              alt=""
              className="absolute inset-0 h-full w-full object-contain"
              draggable={false}
              src={`data:image/${frame.format};base64,${frame.data}`}
            />
          ) : surface ? (
            <SurfaceCanvas surface={surface} />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center px-4 text-center text-[0.65rem] text-[var(--ink-faint)]">
              {session ? t('screenMirror') : ''}
            </div>
          )}
        </div>
      )}

      {session && hasView ? (
        <p className="flex items-start gap-1.5 text-[0.62rem] leading-4 text-[var(--ink-faint)]">
          <IconLock />
          <span>{frame ? t('screenReadonly') : t('screenMirror')}</span>
        </p>
      ) : null}

      {activity.length > 0 ? (
        <div>
          <SectionLabel>·</SectionLabel>
          <ul className="space-y-1">
            {activity.slice(0, 6).map((entry) => {
              const tone = ACTIVITY_TONE[entry.kind] ?? 'muted';
              return (
                <li className="text-xs" key={entry.id}>
                  <div className="flex items-baseline justify-between gap-2">
                    <span className={classNames('h-1.5 w-1.5 shrink-0 translate-y-[-1px] rounded-full', TONE_DOT[tone])} />
                    <span className="min-w-0 flex-1 truncate text-[var(--ink-muted)]">{entry.label}</span>
                    <span className="shrink-0 text-[0.62rem] text-[var(--ink-faint)]">
                      {new Date(entry.at).toLocaleTimeString(language === 'ja' ? 'ja-JP' : language === 'zh' ? 'zh-CN' : 'en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                    </span>
                  </div>
                  {entry.detail ? <p className="truncate pl-3.5 text-[0.62rem] text-[var(--ink-faint)]">{entry.detail}</p> : null}
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/**
 * The semantic view: the hidden surface's accessibility tree, drawn in its
 * real proportions. Windows become frames, controls become boxes, text is
 * drawn where the text is. Nothing here is interactive, because nothing here
 * is the screen — it is a drawing of what the agent can see.
 */
function SurfaceCanvas({ surface }: { surface: ScreenSurface }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  const sortedNodes = useMemo(
    () => [...surface.nodes].sort((a, b) => a.depth - b.depth),
    [surface],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const draw = () => drawSurface(canvas, surface, sortedNodes);
    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [surface, sortedNodes]);

  return <canvas aria-hidden="true" className="absolute inset-0 h-full w-full" ref={canvasRef} />;
}

function drawSurface(
  canvas: HTMLCanvasElement,
  surface: ScreenSurface,
  nodes: SurfaceNode[],
) {
  const context = canvas.getContext('2d');
  if (!context) return;
  const ratio = window.devicePixelRatio || 1;
  const cssWidth = canvas.clientWidth || 1;
  const cssHeight = canvas.clientHeight || 1;
  if (canvas.width !== Math.round(cssWidth * ratio) || canvas.height !== Math.round(cssHeight * ratio)) {
    canvas.width = Math.round(cssWidth * ratio);
    canvas.height = Math.round(cssHeight * ratio);
  }
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  context.clearRect(0, 0, cssWidth, cssHeight);

  const styles = getComputedStyle(canvas);
  const variable = (name: string, fallback: string) => styles.getPropertyValue(name).trim() || fallback;
  const palette = {
    background: variable('--canvas', '#101210'),
    line: variable('--line', '#2c302c'),
    lineStrong: variable('--line-strong', '#3c423c'),
    ink: variable('--ink', '#e6e4de'),
    inkFaint: variable('--ink-faint', '#8a8f8a'),
    accent: variable('--accent', '#9fd0a0'),
  };

  context.fillStyle = palette.background;
  context.fillRect(0, 0, cssWidth, cssHeight);

  const surfaceWidth = surface.width > 0 ? surface.width : 1;
  const surfaceHeight = surface.height > 0 ? surface.height : 1;
  const padding = 10;
  const scale = Math.min(
    (cssWidth - padding * 2) / surfaceWidth,
    (cssHeight - padding * 2) / surfaceHeight,
  );
  const offsetX = padding + (cssWidth - padding * 2 - surfaceWidth * scale) / 2;
  const offsetY = padding + (cssHeight - padding * 2 - surfaceHeight * scale) / 2;
  const boxOf = (node: SurfaceNode) => {
    if (!node.bounds) return null;
    const [left, top, right, bottom] = node.bounds;
    return {
      x: offsetX + (left - surface.originLeft) * scale,
      y: offsetY + (top - surface.originTop) * scale,
      width: (right - left) * scale,
      height: (bottom - top) * scale,
    };
  };

  context.textBaseline = 'top';
  for (const node of nodes) {
    const box = boxOf(node);
    const role = node.role.toLowerCase();
    const isWindow = role.includes('window') || role.includes('dialog') || role.includes('sheet');
    const isText = role.includes('statictext') || role === 'text';
    const isField = role.includes('textfield') || role.includes('textarea')
      || role.includes('combobox') || role.includes('searchfield');
    const isButton = role.includes('button') || role.includes('menuitem')
      || role.includes('tab') || role.includes('link');
    if (!box) continue;
    if (box.width < 2 || box.height < 2) continue;

    context.lineWidth = node.focused ? 1.6 : 1;
    if (isWindow) {
      context.fillStyle = withAlpha(palette.accent, 0.05);
      context.fillRect(box.x, box.y, box.width, box.height);
      context.strokeStyle = palette.lineStrong;
      context.strokeRect(box.x, box.y, box.width, box.height);
      if (node.window && box.height > 18) {
        context.fillStyle = withAlpha(palette.accent, 0.1);
        context.fillRect(box.x, box.y, box.width, 14);
        clipText(context, node.window, box.x + 4, box.y + 3, box.width - 8, palette.inkFaint);
      }
    } else if (isText) {
      clipText(context, node.value || node.name || '', box.x, box.y, Math.max(box.width, 40), palette.ink);
    } else if (isButton) {
      context.fillStyle = withAlpha(palette.accent, node.enabled ? 0.12 : 0.05);
      context.fillRect(box.x, box.y, box.width, box.height);
      context.strokeStyle = palette.accent;
      context.globalAlpha = node.enabled ? 0.7 : 0.35;
      context.strokeRect(box.x, box.y, box.width, box.height);
      context.globalAlpha = 1;
      if (box.height >= 12) {
        clipText(context, node.name || '', box.x + 3, box.y + (box.height - 10) / 2, box.width - 6, palette.ink);
      }
    } else if (isField) {
      context.fillStyle = withAlpha(palette.ink, 0.05);
      context.fillRect(box.x, box.y, box.width, box.height);
      context.strokeStyle = node.focused ? palette.accent : palette.lineStrong;
      context.strokeRect(box.x, box.y, box.width, box.height);
      clipText(context, node.value || node.name || '', box.x + 3, box.y + 2, box.width - 6, palette.ink);
    } else {
      context.strokeStyle = palette.line;
      context.strokeRect(box.x, box.y, box.width, box.height);
    }
    if (node.focused && !isField) {
      context.strokeStyle = palette.accent;
      context.lineWidth = 1.4;
      context.strokeRect(box.x - 1, box.y - 1, box.width + 2, box.height + 2);
    }
  }

  if (nodes.length === 0) {
    context.fillStyle = palette.inkFaint;
    context.font = '10px ui-sans-serif, system-ui, sans-serif';
    context.textAlign = 'center';
    context.fillText('—', cssWidth / 2, cssHeight / 2);
    context.textAlign = 'left';
  }
}

function clipText(
  context: CanvasRenderingContext2D,
  text: string,
  x: number,
  y: number,
  maxWidth: number,
  color: string,
) {
  if (!text || maxWidth < 12) return;
  context.font = '10px ui-sans-serif, system-ui, sans-serif';
  context.fillStyle = color;
  let shown = text;
  if (context.measureText(shown).width > maxWidth) {
    while (shown.length > 1 && context.measureText(`${shown}…`).width > maxWidth) {
      shown = shown.slice(0, -1);
    }
    shown = `${shown}…`;
  }
  context.fillText(shown, x, y);
}

function withAlpha(color: string, alpha: number): string {
  // Colors arrive as hex custom-property values; paint them at partial
  // opacity via a full-canvas overlay-free fill style.
  if (color.startsWith('#')) {
    const length = color.length === 4 ? 1 : 2;
    const channel = (index: number) => parseInt(
      color.slice(1 + index * length, 1 + (index + 1) * length).repeat(length === 1 ? 2 : 1),
      16,
    );
    return `rgba(${channel(0)}, ${channel(1)}, ${channel(2)}, ${alpha})`;
  }
  return color;
}

function IconLock() {
  return (
    <svg
      aria-hidden="true"
      className="mt-0.5 h-3 w-3 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.5"
      viewBox="0 0 24 24"
    >
      <rect height="10" rx="2" width="14" x="5" y="10" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </svg>
  );
}
