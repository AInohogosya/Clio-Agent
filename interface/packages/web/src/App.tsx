import { useEffect, useState, type CSSProperties } from 'react';
import { createTranslator, type AgentControlAction } from '@project-phone/core';
import { ChatScreen, type CoordinationState } from './screens/ChatScreen';
import { AgentScreen } from './screens/AgentScreen';
import { SettingsScreen } from './screens/SettingsScreen';
import { useDuplex } from './hooks/useDuplex';

type Screen = 'main' | 'settings';

/**
 * Chooses the surface, and keeps the theme the reader chose.
 *
 * `agent` is the default because that is what this kit is for: an interface for
 * an agent, with the agent's own state on screen next to the conversation. The
 * direct-provider chat is still here, for the case where the point is to talk to
 * a model with nothing else in the room.
 */
export default function App() {
  const controller = useDuplex();
  const { snapshot } = controller;
  const {
    view,
    streaming,
    link,
    notice,
    control,
    undoAction,
    closeIntention,
    refresh,
    conversation,
    conversations,
    closedChannels,
    setConversation,
  } = controller;
  const [screen, setScreen] = useState<Screen>('main');
  const [armedEmergency, setArmedEmergency] = useState(false);
  const [pendingAction, setPendingAction] = useState<AgentControlAction | null>(null);
  const t = createTranslator(snapshot.settings.language);

  useEffect(() => {
    const theme = snapshot.settings.theme;
    document.documentElement.dataset.theme = theme;
    document.documentElement.lang = snapshot.settings.language;
    document
      .querySelector<HTMLMetaElement>('meta[name="theme-color"]')
      ?.setAttribute('content', theme === 'light' ? '#efede7' : '#101210');
  }, [snapshot.settings.language, snapshot.settings.theme]);

  const shellStyle = { '--accent': snapshot.settings.accent } as CSSProperties;
  const coordination: CoordinationState = {
    tone: controller.bridgeStatus === 'connected' ? 'live' : controller.bridgeStatus === 'blocked' ? 'blocked' : 'local',
    configFile: controller.configFile,
    credential: controller.credential,
    credentialSource: controller.credentialSource,
    // A key that lives in the shared file is used by the local bridge, so the
    // page never needs to hold it.
    viaBridge: controller.bridgeStatus === 'connected' && controller.credential.present,
    lastSyncedAt: controller.lastSyncedAt,
  };

  const runControl = (action: AgentControlAction) => {
    setPendingAction(action);
    void control(action).finally(() => setPendingAction(null));
  };

  /**
   * Whether the agent is in preview mode, or `null` when it could not be asked.
   *
   * `null` while the link is not online, because that is the whole difference
   * between "the agent may act" and "this page has no idea". The view carries the
   * flag from the agent's own lifecycle row, so it is the answer rather than a
   * local guess, and it arrives back on its own after the write — nothing here
   * keeps a second copy that could disagree with the machine.
   */
  const preview = link === 'online' ? view.control.preview : null;

  return (
    <div className="instrument-canvas flex h-dvh min-h-0 flex-col overflow-hidden" style={shellStyle}>
      {screen === 'settings' ? (
        <SettingsScreen
          discover={(candidate, signal, options) => controller.discoverModels(signal, candidate, options)}
          onClose={() => setScreen('main')}
          onUpdate={controller.updateSettings}
          clearIdentity={controller.clearIdentity}
          preview={preview}
          readBaseModel={controller.readBaseModel}
          readDoors={controller.readDoors}
          readEnvApiKeys={controller.readEnvApiKeys}
          readIdentity={controller.readIdentity}
          saveBaseModel={controller.saveBaseModel}
          saveDoor={controller.saveDoor}
          saveIdentity={controller.saveIdentity}
          // The same lifecycle verb every other control button uses, so preview
          // mode is answered by the same gate that answers "stop" — and reaches the
          // toolhost by the same road, rather than a second path that could lag
          // behind the first.
          setPreview={(on) => control(on ? 'preview_on' : 'preview_off').then(() => undefined)}
          settings={snapshot.settings}
        />
      ) : snapshot.settings.interface === 'agent' ? (
        <AgentScreen
          armedEmergency={armedEmergency}
          connected={link !== 'offline'}
          conversation={conversation}
          conversations={conversations}
          closedChannels={closedChannels}
          notice={notice}
          onArmEmergency={setArmedEmergency}
          onCloseIntention={(id) => void closeIntention(id)}
          onControl={runControl}
          onInterrupt={controller.interrupt}
          onOpenSettings={() => setScreen('settings')}
          onRefresh={() => void refresh()}
          onSelectConversation={(id) => setConversation(
            conversations.find((entry) => entry.id === id) ?? null,
          )}
          onSend={(text) => void controller.sendMessage(text)}
          onUndo={(id) => void undoAction(id)}
          pendingAction={pendingAction}
          snapshot={snapshot}
          streaming={streaming}
          view={view}
        />
      ) : (
        <ChatScreen
          coordination={coordination}
          onClear={controller.clearConversation}
          onInterrupt={controller.interrupt}
          onOpenSettings={() => setScreen('settings')}
          onSend={(text) => void controller.sendMessage(text)}
          snapshot={snapshot}
        />
      )}
      <div aria-live="polite" className="sr-only">{snapshot.status === 'thinking' ? t('thinking') : ''}</div>
    </div>
  );
}
