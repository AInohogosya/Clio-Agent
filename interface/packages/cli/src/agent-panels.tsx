import React from 'react';
import { Box, Text } from 'ink';
import {
  actionTone,
  controlSummary,
  createTranslator,
  deadlineLabel,
  flattenIntentions,
  formatClock,
  formatDay,
  formatUsd,
  humanizeState,
  intentionTone,
  stateTone,
  channelLabel,
  type AgentBaseModel,
  type AgentConversation,
  type AgentControl,
  type AgentDoor,
  type AgentIdentity,
  type AgentIntention,
  type AgentLinkStatus,
  type AgentView,
  type Language,
  type Settings,
} from '@project-phone/core';
import { doorStateNote } from './channels.js';
import { Divider, label, Meta, Panel, toneMark } from './tui-kit.js';
import { toneHex, truncateWidth, type Palette, type Tone } from './palette.js';
import { meter, wrapText } from './render.js';

export type T = ReturnType<typeof createTranslator>;

/**
 * The terminal's twin of the web panels.
 *
 * Every row is one fact the agent reported, and the wording and colours come from
 * the kernel, so a reader who moves between this and the browser is not learning
 * a second vocabulary for the same agent.
 *
 * The panels are the whole data set, not a summary of it: the browser's panes
 * scroll, so a terminal that stopped at the first twenty intentions was showing
 * less than the surface it is supposed to match. Each panel builds one flat list
 * of rows and windows it with `scroll`/`height`, the way the transcript does —
 * and where a row names something the reader can act on, it names the id that
 * acts on it, because `/undo` and `/cancel` are typed here rather than clicked.
 */

/** How many rows of a panel body fit on screen, bounded below so a panel never vanishes. */
export function panelHeightFor(height: number | undefined): number {
  return Math.max(4, (height ?? 24) - 4);
}

/** The short form of an id: long enough to be unique, short enough to type. */
export function shortId(id: string): string {
  return id.length <= 8 ? id : id.slice(0, 8);
}

/** One window over a panel's rows, with the honest footer when some are hidden. */
function SlicedRows({
  rows, scroll, height, palette, t,
}: {
  rows: React.ReactElement[];
  scroll: number;
  height: number | undefined;
  palette: Palette;
  t: T;
}): React.ReactElement {
  if (height === undefined || rows.length <= height) {
    return <Box flexDirection="column">{rows}</Box>;
  }
  const furthest = Math.max(0, rows.length - height);
  const at = Math.max(0, Math.min(scroll, furthest));
  const shown = rows.slice(at, at + height);
  return (
    <Box flexDirection="column">
      {shown}
      <Box marginTop={1}>
        <Text color={palette.inkFaint}>
          {`▲▼ ${t('panelShowing', { from: at + 1, to: at + shown.length, total: rows.length })}`}
        </Text>
      </Box>
    </Box>
  );
}

/** The link's own health, which is not the agent's state. */
export function linkLabel(link: AgentLinkStatus, streaming: boolean, t: T): string {
  if (link === 'offline') return t('agentLinkOffline');
  return streaming ? t('agentLinkLive') : t('agentLinkPolling');
}

export function linkTone(link: AgentLinkStatus): Tone {
  if (link === 'offline') return 'danger';
  return link === 'online' ? 'success' : 'warning';
}

export function PresencePanel({
  view, link, streaming, language, palette, t, width,
}: {
  view: AgentView;
  link: AgentLinkStatus;
  streaming: boolean;
  language: Language;
  palette: Palette;
  t: T;
  width: number;
}) {
  const tone = stateTone(view.presence.state);
  const summary = controlSummary(view.control, t);
  return (
    <Panel
      palette={palette}
      badge={humanizeState(view.presence.state)}
      badgeTone={tone}
      borderColor={palette.accent}
      title={t('presenceLabel')}
      width={width}
    >
      <Box flexDirection="column">
        <Meta label={t('presenceState')} palette={palette}>
          <Text color={toneHex(palette, tone)}>{`${toneMark(tone)} ${humanizeState(view.presence.state)}`}</Text>
        </Meta>
        <Meta label={t('presenceFocus')} palette={palette}>
          {view.presence.focus?.title ?? (view.loaded ? t('presenceIdle') : t('presenceNeverRun'))}
        </Meta>
        <Meta label={t('agentLink')} palette={palette}>
          <Text color={toneHex(palette, linkTone(link))}>{linkLabel(link, streaming, t)}</Text>
        </Meta>
        {view.presence.ts ? (
          <Meta label={t('presenceUpdated')} palette={palette}>{formatClock(view.presence.ts, language)}</Meta>
        ) : null}
        <Box marginTop={1}>
          <Text color={toneHex(palette, summary.tone)}>{`${summary.tone === 'success' ? '✓' : '!'} ${summary.text}`}</Text>
        </Box>
      </Box>
    </Panel>
  );
}

/**
 * The cycle strip: one mark per recent cycle, oldest at the left.
 *
 * It answers a question the state cannot: whether the agent is cycling steadily,
 * stalling between thoughts, or has not run at all — which is a different fact
 * from the state it happens to be in right now.
 */
export function CycleStrip({ view, palette, t, width }: { view: AgentView; palette: Palette; t: T; width: number }) {
  const cycles = view.presence.recentCycles.slice(0, 24).reverse();
  if (!cycles.length) return null;
  const cells = Math.max(6, width - 8);
  const step = Math.max(1, Math.ceil(cycles.length / cells));
  const sampled = cycles.filter((_, index) => index % step === 0).slice(0, cells);
  return (
    <Panel palette={palette} title={t('presenceCycles')} width={width}>
      <Text>
        {sampled.map((cycle, index) => {
          const tone = stateTone(cycle.state);
          return (
            <Text color={toneHex(palette, tone)} key={`${cycle.ts ?? 'x'}-${index}`}>
              {toneMark(tone)}
            </Text>
          );
        })}
      </Text>
    </Panel>
  );
}

/** A one-line cycle strip for the header, where a panel would take a title with it. */
export function CycleStripLine({ view, palette }: { view: AgentView; palette: Palette }): React.ReactElement | null {
  const cycles = view.presence.recentCycles.slice(0, 24).reverse();
  if (!cycles.length) return null;
  return (
    <Text>
      <Text color={palette.inkFaint}>{'◌ '}</Text>
      {cycles.map((cycle, index) => (
        <Text color={toneHex(palette, stateTone(cycle.state))} key={`${cycle.ts ?? 'x'}-${index}`}>
          {toneMark(stateTone(cycle.state))}
        </Text>
      ))}
    </Text>
  );
}

export function ThoughtsPanel({
  view, language, palette, t, width, scroll, height,
}: {
  view: AgentView;
  language: Language;
  palette: Palette;
  t: T;
  width: number;
  scroll?: number;
  height?: number;
}) {
  const body = Math.max(8, width - 12);
  const rows = view.thoughts.map((thought) => {
    const wrapped = wrapText(thought.summary, body);
    return (
      <Box flexDirection="column" key={thought.id} marginBottom={0}>
        {wrapped.map((line, index) => (
          <Text color={index === 0 ? palette.inkMuted : palette.inkFaint} key={index} wrap="truncate">
            {index === 0
              ? `${thought.ts ? formatClock(thought.ts, language) : '--:--'}  ${line}`
              : `        ${line}`}
          </Text>
        ))}
      </Box>
    );
  });
  return (
    <Panel palette={palette} badge={String(view.thoughts.length)} title={t('panelThoughts')} width={width}>
      <SlicedRows height={height} palette={palette} rows={rows} scroll={scroll ?? 0} t={t} />
      {view.thoughts.length === 0 ? <Text color={palette.inkFaint}>{t('thoughtsEmpty')}</Text> : null}
    </Panel>
  );
}

const CLOSED_STATUSES = new Set(['done', 'completed', 'cancelled', 'abandoned']);

function intentionMeta(node: AgentIntention, t: T): string {
  const parts: string[] = [];
  if (node.kind) parts.push(node.kind);
  if (node.priority > 0) parts.push(`p${node.priority.toFixed(2)}`);
  if (node.commissioned_by) parts.push(`${t('intentionBy')} ${node.commissioned_by}`);
  return parts.join(' · ');
}

function intentionRows({
  nodes, palette, t, language, width, now, depth,
}: {
  nodes: readonly AgentIntention[];
  palette: Palette;
  t: T;
  language: Language;
  width: number;
  now: number;
  depth: number;
}): React.ReactElement[] {
  const rows: React.ReactElement[] = [];
  const indent = '  '.repeat(depth);
  for (const node of nodes) {
    const tone = intentionTone(node.status);
    const title = node.title || node.id;
    const budget = node.budget_usd === null
      ? ''
      : `  ${formatUsd(node.spent_usd ?? 0, language, 2)}/${formatUsd(node.budget_usd, language, 2)}`;
    rows.push(
      <Box key={node.id}>
        <Text>{' '.repeat(depth * 2)}</Text>
        <Text color={toneHex(palette, tone)}>{'● '}</Text>
        <Text color={palette.ink} wrap="truncate">{truncateWidth(title, Math.max(8, width - 8 - depth * 2))}</Text>
        {budget ? <Text color={palette.inkFaint}>{budget}</Text> : null}
      </Box>,
    );
    // The id leads the meta line: it is what `/cancel` takes, and a line that
    // truncates from the right must cut the least actionable fact first.
    const meta = [`·${shortId(node.id)}`, intentionMeta(node, t)].filter(Boolean).join(' · ');
    if (meta) {
      rows.push(
        <Text color={palette.inkFaint} key={`${node.id}-m`} wrap="truncate">
          {`${indent}  ${truncateWidth(meta, Math.max(8, width - 6 - depth * 2))}`}
        </Text>,
      );
    }
    const deadline = deadlineLabel(node.deadline, now, t);
    if (deadline) {
      rows.push(
        <Text
          color={deadline.overdue ? toneHex(palette, 'danger') : palette.inkFaint}
          key={`${node.id}-d`}
          wrap="truncate"
        >
          {`${indent}  ${deadline.text}`}
        </Text>,
      );
    }
    if (node.desired_end_state) {
      rows.push(
        <Text color={palette.inkMuted} key={`${node.id}-e`} wrap="truncate">
          {`${indent}  ⇒ ${t('intentionDesired')}: ${truncateWidth(node.desired_end_state, Math.max(8, width - 10 - depth * 2))}`}
        </Text>,
      );
    }
    rows.push(...intentionRows({
      depth: depth + 1,
      language,
      nodes: node.children,
      now,
      palette,
      t,
      width,
    }));
  }
  return rows;
}

export function IntentionsPanel({
  view, language, palette, t, width, now, scroll, height,
}: {
  view: AgentView;
  language: Language;
  palette: Palette;
  t: T;
  width: number;
  now: number;
  scroll?: number;
  height?: number;
}) {
  const rows = intentionRows({ depth: 0, language, nodes: view.intentions, now, palette, t, width });
  const closable = flattenIntentions(view.intentions).filter((node) => !CLOSED_STATUSES.has(node.status)).length;
  return (
    <Panel palette={palette} badge={String(flattenIntentions(view.intentions).length)} title={t('panelIntentions')} width={width}>
      {rows.length ? (
        <Box flexDirection="column">
          <SlicedRows height={height} palette={palette} rows={rows} scroll={scroll ?? 0} t={t} />
          {closable > 0 ? (
            <Box marginTop={1}>
              <Text color={palette.inkFaint}>{t('cliCancelHint')}</Text>
            </Box>
          ) : null}
        </Box>
      ) : (
        <Text color={palette.inkFaint}>{t('intentionsEmpty')}</Text>
      )}
    </Panel>
  );
}

function actionRows({
  actions, palette, t, language, width,
}: {
  actions: AgentView['actions'];
  palette: Palette;
  t: T;
  language: Language;
  width: number;
}): React.ReactElement[] {
  const rows: React.ReactElement[] = [];
  const body = Math.max(8, width - 8);
  for (const action of actions) {
    const tone = actionTone(action.status);
    rows.push(
      <Box key={action.id}>
        <Text color={palette.inkFaint}>{`${action.ts_start ? formatClock(action.ts_start, language) : '--:--'}  `}</Text>
        <Text color={toneHex(palette, tone)}>{'● '}</Text>
        <Text color={palette.ink} wrap="truncate">{truncateWidth(action.tool, Math.max(8, width - 26))}</Text>
        <Text color={toneHex(palette, tone)}>{`  ${humanizeState(action.status)}`}</Text>
        {action.undo_ref ? <Text color={palette.accent}>{'  ↺'}</Text> : null}
      </Box>,
    );
    const facts = [
      `·${shortId(action.id)}`,
      action.thread_id ? action.thread_id.slice(0, 12) : '',
    ].filter(Boolean).join(' · ');
    rows.push(
      <Text color={palette.inkFaint} key={`${action.id}-i`} wrap="truncate">{`  ${facts}`}</Text>,
    );
    if (action.reason) {
      const why = wrapText(action.reason, body).slice(0, 2);
      why.forEach((line, index) => {
        rows.push(
          <Text color={palette.inkMuted} key={`${action.id}-r${index}`} wrap="truncate">
            {index === 0 ? `  ${t('actionWhy')}: ${line}` : `    ${line}`}
          </Text>,
        );
      });
    }
    if (action.args_redacted && Object.keys(action.args_redacted).length > 0) {
      const json = wrapText(JSON.stringify(action.args_redacted), body).slice(0, 3);
      const hidden = wrapText(JSON.stringify(action.args_redacted), body).length - json.length;
      json.forEach((line, index) => {
        rows.push(
          <Text color={palette.inkFaint} key={`${action.id}-a${index}`} wrap="truncate">
            {index === 0 ? `  ${t('actionArgs')}: ${line}` : `    ${line}`}
          </Text>,
        );
      });
      if (hidden > 0) {
        rows.push(
          <Text color={palette.inkFaint} key={`${action.id}-am`}>{`    ⋯ ${t('cliMoreLines', { count: hidden })}`}</Text>,
        );
      }
    }
  }
  return rows;
}

export function ActionsPanel({
  view, language, palette, t, width, scroll, height,
}: {
  view: AgentView;
  language: Language;
  palette: Palette;
  t: T;
  width: number;
  scroll?: number;
  height?: number;
}) {
  const rows = actionRows({ actions: view.actions, language, palette, t, width });
  const undoable = view.actions.filter((action) => action.undo_ref).length;
  return (
    <Panel palette={palette} badge={String(view.actions.length)} title={t('panelActions')} width={width}>
      <Box flexDirection="column">
        {rows.length ? (
          <SlicedRows height={height} palette={palette} rows={rows} scroll={scroll ?? 0} t={t} />
        ) : (
          <Text color={palette.inkFaint}>{t('actionsEmpty')}</Text>
        )}
        {undoable > 0 ? (
          <Box marginTop={1}>
            <Text color={palette.inkFaint}>{t('cliUndoHint')}</Text>
          </Box>
        ) : null}
      </Box>
    </Panel>
  );
}

/** Seven days of spend, one bar a day — the terminal's twin of the web's DailyBars. */
function DailyBars({
  daily, language, palette, t, width,
}: {
  daily: AgentView['budget']['daily'];
  language: Language;
  palette: Palette;
  t: T;
  width: number;
}) {
  const peak = Math.max(...daily.map((row) => row.total), 0.0001);
  return (
    <Box flexDirection="column">
      {daily.map((row) => (
        <Box key={row.day}>
          <Text color={palette.inkFaint}>{`${formatDay(row.day, language)} `}</Text>
          <Text>{meter(row.total, peak, Math.max(6, Math.min(20, width - 20)), palette, 'accent')}</Text>
          <Text color={palette.inkMuted}>{` ${formatUsd(row.total, language, 2)}`}</Text>
        </Box>
      ))}
      <Text color={palette.inkFaint}>{label(t('budgetByDay'))}</Text>
    </Box>
  );
}

export function BudgetPanel({
  view, language, palette, t, width, scroll, height,
}: {
  view: AgentView;
  language: Language;
  palette: Palette;
  t: T;
  width: number;
  scroll?: number;
  height?: number;
}) {
  const caps = view.budget.caps;
  const rows = view.budget.todayByBucket;
  const total = rows.reduce((sum, row) => sum + row.total, 0);
  const cells = Math.max(8, width - 14);
  const body: React.ReactElement[] = [];
  body.push(
    <Meta key="today" label={t('budgetToday')} palette={palette}>{formatUsd(total, language)}</Meta>,
  );
  if (caps.daily_usd > 0) {
    body.push(
      <Text key="cap-meter">{meter(total, caps.daily_usd, cells, palette, total > caps.daily_usd ? 'danger' : 'accent')}</Text>,
      <Text key="cap-note" color={palette.inkFaint}>
        {t('budgetOf', { spent: formatUsd(total, language), cap: formatUsd(caps.daily_usd, language) })}
      </Text>,
    );
  } else {
    body.push(<Text key="cap-note" color={palette.inkFaint}>{t('budgetNoCap')}</Text>);
  }
  if (rows.length) {
    body.push(<Divider key="d1" palette={palette} width={width - 4} />);
    for (const row of rows) {
      const cap = row.bucket === 'commitment' ? caps.commitment_usd : caps.discretionary_usd;
      body.push(
        <Box flexDirection="column" key={row.bucket} marginTop={0}>
          <Meta label={row.bucket === 'commitment' ? t('budgetCommitment') : t('budgetDiscretionary')} palette={palette}>
            {formatUsd(row.total, language)}
          </Meta>
          {cap > 0 ? (
            <Text>{meter(row.total, cap, cells, palette, row.bucket === 'commitment' ? 'accent' : 'warning')}</Text>
          ) : null}
        </Box>,
      );
    }
  }
  if (view.budget.byModel.length) {
    body.push(<Divider key="d2" palette={palette} width={width - 4} />);
    body.push(<Text key="bm-h" bold color={palette.inkFaint}>{label(t('budgetByModel'))}</Text>);
    for (const row of view.budget.byModel) {
      body.push(
        <Meta key={row.model} label={truncateWidth(row.model, Math.max(8, width - 16))} palette={palette}>
          {formatUsd(row.total, language)}
        </Meta>,
      );
    }
  }
  if (view.budget.daily.length > 1) {
    body.push(<Divider key="d3" palette={palette} width={width - 4} />);
    body.push(
      <DailyBars daily={view.budget.daily} key="daily" language={language} palette={palette} t={t} width={width} />,
    );
  }
  return (
    <Panel palette={palette} borderColor={palette.accent} title={t('panelBudget')} width={width}>
      <SlicedRows height={height} palette={palette} rows={body} scroll={scroll ?? 0} t={t} />
    </Panel>
  );
}

export function GuardianPanel({
  view, language, palette, t, width, now, scroll, height,
}: {
  view: AgentView;
  language: Language;
  palette: Palette;
  t: T;
  width: number;
  now: number;
  scroll?: number;
  height?: number;
}) {
  const guardian = view.guardian;
  const integrity = guardian.integrity;
  const body: React.ReactElement[] = [];
  body.push(
    <Meta key="integrity" label={t('guardianIntegrity')} palette={palette}>
      {!integrity ? t('guardianIntegrityStale') : (
        <Text color={toneHex(palette, integrity.ok ? 'success' : 'danger')}>
          {integrity.ok ? t('guardianIntegrityOk') : t('guardianIntegrityFail')}
        </Text>
      )}
    </Meta>,
  );
  if (integrity?.ran_at) {
    body.push(
      <Meta key="integrity-at" label={t('presenceUpdated')} palette={palette}>{formatClock(integrity.ran_at, language)}</Meta>,
    );
  }
  if (integrity && integrity.artifact_mismatches.length > 0) {
    body.push(
      <Text key="mismatches" color={toneHex(palette, 'danger')}>
        {t('guardianMismatches', { count: integrity.artifact_mismatches.length })}
      </Text>,
    );
  }
  if (guardian.auditHead) {
    body.push(
      <Meta key="audit" label={t('guardianAudit')} palette={palette}>
        {`#${guardian.auditHead.seq ?? '—'} ${(guardian.auditHead.hash ?? '').slice(0, 12)}`}
      </Meta>,
    );
  }
  body.push(
    <Meta key="protected" label={t('budgetProtected')} palette={palette}>{String(guardian.protectedCount)}</Meta>,
  );

  body.push(
    <Text key="snap-h" bold color={palette.inkFaint}>{label(t('guardianSnapshots'))}</Text>,
  );
  if (guardian.snapshots.length) {
    for (const snapshot of guardian.snapshots) {
      body.push(
        <Meta
          key={snapshot.name}
          label={truncateWidth(snapshot.name, Math.max(8, width - 18))}
          palette={palette}
        >
          {snapshot.created_at ? formatClock(snapshot.created_at, language) : t('presenceNever')}
        </Meta>,
      );
      if (snapshot.reason) {
        body.push(
          <Text color={palette.inkFaint} key={`${snapshot.name}-r`} wrap="truncate">
            {`  ${truncateWidth(snapshot.reason, Math.max(8, width - 6))}`}
          </Text>,
        );
      }
    }
  } else {
    body.push(<Text key="snap-e" color={palette.inkFaint}>{t('guardianSnapshotsEmpty')}</Text>);
  }

  body.push(
    <Text key="trash-h" bold color={palette.inkFaint}>{label(t('guardianTrash'))}</Text>,
  );
  if (guardian.trash.length) {
    for (const entry of guardian.trash) {
      const retained = entry.retention_until ? Date.parse(entry.retention_until) : Number.NaN;
      const expired = Number.isFinite(retained) && retained <= now;
      const shown = entry.retention_until
        ? formatClock(entry.retention_until, language)
        : t('presenceNever');
      body.push(
        <Meta key={`${entry.id}-o`} label={truncateWidth(entry.origin, Math.max(8, width - 20))} palette={palette}>
          <Text color={toneHex(palette, expired ? 'muted' : 'warning')}>{shown}</Text>
        </Meta>,
      );
      if (entry.reason) {
        body.push(
          <Text color={palette.inkFaint} key={`${entry.id}-r`} wrap="truncate">
            {`  ${truncateWidth(entry.reason, Math.max(8, width - 6))}`}
          </Text>,
        );
      }
    }
  } else {
    body.push(<Text key="trash-e" color={palette.inkFaint}>{t('guardianTrashEmpty')}</Text>);
  }

  return (
    <Panel palette={palette} title={t('panelGuardian')} width={width}>
      <SlicedRows height={height} palette={palette} rows={body} scroll={scroll ?? 0} t={t} />
    </Panel>
  );
}

/**
 * What this surface is pointed at, and how to change it.
 *
 * A terminal has no form, so this is a report rather than a form — and each row
 * names the command that changes it. Reading a value off a screen and then being
 * told the one word that alters it is faster than finding a settings page, and it
 * cannot leave a field half-edited.
 */
export function AgentSettingsPanel({
  settings, revision, palette, t, width, model, identity, catalogue, control,
}: {
  settings: Settings;
  revision: number;
  palette: Palette;
  t: T;
  width: number;
  /** What the agent is thinking with, as the agent reports it. */
  model?: AgentBaseModel | null;
  /** What the agent has been named, when a person has named it. */
  identity?: AgentIdentity | null;
  /** How many models it falls back to when there is no base model. */
  catalogue?: number;
  /** The lifecycle flags, so the panel can report them beside the commands that change them. */
  control?: AgentControl;
}) {
  const summary = control ? controlSummary(control, t) : null;
  return (
    <Panel borderColor={palette.accent} palette={palette} title={t('cliSettings')} width={width}>
      <Box flexDirection="column">
        <Meta label={t('interfaceLabel')} palette={palette}>{settings.interface}</Meta>
        <IdentityRow identity={identity} palette={palette} t={t} />
        <Meta label={t('agentEndpoint')} palette={palette}>{settings.agentUrl}</Meta>
        <Meta label={t('agentPerson')} palette={palette}>{settings.agentPerson}</Meta>
        {summary ? (
          <Meta label={t('controlLabel')} palette={palette} tone={summary.tone}>{summary.text}</Meta>
        ) : null}
        {control ? (
          <Meta label={t('previewLabel')} palette={palette} tone={control.preview ? 'warning' : 'muted'}>
            {control.preview ? t('previewOn') : t('previewOff')}
          </Meta>
        ) : null}
        <BaseModelRows catalogue={catalogue} model={model} palette={palette} t={t} />
        <Meta label={t('syncRevision')} palette={palette}>{String(revision)}</Meta>
        <Meta label={t('theme')} palette={palette}>{settings.theme}</Meta>
        <Meta label={t('language')} palette={palette}>{settings.language}</Meta>
        <Meta label={t('accent')} palette={palette}>{settings.accent}</Meta>
        <Box marginTop={1}>
          <Text bold color={palette.inkFaint}>{label(t('cliCommands'))}</Text>
          {[
            `/name ${t('agentName')}`,
            `/link ${t('agentEndpoint')}`,
            `/person ${t('agentPerson')}`,
            '/model [provider model]',
            '/preview on | off',
            '/theme',
            '/lang',
            '/accent',
          ].map((command) => (
            <Text color={palette.accent} key={command}>{`  ${command}`}</Text>
          ))}
        </Box>
      </Box>
    </Panel>
  );
}

/**
 * What the agent is called, and the three things that row has to be able to say.
 *
 * A name, "not named yet", and "could not be asked". They are different facts with
 * different next steps and the panel is the only place in a terminal that reports
 * them, so they are kept apart: repeating a name the agent gave is the ordinary
 * answer, no name yet is a warning worth its own colour, and a service that did not
 * answer is a different thing again — it is advice to start something that is
 * already running, and it is not what an unconfigured agent looks like.
 */
function IdentityRow({
  identity, palette, t,
}: {
  identity?: AgentIdentity | null;
  palette: Palette;
  t: T;
}) {
  if (identity === undefined || identity === null) {
    return <Meta label={t('agentName')} palette={palette} tone="danger">{t('agentNameUnreachable')}</Meta>;
  }
  if (!identity.configured) {
    return <Meta label={t('agentName')} palette={palette} tone="muted">{t('agentNameUnnamed')}</Meta>;
  }
  return <Meta label={t('agentName')} palette={palette}>{identity.selfName}</Meta>;
}

/**
 * Two rows, because there are two things to say.
 *
 * A base model with no key is the state that looks fine and is not, so it is
 * reported as its own row in the warning colour rather than being folded into
 * the model name. "No key" is the single most useful line on this panel for
 * somebody whose agent is refusing every question.
 */
function BaseModelRows({
  model, catalogue, palette, t,
}: {
  model?: AgentBaseModel | null;
  catalogue?: number;
  palette: Palette;
  t: T;
}) {
  if (!model) {
    return <Meta label={t('agentBaseModel')} palette={palette}>{t('agentBaseModelUnreachable')}</Meta>;
  }
  if (!model.configured) {
    return (
      <Meta label={t('agentBaseModel')} palette={palette}>
        {t('cliAgentCatalogue', { count: String(catalogue ?? 0) })}
      </Meta>
    );
  }
  // A base model may hold no key at all and still be usable, when it names the
  // variable to read one from — so "no key" is only the truth when there is
  // neither a key nor a variable. Saying otherwise would report a working
  // install as broken, which is the one thing a panel about the agent's own model
  // must not do.
  const fromEnv = !model.keyPresent && Boolean(model.keyEnv);
  return (
    <>
      <Meta label={t('agentBaseModel')} palette={palette}>
        {`${model.provider} · ${model.model}`}
      </Meta>
      <Meta
        label={t('apiKey')}
        palette={palette}
        tone={model.keyPresent || fromEnv ? 'muted' : 'danger'}
      >
        {model.keyPresent
          ? model.keyHint
          : fromEnv
            ? t('envKeyFromFile', { variable: model.keyEnv })
            : t('agentBaseModelKeyMissing', { provider: model.provider })}
      </Meta>
    </>
  );
}

/**
 * The doors the agent can be reached on, and the one this terminal is using.
 *
 * The same panel the browser's people list is, for the same reason it exists
 * at all: a deployment with Telegram open is one fact, and a terminal that could
 * not see it would be a terminal where a conversation happening on somebody's
 * phone is invisible and unreachable. Three facts per door, and the third is what
 * makes the second actionable — a door that admits people but knows no address
 * for *you* is still a door you cannot use, and finding that out here is cheaper
 * than finding it out after a deliberation.
 *
 * Above the doors, the conversations the transcript holds — the people, named
 * the way the browser names them, from the same function. The two lists answer
 * different questions and a reader needs both: a door is how a conversation
 * travels, a person is who it is with, and the id beside each name is the one
 * that opens it here, the way a click opens it there.
 *
 * Every row is numbered, because `/channel <n>` is the spelling that does not
 * ask anybody to type a uuid: the same list the web clicks, indexed.
 *
 * A closed door is listed rather than hidden. Those are in the transcript — a
 * conversation happened there — and the agent can no longer be spoken to on them,
 * which from a filter looks exactly like nothing having been said.
 */
export function ChannelsPanel({
  current, currentConversation, conversations, doors, closed, personId, palette, t, width, scroll, height,
}: {
  current: string;
  currentConversation: string;
  conversations: readonly AgentConversation[];
  doors: readonly AgentDoor[];
  closed: readonly string[];
  personId: string;
  palette: Palette;
  t: T;
  width: number;
  scroll?: number;
  height?: number;
}) {
  const rows: React.ReactElement[] = [];
  rows.push(
    <Text bold color={palette.inkFaint} key="people-h">{t('peopleFilter').toUpperCase()}</Text>,
  );
  conversations.forEach((conversation, index) => {
    const active = conversation.id === currentConversation;
    rows.push(
      <Box key={conversation.id} gap={1}>
        <Text color={palette.inkFaint}>{`${String(index + 1).padStart(2, ' ')}.`}</Text>
        <Text color={toneHex(palette, active ? 'accent' : 'muted')}>{active ? '●' : '○'}</Text>
        <Text
          bold={active}
          color={active ? toneHex(palette, 'accent') : toneHex(palette, 'muted')}
          wrap="truncate"
        >
          {truncateWidth(conversation.label, Math.max(8, width - 34))}
        </Text>
        <Text color={palette.inkFaint} wrap="truncate">
          {truncateWidth(`${channelLabel(conversation.channel)} · ${conversation.id}`, 28)}
        </Text>
      </Box>,
    );
  });
  rows.push(<Text color={palette.inkFaint} key="people-f">{t('cliPeopleSwitch')}</Text>);

  rows.push(
    <Box key="doors-gap" marginTop={1} />,
    <Text bold color={palette.inkFaint} key="doors-h">{t('cliDoorsTitle').toUpperCase()}</Text>,
  );
  if (doors.length === 0) {
    rows.push(<Text color={palette.inkFaint} key="doors-e">{t('cliChannelsEmpty')}</Text>);
  }
  doors.forEach((door, index) => {
    const inUse = door.id === current;
    rows.push(
      <Box flexDirection="column" key={door.id} marginBottom={1}>
        <Box gap={1}>
          <Text color={palette.inkFaint}>{`${String(conversations.length + index + 1).padStart(2, ' ')}.`}</Text>
          <Text color={toneHex(palette, inUse ? 'accent' : door.admits ? 'success' : 'warning')}>
            {inUse ? '●' : door.admits ? '●' : '○'}
          </Text>
          <Text
            bold={inUse}
            color={inUse ? toneHex(palette, 'accent') : toneHex(palette, 'muted')}
          >
            {truncateWidth(channelLabel(door.id), Math.max(8, width - 28))}
          </Text>
          {inUse ? <Text color={palette.accent}>{t('cliChannelsCurrent')}</Text> : null}
          <Text color={palette.inkFaint}>{truncateWidth(door.id, 12)}</Text>
        </Box>
        <Text color={palette.inkFaint}>
          {door.admits
            ? doorStateNote(door, personId, palette, width - 4, (key) => t(key as never))
            : t('cliChannelsAdmitsNobody')}
        </Text>
      </Box>,
    );
  });

  if (closed.length > 0) {
    rows.push(
      <Box key="closed" flexDirection="column" marginTop={1}>
        <Text bold color={palette.inkFaint}>{label(t('channelClosed'))}</Text>
        <Text color={palette.inkFaint}>{closed.map(channelLabel).join(', ')}</Text>
      </Box>,
    );
  }
  rows.push(
    <Box key="hints" marginTop={1} flexDirection="column">
      <Text color={palette.inkFaint}>{t('cliChannelsSwitch')}</Text>
      {/* The panel answers "what can I reach", so it has to say how a door that
          is not there yet gets opened — otherwise a reader who finds no
          Telegram in this list concludes the agent cannot be reached that way,
          which is true of this moment and false of the deployment. */}
      <Text color={palette.inkFaint}>{t('cliChannelsSet')}</Text>
    </Box>,
  );

  return (
    <Panel palette={palette} title={t('panelChannels')} width={width}>
      <SlicedRows height={height} palette={palette} rows={rows} scroll={scroll ?? 0} t={t} />
    </Panel>
  );
}
