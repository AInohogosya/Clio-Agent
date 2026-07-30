# Operations → Known Limitations

An honest list of what Clio-Agent-2 can't do or doesn't do well.

1. **No sandbox around shell commands.** `shell_command` runs anything, as you,
   with no allow-list or confirmation. This is the single biggest risk — see
   [Safety](safety.md). Prefer a container/VM or least-privilege account.
2. **Autonomous mode is on by default** and calls the model every few seconds,
   spending API quota/money and acting without your input. Disable it for an
   on-demand assistant.
3. **No default model.** You *must* set `CURRENT_MODEL` or the thinking loop won't
   start.
4. **Memory is lossy.** Context is compressed (summarized) once it grows, so the
   agent can "forget" details from much earlier. The context log *is* durably
   persisted and restored on restart (flushed on clean shutdown, with a `.bak`
   fallback if the file is corrupt); the only residual risk is a hard crash within
   ~10 entries of the last write. `/clear_context` is now recoverable via
   `/restore_context` (it backs the context up first).
5. **The LLM lock guards the model, not the tools.** Provider/model changes are
   blocked by default (good), but tool actions — including `shell_command` — are
   not similarly gated.
6. **Stuck/repetition detection is basic.** `RepetitionDetector` only catches an
   *exact* action signature repeating within a small recent window, so varied or
   long-pause loops may go unnoticed.
7. **Compression failures are swallowed.** If the LLM summarization errors, the
   agent logs `"Compression failed"` and continues with uncompressed context rather
   than halting.
8. **Discord is Beta** and exposes fewer features than the CLI/Telegram.
9. **Needs internet + a paid API key** to do anything useful; it can't run fully
   offline (except against a local Ollama/OpenAI-compatible server you host).
10. **Two `config/` folders can confuse you** (root vs `clio_agent_2/config`). Only
    the latter is used.
11. **Prompt-injection exposure.** Because the agent can fetch web pages and run
    commands, malicious content it reads could try to steer its actions. The model
    lock limits *model* switching, but not tool use.

See also: [Meta Controller](../core/meta-controller.md), [Context Management](../architecture/context-management.md).
