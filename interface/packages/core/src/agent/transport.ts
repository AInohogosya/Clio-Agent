import type { DuplexTransport, TurnAssignment } from '../duplex-client.js';
import type { CompletionMessage } from '../provider-adapter.js';
import type { ModelDiscoveryResult } from '../types.js';
import { AgentClient } from './client.js';
import { AgentRequestError, type AgentControlAction } from './types.js';

/**
 * Puts the agent behind the same door a model provider goes through.
 *
 * The turn lifecycle the shared client already owns — record the question, wait,
 * write the answer, and write *nothing* when there is no answer — is exactly the
 * lifecycle a conversation with an agent needs, and it is the one that already
 * refuses to invent a reply. So the agent arrives as a transport rather than as a
 * second client, and both surfaces get the same staleness rules for free.
 *
 * Two things a provider has and the agent does not are handled rather than
 * faked: there is no token stream, so `onDelta` is never called and a partial
 * sentence can never be mistaken for one the agent wrote; and the model
 * catalogue is not this link's to enumerate — the base model's is, so discovery
 * asks the agent rather than answering for it.
 */

/**
 * Clock slack between this process and the agent's clock.
 *
 * The reply is matched on the agent's timestamp against the moment before the
 * question was sent, so a reply recorded a few hundred milliseconds "early" by a
 * slightly fast clock still counts. It is bounded and small: enough to absorb
 * skew, far too little to reach the previous turn's reply.
 */
const CLOCK_SLACK_MS = 1_000;

export interface AgentTransport extends DuplexTransport {
  readonly kind: 'agent';
  /** The lifecycle verbs a surface can ask for. */
  control(action: AgentControlAction): Promise<unknown>;
  undoAction(id: string): Promise<void>;
  closeIntention(id: string): Promise<void>;
}

export interface AgentTransportOptions {
  /**
   * Resolves the live client. A surface finds its link asynchronously, so this is
   * read at the moment of the turn rather than captured at construction — a
   * transport bound to nothing at start-up would refuse every message in the
   * session.
   */
  resolve: () => AgentClient | null;
  now?: () => number;
}

export function createAgentTransport(options: AgentTransportOptions): AgentTransport {
  const now = options.now ?? Date.now;
  return {
    kind: 'agent',
    // The agent is reached over the loopback interface service, which is what
    // signs nothing on the surface's behalf but needs no credential either: the
    // link is trusted because it is local, not because a key was supplied.
    get authenticated(): boolean {
      return true;
    },

async complete(
    prompt: string,
    _history: readonly CompletionMessage[],
    signal: AbortSignal,
    _onDelta: (text: string) => void,
    onAssigned?: (assignment: TurnAssignment) => void,
  ): Promise<string> {
      const agent = options.resolve();
      if (!agent) throw new AgentRequestError('agent_offline', 'no_link');
      const control = agent.getView().control;
      // A stopped agent cannot take a turn, and waiting for one it cannot take
      // would leave the reader watching a spinner that means nothing.
      if (control.stopped || control.emergency) {
        throw new AgentRequestError('agent_stopped', control.emergency ? 'emergency' : 'stopped');
      }
      // A paused agent is the same fact with a different fix: `resume`, not a
      // start. It is asked here rather than inferred at the end of a wait,
      // because a paused agent says nothing for as long as the pause lasts and
      // the reader would otherwise be told it had stopped existing.
      if (control.paused) {
        throw new AgentRequestError('agent_stopped', 'paused');
      }
      // Nothing has come from the agent on this link, so there is nobody to
      // answer. Saying that now costs nothing; saying it after the whole stall
      // budget says the same thing slowly, and by then the reader has spent two
      // and a half minutes watching a spinner for a machine that is not running.
      if (agent.isAgentAbsent()) {
        throw new AgentRequestError('agent_not_running');
      }
      const alreadyAnswered = new Set(agent.getView().messages.map((message) => message.id));

      const after = now() - CLOCK_SLACK_MS;
      const sentAt = await agent.sendMessage(prompt);
      if (!sentAt) throw new AgentRequestError('agent_refused', 'not_queued');
      // Both ids are reported the moment they are known, because the surface's own
      // turn is drawn under ids minted locally while the agent's transcript holds
      // the same two messages under the row ids it assigned. The surface and the
      // store are unioned by id, so without these the question and the answer are
      // each on screen twice — one exchange drawn as four messages, two of them
      // reading as the agent repeating itself. Naming them is what makes that union
      // collapse the pairs, with the agent's row winning as intended.
      //
      // The question's id is reported now rather than at the end of the turn, the
      // moment the agent has assigned it and while the surface still has a live
      // turn to reconcile. A turn the reader supersedes before the answer arrives
      // keeps its question in the transcript, and the agent's row for that question
      // is already in the store: deferring the id to the end of the turn would
      // leave exactly those questions drawn twice, for good and for a turn no
      // longer in flight to fix them.
      onAssigned?.({ questionId: sentAt });
      // `sentAt` is handed to the wait as well as checked, because it is the one
      // thing that ties a "heard you, not answering" back to this question. The
      // agent is free to decline, and when it does the surface should say the
      // agent declined rather than sit out the whole stall budget and then
      // report that it had gone quiet.
      const reply = await agent.awaitReply(after, signal, alreadyAnswered, sentAt);
      onAssigned?.({ answerId: reply.id });
      const text = reply.text.trim();
      if (!text) throw new AgentRequestError('agent_declined', 'empty');
      return text;
    },

    async discoverModels(signal?: AbortSignal): Promise<ModelDiscoveryResult> {
      void signal;
      // A catalogue the agent can honour, asked of the agent. It used to report
      // that there was none, on the grounds that the agent picks its own model
      // per call and a list here could not be honoured — which was true of the
      // *routing* catalogue and wrong about the *base model*, the one a person
      // configures and the agent does use. So the list is the base model's, and
      // it comes back over the link rather than being invented here.
      const agent = options.resolve();
      if (!agent) return { models: [], source: 'offline', error: 'agent_offline' };
      return agent.discoverBaseModels();
    },

    control(action: AgentControlAction): Promise<unknown> {
      const agent = options.resolve();
      if (!agent) return Promise.reject(new AgentRequestError('agent_offline', 'no_link'));
      return agent.control(action);
    },

    undoAction(id: string): Promise<void> {
      const agent = options.resolve();
      if (!agent) return Promise.reject(new AgentRequestError('agent_offline', 'no_link'));
      return agent.undoAction(id);
    },

    closeIntention(id: string): Promise<void> {
      const agent = options.resolve();
      if (!agent) return Promise.reject(new AgentRequestError('agent_offline', 'no_link'));
      return agent.closeIntention(id);
    },
  };
}
