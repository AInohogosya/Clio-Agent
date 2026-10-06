import { useInput } from 'ink';
import { AGENT_CONTROL_ACTIONS, LANGUAGES, MAX_TERMINAL_TEXT_LENGTH, type Settings } from '@project-phone/core';
import type { createTranslator } from '@project-phone/core';
import { accentStep, type Tone } from './palette.js';

export type Translator = ReturnType<typeof createTranslator>;

/**
 * The screens the line can be on.
 *
 * The agent's panels are screens rather than overlays because they replace the
 * transcript rather than floating over it: an intention board drawn on top of a
 * conversation is two things competing for the same rows, and the transcript is
 * the thing a reader scrolls back through.
 */
export type Screen = 'chat' | 'settings' | 'presence' | 'intentions' | 'actions' | 'budget' | 'guardian' | 'channels';

/** Every screen that is not the conversation, and so is left by the same key. */
export const PANEL_SCREENS: readonly Screen[] = ['presence', 'intentions', 'actions', 'budget', 'guardian', 'channels'];

export function isPanelScreen(screen: Screen): boolean {
  return PANEL_SCREENS.includes(screen);
}

export type Overlay = 'none' | 'help';

/** Matches a control chord whether the input layer reports the letter or the code. */
export function isCtrl(value: string, key: { ctrl?: boolean }, letter: string): boolean {
  if (!key.ctrl) return false;
  return value === letter || value.charCodeAt(0) === letter.toUpperCase().charCodeAt(0) - 64;
}

export function isEscape(key: { escape?: boolean }): boolean {
  return key.escape === true;
}

/** Everything a keystroke may read. */
export interface ComposerState {
  input: string;
  cursor: number;
  scroll: number;
  /** How far the transcript can be scrolled back, in rows. */
  limit: number;
  chatHeight: number;
  scrollable: boolean;
  pending: boolean;
  screen: Screen;
  overlay: Overlay;
  settings: Settings;
}

/** Everything a keystroke may change. */
export interface ComposerActions {
  setInput: (value: string) => void;
  setCursor: (cursor: number | ((current: number) => number)) => void;
  setScroll: (scroll: number | ((current: number) => number)) => void;
  setScreen: (screen: Screen) => void;
  setOverlay: (overlay: Overlay) => void;
  submit: (raw: string) => void;
  clearConversation: () => void;
  interrupt: () => void;
  quit: () => void;
  patchSettings: (patch: Partial<Settings>) => void;
  say: (text: string, tone?: Tone) => void;
  t: Translator;
  /**
   * The agent verbs. Optional because the direct-provider surface has no agent,
   * and a command that is offered on a surface with nothing behind it is a
   * command that reports success and does nothing.
   */
  agentControl?: (action: (typeof AGENT_CONTROL_ACTIONS)[number]) => void;
  agentUndo?: (id: string) => void;
  agentCancel?: (id: string) => void;
  /** Steps through the agent panels and back to the conversation. */
  cyclePanel?: (backwards: boolean) => void;
  /** Repoints the agent address. Validated by the kernel, which owns the rules. */
  agentAddress?: (field: 'link' | 'person', value: string) => void;
  /**
   * Shows or changes the agent's base model.
   *
   * `null` is a complete argument: with no provider it says what the agent is
   * using, and with one it writes it. A key is deliberately not an argument —
   * the agent holds it, and a command line is the wrong place for one.
   */
  agentBaseModel?: (argument: string) => void;
  /**
   * Shows or changes the name the agent answers to.
   *
   * `null` is a complete argument, as it is for the base model: with no name it says
   * what the agent is called, and with one it names it. The whole setting is a
   * single word, so the argument is the name itself — there is no provider, model or
   * endpoint to disambiguate, and splitting it would only give a second way to get it
   * wrong.
   */
  agentName?: (argument: string) => void;
  /**
   * Shows or changes where this terminal talks to the agent, or opens one
   * conversation.
   *
   * Three spellings, because a terminal has no dropdown: `/channels` opens the
   * lists, `/channel <name>` moves onto a door, and `/channel <door:person>`
   * opens one conversation — the key the transcript files messages under and the
   * browser's people list selects on, so several people on one door are not
   * drawn as one stream here either. A bare `/channel` says which one is in use,
   * the same way a bare `/name` reports the name and a bare `/person` reports
   * the person — reporting is what the no-argument form is *for*, and it is the
   * form a reader reaches for when they want to know where their words are
   * going.
   */
  agentChannel?: (argument: string) => void;
}

/** The verb a lifecycle command word means, or `null` for an unknown one. */
export const LIFECYCLE_WORDS: Record<string, (typeof AGENT_CONTROL_ACTIONS)[number]> = {
  hold: 'pause_all',
  pause: 'pause_all',
  'hold-actions': 'pause_actions',
  'pause-actions': 'pause_actions',
  resume: 'resume',
  stop: 'stop',
  emergency: 'emergency_stop',
};

/**
 * Preview mode is a toggle, and a toggle is not one of the verbs above.
 *
 * `LIFECYCLE_WORDS` maps a word to a single fixed action, which is exactly right
 * for `/hold` and `/stop` and wrong here. `/preview` with nothing after it would
 * have to guess which way the flag is standing, and the input layer does not hold
 * the agent's state to guess from — and a safety switch that flips on a coin is
 * worse than one that asks. So the argument names the state, and a missing one
 * says what to type rather than picking for the reader.
 */
export const PREVIEW_WORDS: Record<string, 'preview_on' | 'preview_off'> = {
  on: 'preview_on',
  off: 'preview_off',
};

export interface DraftEdit {
  input: string;
  cursor: number;
}

/**
 * Types a character at the caret.
 *
 * The caret follows the insertion instead of jumping to the end of the draft:
 * inserting one character in the middle of a line used to move the caret to
 * `input.length + 1`, which made a typo in the middle of a message impossible
 * to fix without starting again.
 */
export function insertAt(input: string, cursor: number, addition: string): DraftEdit {
  const position = Math.max(0, Math.min(cursor, input.length));
  const clipped = addition.slice(0, MAX_TERMINAL_TEXT_LENGTH);
  const next = `${input.slice(0, position)}${clipped}${input.slice(position)}`.slice(0, MAX_TERMINAL_TEXT_LENGTH);
  return { input: next, cursor: Math.min(position + clipped.length, next.length) };
}

/** Deletes the character before the caret, if there is one. */
export function deleteBefore(input: string, cursor: number): DraftEdit | null {
  if (cursor <= 0) return null;
  return { input: input.slice(0, cursor - 1) + input.slice(cursor), cursor: cursor - 1 };
}

/** `/accent` with an argument sets that colour; without one it steps the presets. */
export function nextAccent(accent: string, argument: string): string {
  if (/^#[0-9a-f]{6}$/i.test(argument)) return argument.toUpperCase();
  return accentStep(accent, 1);
}

/**
 * Runs a slash command. Returns false when the text is not a command at all, so
 * the caller sends it as a message instead. Every command reports true, including
 * an unknown one: typing `/halp` must not post a message to the provider.
 */
export function runCommand(message: string, state: ComposerState, actions: ComposerActions): boolean {
  if (!message.startsWith('/')) return false;
  // Only the command word is folded; the argument is the user's own text and is
  // passed through exactly as typed. `/send What Is New?` used to reach the
  // provider as `what is new?`.
  const body = message.slice(1);
  const split = body.search(/\s/);
  const name = (split === -1 ? body : body.slice(0, split)).toLowerCase();
  const argument = split === -1 ? '' : body.slice(split + 1).trim();
  switch (name) {
    case 'quit':
    case 'exit':
      actions.quit();
      return true;
    case 'help':
    case '?':
      actions.setOverlay(state.overlay === 'help' ? 'none' : 'help');
      return true;
    case 'settings':
    case 'config':
    case 'models':
      actions.setScreen('settings');
      return true;
    case 'clear':
      actions.clearConversation();
      return true;
    case 'interrupt':
      actions.interrupt();
      return true;
    case 'theme':
      actions.patchSettings({ theme: state.settings.theme === 'dark' ? 'light' : 'dark' });
      return true;
    case 'lang':
    case 'language': {
      const index = LANGUAGES.indexOf(state.settings.language);
      actions.patchSettings({ language: LANGUAGES[(index + 1) % LANGUAGES.length] ?? 'en' });
      return true;
    }
    case 'accent':
      actions.patchSettings({ accent: nextAccent(state.settings.accent, argument) });
      return true;
    case 'send':
      if (argument) actions.submit(argument);
      return true;
    default:
      break;
  }

  // Agent verbs. They are matched after the shared commands so `/stop` keeps
  // meaning the agent's stop rather than the provider's.
  if (name === 'preview') {
    if (!actions.agentControl) {
      actions.say(actions.t('interfaceDirectHint'), 'warning');
      return true;
    }
    const preview = PREVIEW_WORDS[argument.toLowerCase()];
    if (!preview) {
      actions.say(actions.t('previewUsage'), 'warning');
      return true;
    }
    actions.agentControl(preview);
    return true;
  }
  const lifecycle = LIFECYCLE_WORDS[name];
  if (lifecycle) {
    if (!actions.agentControl) {
      actions.say(actions.t('interfaceDirectHint'), 'warning');
      return true;
    }
    actions.agentControl(lifecycle);
    return true;
  }
  const panels: Record<string, Screen> = {
    presence: 'presence',
    intentions: 'intentions',
    intents: 'intentions',
    actions: 'actions',
    journal: 'actions',
    budget: 'budget',
    spend: 'budget',
    guardian: 'guardian',
    safety: 'guardian',
    // The list, as a panel rather than as a `/channel` argument: a terminal has
    // no dropdown, so the names have to be visible somewhere to be typeable.
    channels: 'channels',
    doors: 'channels',
  };
  if (panels[name]) {
    actions.setScreen(panels[name]);
    return true;
  }
  if (name === 'link' || name === 'person') {
    if (!actions.agentAddress) {
      actions.say(actions.t('interfaceDirectHint'), 'warning');
      return true;
    }
    if (!argument) {
      actions.say(`${name === 'link' ? actions.t('agentEndpoint') : actions.t('agentPerson')}: ${state.settings[name === 'link' ? 'agentUrl' : 'agentPerson']}`, 'accent');
      return true;
    }
    actions.agentAddress(name, argument);
    return true;
  }
  if (name === 'model' || name === 'base-model') {
    if (!actions.agentBaseModel) {
      actions.say(actions.t('interfaceDirectHint'), 'warning');
      return true;
    }
    actions.agentBaseModel(argument);
    return true;
  }
  if (name === 'name' || name === 'agent-name') {
    if (!actions.agentName) {
      actions.say(actions.t('interfaceDirectHint'), 'warning');
      return true;
    }
    actions.agentName(argument);
    return true;
  }
  if (name === 'channel' || name === 'via') {
    if (!actions.agentChannel) {
      actions.say(actions.t('interfaceDirectHint'), 'warning');
      return true;
    }
    // An empty argument is a real request — "which one am I on?" — and it is
    // answered by the same call, which reports rather than moves. Sending `all`
    // back to the local line is the one thing it will not do: a terminal has no
    // way to send a message *to* every door at once, and pretending otherwise
    // would be a lie about where the next thing typed is going.
    if (argument && argument.toLowerCase() !== 'all' && argument.toLowerCase() !== 'none') {
      actions.agentChannel(argument);
      return true;
    }
    actions.agentChannel('');
    return true;
  }
  if (name === 'undo') {
    if (!actions.agentUndo) {
      actions.say(actions.t('interfaceDirectHint'), 'warning');
      return true;
    }
    if (argument) actions.agentUndo(argument);
    else actions.say(`${actions.t('cliAgentNoUndo')}`, 'warning');
    return true;
  }
  if (name === 'cancel') {
    if (!actions.agentCancel) {
      actions.say(actions.t('interfaceDirectHint'), 'warning');
      return true;
    }
    if (argument) actions.agentCancel(argument);
    return true;
  }

  actions.say(`${actions.t('cliUnknownCommand')}: /${name}`, 'danger');
  return true;
}

/**
 * Every key the chat screen answers to, in the order it looks at them: leaving
 * the line first, then the control chords, then the overlay, then navigation,
 * then the composer.
 */
export function useComposerInput(state: ComposerState, actions: ComposerActions): void {
  const { input, cursor, scroll, limit, chatHeight, scrollable, pending, screen, overlay } = state;
  const page = Math.max(1, chatHeight - 2);

  useInput((value, key) => {
    if (isCtrl(value, key, 'c') || isCtrl(value, key, 'q')) {
      actions.quit();
      return;
    }
    if (isCtrl(value, key, 's')) {
      // One chord cycles the same places the slash commands reach: the wizard,
      // then back to the conversation, then the help panel.
      if (overlay === 'help') actions.setOverlay('none');
      else if (screen === 'settings') actions.setScreen('chat');
      else actions.setScreen('settings');
      return;
    }
    if (isCtrl(value, key, 'p')) {
      actions.cyclePanel?.(false);
      return;
    }
    if (isCtrl(value, key, 'l')) {
      actions.clearConversation();
      return;
    }
    if (isCtrl(value, key, 'k')) {
      actions.setInput('');
      actions.setCursor(0);
      return;
    }

    // Escape unwinds the topmost thing on screen, in order, so the way out is
    // always the way back from wherever the reader is. The help panel says
    // "Esc closes this", so Esc has to close it.
    if (isEscape(key)) {
      if (overlay === 'help') {
        actions.setOverlay('none');
        return;
      }
      if (screen !== 'chat') {
        actions.setScreen('chat');
        return;
      }
      if (pending) {
        actions.interrupt();
        return;
      }
      if (scroll > 0) {
        actions.setScroll(0);
        return;
      }
      actions.setScreen('settings');
      return;
    }

    // A panel is modal: nothing below it takes keystrokes, so a keystroke
    // cannot land in a composer the reader cannot see.
    if (overlay === 'help') return;

    // Only the conversation has a composer. A panel is read, and a keystroke
    // that landed in a composer nobody can see would be a message nobody sent.
    if (screen !== 'chat') return;

    if (key.pageDown) {
      actions.setScroll((current) => Math.min(limit, current + page));
      return;
    }
    if (key.pageUp) {
      actions.setScroll((current) => Math.max(0, current - page));
      return;
    }
    if (key.upArrow) {
      if (scrollable) actions.setScroll((current) => Math.min(limit, current + 1));
      return;
    }
    if (key.downArrow) {
      if (scrollable) actions.setScroll((current) => Math.max(0, current - 1));
      return;
    }
    if (key.return) {
      actions.submit(input);
      return;
    }
    if (key.leftArrow) {
      actions.setCursor((current) => Math.max(0, current - 1));
      return;
    }
    if (key.rightArrow) {
      actions.setCursor((current) => Math.min(input.length, current + 1));
      return;
    }
    if (key.backspace) {
      const edit = deleteBefore(input, cursor);
      if (!edit) return;
      actions.setInput(edit.input);
      actions.setCursor(edit.cursor);
      return;
    }
    if (key.delete) {
      if (cursor >= input.length) return;
      const edit = { input: input.slice(0, cursor) + input.slice(cursor + 1), cursor };
      actions.setInput(edit.input);
      actions.setCursor(edit.cursor);
      return;
    }
    if (isCtrl(value, key, 'u')) {
      actions.setInput(input.slice(cursor));
      actions.setCursor(0);
      return;
    }
    if (isCtrl(value, key, 'a')) {
      actions.setCursor(0);
      return;
    }
    if (isCtrl(value, key, 'e')) {
      actions.setCursor(input.length);
      return;
    }
    if (key.tab) {
      actions.cyclePanel?.(key.shift === true);
      return;
    }
    if (value && !key.ctrl && !key.meta) {
      const edit = insertAt(input, cursor, value);
      actions.setInput(edit.input);
      actions.setCursor(edit.cursor);
    }
  });
}
