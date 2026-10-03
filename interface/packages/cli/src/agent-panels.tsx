import React from 'react';
import { Box, Text } from 'ink';
import {
  actionTone,
  controlSummary,
  createTranslator,
  deadlineLabel,
  flattenIntentions,
  formatClock,
  formatUsd,
  humanizeState,
  intentionTone,
  stateTone,
  channelLabel,
  type AgentBaseModel,
  type AgentDoor,
  type AgentIdentity,
  type AgentIntention,
  type AgentLinkStatus,
  type AgentView,
  type Language,
  type Settings,
} from '@project-phone/core';
import { doorStateNote } from './channels.js';
import { Divider, Meta, Panel } from './tui-kit.js';
import { toneHex, truncateWidth, type Palette, type Tone } from './palette.js';
import { meter } from './render.js';

export type T = ReturnType<typeof createTranslator>;

/**
 * The terminal's twin of the web panels.
 *
 * Every row is one fact the agent reported, and the wording and colours come from
 * the kernel, so a reader who moves between this and the browser is not learning
 * a second vocabulary for the same agent.
 */

const MARK: Record<string, string> = {
  accent: '◆',
  success: '●',
  warning: '▲',
  danger: '■',
  muted: '·',
};

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
          <Text color={toneHex(palette, tone)}>{`${MARK[tone] ?? MARK.muted} ${humanizeState(view.presence.state)}`}</Text>
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
              {MARK[tone] ?? MARK.muted}
            </Text>
          );
        })}
      </Text>
    </Panel>
  );
}

export function ThoughtsPanel({
  view, language, palette, t, width,
}: { view: AgentView; language: Language; palette: Palette; t: T; width: number }) {
  const rows = view.thoughts.slice(0, 8).map((thought) => (
    <Box flexDirection="column" key={thought.id}>
      <Text color={palette.inkMuted} wrap="truncate">
        {`${thought.ts ? formatClock(thought.ts, language) : '--:--'}  ${thought.summary}`}
      </Text>
    </Box>
  ));
  return (
    <Panel palette={palette} title={t('panelThoughts')} width={width}>
      <Box flexDirection="column">
        {rows.length ? rows : <Text color={palette.inkFaint}>{t('thoughtsEmpty')}</Text>}
      </Box>
    </Panel>
  );
}

function IntentionRows({
  nodes, palette, t, language, width, now, depth, limit,
}: {
  nodes: readonly AgentIntention[];
  palette: Palette;
  t: T;
  language: Language;
  width: number;
  now: number;
  depth: number;
  limit: number;
}): React.ReactElement | null {
  if (!nodes.length || limit <= 0) return null;
  return (
    <Box flexDirection="column">
      {nodes.slice(0, limit).map((node) => {
        const tone = intentionTone(node.status);
        const deadline = deadlineLabel(node.deadline, now, t);
        const budget = node.budget_usd === null
          ? null
          : ` ${formatUsd(node.spent_usd ?? 0, language, 2)}/${formatUsd(node.budget_usd, language, 2)}`;
        return (
          <Box flexDirection="column" key={node.id} marginLeft={depth}>
            <Box>
              <Text color={toneHex(palette, tone)}>{'● '}</Text>
              <Text color={palette.ink} wrap="truncate">{node.title || node.id}</Text>
              {budget ? <Text color={palette.inkFaint}>{budget}</Text> : null}
            </Box>
            {deadline ? (
              <Box>
                <Text color={deadline.overdue ? toneHex(palette, 'danger') : palette.inkFaint}>{`  ${deadline.text}`}</Text>
              </Box>
            ) : null}
            <IntentionRows
              depth={depth + 1}
              language={language}
              limit={limit - 1}
              nodes={node.children}
              now={now}
              palette={palette}
              t={t}
              width={width}
            />
          </Box>
        );
      })}
    </Box>
  );
}

export function IntentionsPanel({
  view, language, palette, t, width, now,
}: { view: AgentView; language: Language; palette: Palette; t: T; width: number; now: number }) {
  return (
    <Panel palette={palette} badge={String(flattenIntentions(view.intentions).length)} title={t('panelIntentions')} width={width}>
      {view.intentions.length ? (
        <IntentionRows depth={0} language={language} limit={20} nodes={view.intentions} now={now} palette={palette} t={t} width={width} />
      ) : (
        <Text color={palette.inkFaint}>{t('intentionsEmpty')}</Text>
      )}
    </Panel>
  );
}

export function ActionsPanel({
  view, language, palette, t, width,
}: { view: AgentView; language: Language; palette: Palette; t: T; width: number }) {
  const rows = view.actions.slice(0, 14).map((action) => (
    <Box key={action.id}>
      <Text color={palette.inkFaint}>{`${action.ts_start ? formatClock(action.ts_start, language) : '--:--'}  `}</Text>
      <Text color={toneHex(palette, actionTone(action.status))}>{'● '}</Text>
      <Text color={palette.ink} wrap="truncate">{action.tool}</Text>
      <Text color={palette.inkFaint}>{`  ${action.status}`}</Text>
      {action.undo_ref ? <Text color={palette.accent}>{'  /undo'}</Text> : null}
    </Box>
  ));
  return (
    <Panel palette={palette} badge={String(view.actions.length)} title={t('panelActions')} width={width}>
      <Box flexDirection="column">
        {rows.length ? rows : <Text color={palette.inkFaint}>{t('actionsEmpty')}</Text>}
      </Box>
    </Panel>
  );
}

export function BudgetPanel({
  view, language, palette, t, width,
}: { view: AgentView; language: Language; palette: Palette; t: T; width: number }) {
  const caps = view.budget.caps;
  const rows = view.budget.todayByBucket;
  const total = rows.reduce((sum, row) => sum + row.total, 0);
  const cells = Math.max(8, width - 8);
  return (
    <Panel palette={palette} borderColor={palette.accent} title={t('panelBudget')} width={width}>
      <Box flexDirection="column">
        <Meta label={t('budgetToday')} palette={palette}>{formatUsd(total, language)}</Meta>
        {caps.daily_usd > 0 ? (
          <Box flexDirection="column" marginTop={1}>
            <Text>{meter(total, caps.daily_usd, cells, palette, total > caps.daily_usd ? 'danger' : 'accent')}</Text>
            <Text color={palette.inkFaint}>
              {t('budgetOf', { spent: formatUsd(total, language), cap: formatUsd(caps.daily_usd, language) })}
            </Text>
          </Box>
        ) : (
          <Text color={palette.inkFaint}>{t('budgetNoCap')}</Text>
        )}
        {rows.length ? <Divider palette={palette} width={width - 4} /> : null}
        {rows.map((row) => {
          const cap = row.bucket === 'commitment' ? caps.commitment_usd : caps.discretionary_usd;
          return (
            <Box flexDirection="column" key={row.bucket} marginTop={1}>
              <Meta label={row.bucket === 'commitment' ? t('budgetCommitment') : t('budgetDiscretionary')} palette={palette}>
                {formatUsd(row.total, language)}
              </Meta>
              {cap > 0 ? <Text>{meter(row.total, cap, cells, palette, row.bucket === 'commitment' ? 'accent' : 'warning')}</Text> : null}
            </Box>
          );
        })}
        {view.budget.byModel.length ? (
          <Box flexDirection="column" marginTop={1}>
            <Divider palette={palette} width={width - 4} />
            <Text bold color={palette.inkFaint}>{label(t('budgetByModel'))}</Text>
            {view.budget.byModel.slice(0, 4).map((row) => (
              <Meta key={row.model} label={truncateWidth(row.model, 18)} palette={palette}>{formatUsd(row.total, language)}</Meta>
            ))}
          </Box>
        ) : null}
      </Box>
    </Panel>
  );
}

export function GuardianPanel({
  view, language, palette, t, width, now,
}: { view: AgentView; language: Language; palette: Palette; t: T; width: number; now: number }) {
  const guardian = view.guardian;
  const integrity = guardian.integrity;
  return (
    <Panel palette={palette} title={t('panelGuardian')} width={width}>
      <Box flexDirection="column">
        <Meta label={t('guardianIntegrity')} palette={palette}>
          {!integrity ? t('guardianIntegrityStale') : (
            <Text color={toneHex(palette, integrity.ok ? 'success' : 'danger')}>
              {integrity.ok ? t('guardianIntegrityOk') : t('guardianIntegrityFail')}
            </Text>
          )}
        </Meta>
        {integrity?.ran_at ? (
          <Meta label={t('presenceUpdated')} palette={palette}>{formatClock(integrity.ran_at, language)}</Meta>
        ) : null}
        {integrity && integrity.artifact_mismatches.length > 0 ? (
          <Text color={toneHex(palette, 'danger')}>
            {t('guardianMismatches', { count: integrity.artifact_mismatches.length })}
          </Text>
        ) : null}
        {guardian.auditHead ? (
          <Meta label={t('guardianAudit')} palette={palette}>
            {`#${guardian.auditHead.seq ?? '—'} ${(guardian.auditHead.hash ?? '').slice(0, 10)}`}
          </Meta>
        ) : null}
        <Meta label={t('budgetProtected')} palette={palette}>{String(guardian.protectedCount)}</Meta>

        <Box flexDirection="column" marginTop={1}>
          <Text bold color={palette.inkFaint}>{label(t('guardianSnapshots'))}</Text>
          {guardian.snapshots.length ? guardian.snapshots.slice(0, 5).map((snapshot) => (
            <Meta key={snapshot.name} label={truncateWidth(snapshot.name, 18)} palette={palette}>
              {snapshot.created_at ? formatClock(snapshot.created_at, language) : t('presenceNever')}
            </Meta>
          )) : <Text color={palette.inkFaint}>{t('guardianSnapshotsEmpty')}</Text>}
        </Box>

        <Box flexDirection="column" marginTop={1}>
          <Text bold color={palette.inkFaint}>{label(t('guardianTrash'))}</Text>
          {guardian.trash.length ? guardian.trash.slice(0, 6).map((entry, index) => {
            const retained = entry.retention_until ? Date.parse(entry.retention_until) : Number.NaN;
            const expired = Number.isFinite(retained) && retained <= now;
            const shown = entry.retention_until
              ? formatClock(entry.retention_until, language)
              : t('presenceNever');
            return (
              <Meta key={`${entry.id}-${index}`} label={truncateWidth(entry.origin, 16)} palette={palette}>
                <Text color={toneHex(palette, expired ? 'muted' : 'warning')}>{shown}</Text>
              </Meta>
            );
          }) : <Text color={palette.inkFaint}>{t('guardianTrashEmpty')}</Text>}
        </Box>
      </Box>
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
  settings, revision, palette, t, width, model, identity, catalogue,
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
}) {
  return (
    <Panel borderColor={palette.accent} palette={palette} title={t('cliSettings')} width={width}>
      <Box flexDirection="column">
        <Meta label={t('interfaceLabel')} palette={palette}>{settings.interface}</Meta>
        <IdentityRow identity={identity} palette={palette} t={t} />
        <Meta label={t('agentEndpoint')} palette={palette}>{settings.agentUrl}</Meta>
        <Meta label={t('agentPerson')} palette={palette}>{settings.agentPerson}</Meta>
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

function label(value: string): string {
  return value.toUpperCase();
}

/**
 * The doors the agent can be reached on, and the one this terminal is using.
 *
 * The same panel the browser's channel filter is, for the same reason it exists
 * at all: a deployment with Telegram open is one fact, and a terminal that could
 * not see it would be a terminal where a conversation happening on somebody's
 * phone is invisible and unreachable. Three facts per door, and the third is what
 * makes the second actionable — a door that admits people but knows no address
 * for *you* is still a door you cannot use, and finding that out here is cheaper
 * than finding it out after a deliberation.
 *
 * A closed door is listed rather than hidden. Those are in the transcript — a
 * conversation happened there — and the agent can no longer be spoken to on them,
 * which from a filter looks exactly like nothing having been said.
 */
export function ChannelsPanel({
  current, doors, closed, personId, palette, t, width,
}: {
  current: string;
  doors: readonly AgentDoor[];
  closed: readonly string[];
  personId: string;
  palette: Palette;
  t: T;
  width: number;
}) {
  return (
    <Panel palette={palette} title={t('panelChannels')} width={width}>
      <Box flexDirection="column">
        {doors.length === 0 ? <Text color={palette.inkFaint}>{t('cliChannelsEmpty')}</Text> : null}
        {doors.map((door) => {
          const inUse = door.id === current;
          return (
            <Box flexDirection="column" key={door.id} marginBottom={1}>
              <Box gap={1}>
                <Text color={toneHex(palette, inUse ? 'accent' : door.admits ? 'success' : 'warning')}>
                  {inUse ? '●' : door.admits ? '●' : '○'}
                </Text>
                <Text
                  bold={inUse}
                  color={inUse ? toneHex(palette, 'accent') : toneHex(palette, 'muted')}
                >
                  {truncateWidth(channelLabel(door.id), Math.max(8, width - 24))}
                </Text>
                {inUse ? <Text color={palette.accent}>{t('cliChannelsCurrent')}</Text> : null}
                <Text color={palette.inkFaint}>{truncateWidth(door.id, 12)}</Text>
              </Box>
              <Text color={palette.inkFaint}>
                {door.admits
                  ? doorStateNote(door, personId, palette, width - 4, (key) => t(key as never))
                  : t('cliChannelsAdmitsNobody')}
              </Text>
            </Box>
          );
        })}
        {closed.length > 0 ? (
          <Box flexDirection="column" marginTop={1}>
            <Text bold color={palette.inkFaint}>{label(t('channelClosed'))}</Text>
            <Text color={palette.inkFaint}>{closed.map(channelLabel).join(', ')}</Text>
          </Box>
        ) : null}
        <Box marginTop={1} flexDirection="column">
          <Text color={palette.inkFaint}>{t('cliChannelsSwitch')}</Text>
          {/* The panel answers "what can I reach", so it has to say how a door that
              is not there yet gets opened — otherwise a reader who finds no
              Telegram in this list concludes the agent cannot be reached that way,
              which is true of this moment and false of the deployment. */}
          <Text color={palette.inkFaint}>{t('cliChannelsSet')}</Text>
        </Box>
      </Box>
    </Panel>
  );
}
