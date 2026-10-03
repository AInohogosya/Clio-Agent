#!/usr/bin/env node

/**
 * `npm start` — one command from a fresh clone to a running agent and a served page.
 *
 * This exists because the start-up was correct on paper and unreliable in practice,
 * and both halves of that were the same mistake: too many separate steps, each one
 * optional, in an order nobody was ever told. The build and the launch are different
 * processes, so whether a page existed depended on which finished first — and the
 * bridge decided that once, at startup, so a bridge which lost the race could never
 * serve a page for the rest of its life. Every status signal then came from
 * `/api/health`, which such a bridge answers perfectly: `ethos status` reported
 * `interface: up` over a 404, and re-running `ethos up` reported "already running"
 * and changed nothing. The documented remedy was the one thing guaranteed not to help.
 *
 * So this file owns the order, checks the answers, and puts right what it finds:
 *
 *   1. the Node side:  the bridge's dependencies, and the interface's build
 *   2. the virtualenv: created, and the agent installed, so `ethos` exists at all
 *   3. the last run:   a session already in existence is ended — the supervisor,
 *                      then whatever holds the port — so that what follows is a
 *                      new session and not an old one reported as a new one
 *   4. the agent:      `ethos up` — Postgres, migrations, seeds, supervisor, wait
 *   5. the page:       asked for, and served here if nobody else is serving it
 *
 * Step 2 is the only part that could not live inside `ethos up`: building a
 * virtualenv needs `uv`, and `uv` is what installs this project, so there is no
 * interpreter yet to ask. Everything after it is delegated rather than restated.
 *
 * Nothing here needs a virtualenv to be active, or `ethos` on `PATH` — the venv's
 * interpreter is called by absolute path, and `npm start` is reached over Node. That
 * is deliberate: the people who need this command most are the ones whose shell has
 * neither, and a start-up that assumes an activated virtualenv is a start-up that
 * fails for the people furthest from having set one up.
 *
 *   npm start                    everything: agent, page, and the order between them
 *   npm start -- --no-open       the same, without opening a browser
 *   npm start -- --interface     the page alone, in the foreground, no agent
 *   npm start -- --build-only    build the interface and stop
 *   npm start -- --keep          do not end a session that is already running
 *
 * `--keep` exists because ending a session is not free of consequences, and the
 * person who typed it is the only one who knows whether they are mid-conversation
 * with the agent. It restores the older behaviour — leave a running agent and a
 * served page alone, stop only what cannot serve — which is the right one for a
 * second terminal that only wanted the page. It is not the default, because the
 * default is what makes this command mean what it says: everything this run
 * starts is started now, and nothing it reports as running was started earlier.
 */

import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import http from "node:http";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "web");
const INTERFACE = join(ROOT, "interface");
const VENV = join(ROOT, ".venv");
const PYTHON = join(VENV, "bin", "python");
const INDEX_HTML = join(INTERFACE, "packages", "web", "dist", "index.html");
const BRIDGE_ENTRY = join(WEB, "server", "index.mjs");

const argv = process.argv.slice(2);
const interfaceOnly = argv.includes("--interface");
const buildOnly = argv.includes("--build-only");
const noOpen = argv.includes("--no-open");
const keep = argv.includes("--keep");

const say = (message) => console.log(message);
const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

function die(message, hint) {
  console.error(`-- ${message}`);
  if (hint) console.error(hint);
  process.exit(1);
}

/** A tool, looked for beside the interpreter running this script and then on PATH. */
function findTool(name) {
  const beside = join(dirname(process.execPath), name);
  if (existsSync(beside)) return beside;
  const probe = spawnSync(name, ["--version"], { stdio: "ignore" });
  return probe.error?.code === "ENOENT" ? null : name;
}

/** Runs a step in the foreground and returns its exit status. */
function run(command, { optional = false } = {}) {
  const result = spawnSync(command[0], command.slice(1), { cwd: ROOT, stdio: "inherit" });
  if (result.error) {
    if (optional) return null;
    die(`could not run ${command[0]}: ${result.error.message}`);
  }
  return result.status ?? 1;
}

/**
 * A step whose failure has to stop the start-up, and be reported as itself.
 *
 * Not `run(...)` alone: an `npm install` that fails and is not checked is followed by
 * an `npm run build` against a `node_modules` that is not there, so the thing
 * reported is the build rather than the install that caused it. Each of these names
 * itself, so the line that says what went wrong is the line that failed.
 */
function step(label, command) {
  say(`-- ${label}`);
  if (run(command) !== 0) die(`${label} failed.`, "   The output above is why.");
}

// npm, resolved once. Reached over `npm start`, so npm is on PATH by definition — but
// the interpreter running this file is not necessarily beside it (an nvm Node with a
// Homebrew npm, or the reverse), so both places are tried.
const npm = findTool("npm");
if (!npm) die("npm was not found. Install Node, which includes it, and try again.");

// ---------------------------------------------------------------- 1. the Node side

function prepareNode() {
  if (!existsSync(join(WEB, "node_modules"))) {
    step("installing the bridge's dependencies", [npm, "install", "--prefix", WEB]);
  }
  if (existsSync(INDEX_HTML)) return;

  if (!existsSync(join(INTERFACE, "node_modules"))) {
    step("installing the interface's dependencies", [npm, "install", "--prefix", INTERFACE]);
  }
  // `core` before `web`: the page builds against core's emitted types, and building
  // them together fails in a way that reads like a bug in the interface rather than
  // an ordering mistake.
  step("building the interface", [npm, "run", "build", "--workspace", "@project-phone/core", "--prefix", INTERFACE]);
  step("building the interface page", [npm, "run", "build", "--workspace", "@project-phone/web", "--prefix", INTERFACE]);
  if (!existsSync(INDEX_HTML)) {
    die(
      "the interface build finished but produced no index.html, so there is no page to serve.",
      `   Expected one at ${INDEX_HTML}. The build's own output above says why.`,
    );
  }
}

// ------------------------------------------------------------- 2. the virtualenv

/**
 * A virtualenv with this project installed, creating it when there is not one.
 *
 * `uv` first, because it is what the Makefile and `start.sh` use and it is much
 * faster; `python3 -m venv` second, because it is always present and needs nothing
 * installed before it. `[dev]` because that is what both of those install, and a
 * machine that can start the agent but not run its tests fails confusingly later
 * rather than obviously now.
 */
function preparePython() {
  if (!existsSync(PYTHON)) {
    const uv = findTool("uv");
    if (uv) {
      // `optional`: a machine that already has 3.12 makes this a no-op that exits
      // non-zero for its own reasons, and the `uv venv` below is the step that
      // decides whether there is an interpreter.
      run([uv, "python", "install", "3.12"], { optional: true });
      step(`creating the Python 3.12 virtualenv with ${uv}`, [uv, "venv", VENV, "--python", "3.12"]);
      step("installing the agent", [uv, "pip", "install", "--python", PYTHON, "-e", ".[dev]"]);
    } else {
      const system = findTool("python3.12") ?? findTool("python3");
      if (!system) {
        die(
          "there is no virtualenv here and no way to make one: neither uv nor python3 was found.",
          "   Install Python 3.12 (https://www.python.org/downloads/) or uv (https://docs.astral.sh/uv/), then run this again.",
        );
      }
      step(`creating a virtualenv with ${system}`, [system, "-m", "venv", VENV]);
      step("installing the agent", [PYTHON, "-m", "pip", "install", "-e", ".[dev]"]);
    }
  } else if (!existsSync(join(VENV, "bin", "ethos"))) {
    step("installing the agent", [PYTHON, "-m", "pip", "install", "-e", ".[dev]"]);
  }
  // The console script is what `ethos stop` and `make` reach for, and this function's
  // whole purpose is that a person never has to activate anything to get one. An
  // install that "succeeded" without producing it has not finished.
  if (!existsSync(join(VENV, "bin", "ethos"))) {
    die(
      "the agent is installed in .venv but there is no `ethos` command in it.",
      `   Look in ${join(VENV, "bin")}. If the project was installed somewhere else, remove .venv and run this again.`,
    );
  }
}

// ------------------------------------------------------------------- 3. the port

/**
 * The port the bridge will bind, resolved the way the bridge resolves it:
 * `ETHOS_WEB_PORT`, then `web.http_port` from the shipped config with the agent's
 * own home merged over it, then 8720.
 *
 * Read through `web/node_modules` — which step 1 has already installed — rather than
 * by a second hand-rolled parser of someone else's configuration file. `createRequire`
 * rooted at the bridge's own entry point resolves exactly as the bridge resolves,
 * so there is one rule here instead of two that agree until an edit.
 */
function resolvePort() {
  const fromEnv = Number(process.env.ETHOS_WEB_PORT);
  if (Number.isInteger(fromEnv) && fromEnv > 0) return fromEnv;
  try {
    const require = createRequire(BRIDGE_ENTRY);
    const { load } = require("js-yaml");
    const configDir = process.env.ETHOS_CONFIG_DIR
      ? resolve(process.env.ETHOS_CONFIG_DIR)
      : join(ROOT, "config");
    const readYaml = (file) => {
      try {
        return load(readFileSync(file, "utf8")) ?? {};
      } catch {
        return {};
      }
    };
    const home = process.env.ETHOS_HOME || join(process.env.HOME ?? "", ".ethos");
    const merged = { ...readYaml(join(configDir, "channels.yaml")), ...readYaml(join(home, "channels.yaml")) };
    const port = merged?.web?.http_port;
    if (Number.isInteger(port) && port > 0) return port;
  } catch {
    /* no readable config; the default below is the documented one */
  }
  return 8720;
}

/**
 * What is on the port, asked of the page rather than of `/api/health`.
 *
 * A bridge that cannot serve a page still answers its whole API, which is precisely
 * why a liveness check that asks the API cannot tell a working interface from a
 * broken one. `servesPage` is the only answer the steps below act on.
 */
function probePort(port, timeoutMs = 2000) {
  return new Promise((settle) => {
    const request = http.get({ host: "127.0.0.1", port, path: "/", timeout: timeoutMs }, (response) => {
      const type = String(response.headers["content-type"] ?? "");
      const chunks = [];
      response.on("data", (chunk) => chunks.push(chunk));
      response.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        settle({
          listening: true,
          servesPage: response.statusCode === 200 && type.includes("text/html"),
          detail: `answered ${response.statusCode} as ${type.split(";")[0] || "nothing"}`,
        });
      });
    });
    request.on("timeout", () => request.destroy());
    request.on("error", () =>
      settle({ listening: false, servesPage: false, detail: "nothing is listening" }),
    );
  });
}

/**
 * The pid listening on a loopback port, or null.
 *
 * The filter is one argument, `-tiTCP:<port>`, because lsof reads `TCP:8720` as a
 * protocol-and-address pair and `8720` on its own as a *file* to look up — and it
 * exits 0 either way, having simply found nothing. Split into two arguments it
 * answers nothing at all, which is a null that looks exactly like a free port.
 */
function pidOnPort(port) {
  const found = spawnSync("lsof", [`-tiTCP:${port}`, "-sTCP:LISTEN"], { encoding: "utf8" });
  if (found.error) return null;
  const pid = Number((found.stdout ?? "").trim().split("\n")[0]);
  return Number.isInteger(pid) && pid > 1 ? pid : null;
}

/** A pid's own command line, or nothing — the whole of it, however long. */
function pidCommand(pid) {
  const found = spawnSync("ps", ["-p", String(pid), "-o", "command="], { encoding: "utf8" });
  return found.error ? "" : (found.stdout ?? "").trim();
}

/** Who a pid is, in a phrase — so "stopping X" names a process, not a number. */
function describePid(pid) {
  const command = pidCommand(pid);
  if (!command) return `pid ${pid}`;
  if (isBridgeCommand(command)) return "an interface bridge";
  if (isAgentCommand(command)) return "the agent";
  return `pid ${pid}`;
}

/**
 * Whether a command line is one of this checkout's own.
 *
 * Two questions, not one, because the two processes that can be holding this
 * checkout's port are started by different parents and spell it differently: the
 * supervisor starts the bridge with an absolute path, and a person in a terminal
 * — `node web/server/index.mjs`, which is what the README, the Makefile and this
 * file all use — starts it with a relative one.
 *
 * The agent is matched on how it is invoked rather than on the word "ethos"
 * appearing anywhere: a substring test also answers yes to a text editor with
 * this checkout's README open, and ending somebody's session by pid because of
 * what they had open is not a start command's decision to make. `python -m ethos
 * …` is how the supervisor and all seven of its children run, and `…/bin/ethos` is
 * the console script, so those two are the whole of it.
 */
function isBridgeCommand(command) {
  return command.includes(BRIDGE_ENTRY) || command.includes("web/server/index.mjs");
}

function isAgentCommand(command) {
  return /(?:-m\s+ethos\b|\/ethos\b|^\S*\/ethos\b|^ethos\b)/.test(command);
}

/** Whether a pid belongs to a session this script is entitled to end. */
function isOurs(pid) {
  const command = pidCommand(pid);
  if (!command) return false;
  return isBridgeCommand(command) || isAgentCommand(command);
}

/** Waits for the page, because a supervisor starts its children in its own time. */
async function waitForPage(port, budgetMs) {
  const until = Date.now() + budgetMs;
  for (;;) {
    const answer = await probePort(port);
    if (answer.servesPage || Date.now() >= until) return answer;
    await sleep(500);
  }
}

// --------------------------------------------------------- 3. the session before

/**
 * The supervisor's pid, if an earlier session's agent is still up.
 *
 * `ethos status --pid` rather than the pid file: a file left behind by a `kill -9`
 * names a supervisor that is not there, and a start-up that believed it would stop
 * nothing, hand the port to a second supervisor, and then report two agents. The
 * command exits non-zero when there is no live process, which is the answer wanted
 * here and needs no parsing of prose to get.
 */
function supervisorPid() {
  const asked = spawnSync(PYTHON, ["-m", "ethos", "status", "--pid"], { cwd: ROOT, encoding: "utf8" });
  if (asked.error || asked.status !== 0) return null;
  const pid = Number((asked.stdout ?? "").trim());
  return Number.isInteger(pid) && pid > 1 ? pid : null;
}

/**
 * The agent of the session being replaced, ended before a new one is started.
 *
 * `ethos stop` rather than a signal of our own, because the supervisor is what
 * knows its children: it is asked once, it closes each of them, it removes its own
 * pid file, and the command does not return until that has happened — so by the
 * time this function is done, nothing of the old session is left to hold a port.
 *
 * The refusal to continue is deliberate. A supervisor that will not stop is not a
 * problem this start-up can route around: `ethos up` finds its pid, reports the
 * agent as already running, and changes nothing, which is the one outcome this
 * step exists to make impossible. Better to stop here with the pid in the message
 * than to start a second seven-process agent beside the first one.
 */
function stopRunningAgent() {
  const pid = supervisorPid();
  if (pid === null) return;
  say(`-- ending the session already running: stopping the agent (supervisor pid ${pid})`);
  if (run([PYTHON, "-m", "ethos", "stop"], { optional: true }) === 0) return;

  // A non-zero exit is not one thing. On Windows it can mean the supervisor is
  // gone and only its children were hard-stopped, which the port check below
  // finishes off; it can also mean a supervisor is still standing and ignoring
  // the request. Only the second is fatal, and only the supervisor's own pid
  // file says which of the two happened.
  if (supervisorPid() === null) {
    say("-- the supervisor is gone; anything it left behind is dealt with below");
    return;
  }
  die(
    `the agent already running (supervisor pid ${pid}) would not stop, and a new one would fight it for every port.`,
    "   End it yourself with `kill " + pid + "`, or read what it is saying first: `ethos logs`.",
  );
}

/**
 * The port, emptied of whoever holds it, so that whatever serves it afterwards is
 * part of the session being started now.
 *
 * A page is not evidence that the page is *this run's*: a bridge from an earlier
 * `npm start` answers `GET /` with a perfectly good interface and a conversation
 * the agent has already forgotten, and a start-up that reported that as "ready"
 * would be true in every word and useful in none. So a holder of ours is stopped
 * whether it serves the page or not — except under `keepServedPage`, which is
 * `--keep` and means exactly that — and the session started behind it is a
 * genuinely new one rather than the old one still answering.
 *
 * A holder that is *not* ours is stopped only when it cannot serve the page, which
 * is the one state that makes every other step in this file pointless and that the
 * old start-up could reach and never leave. When it *is* serving something, it is
 * not a stale session of this checkout but somebody's server, and this is the one
 * situation where starting on another port is the answer rather than a fallback
 * worth reaching for.
 */
async function freePort(port, { keepServedPage = false } = {}) {
  const held = await probePort(port);
  if (!held.listening) return;

  const pid = pidOnPort(port);
  const ours = pid !== null && isOurs(pid);

  if (ours && held.servesPage && keepServedPage) {
    say(`-- the interface is already being served on http://127.0.0.1:${port} (--keep)`);
    return;
  }
  if (!ours && held.servesPage) {
    die(
      `something that is not part of this checkout is serving ${port}, so this session cannot have it.`,
      `   It is ${pid ? describePid(pid) : "a process this script cannot identify"}. Stop it, or start beside it: ETHOS_WEB_PORT=8721 npm start`,
    );
  }

  say(
    `-- stopping ${pid ? describePid(pid) : "the process"} on ${port}: ` +
      (ours
        ? "it belongs to the session being replaced"
        : `it holds the port and serves no page (${held.detail})`),
  );
  if (pid !== null) {
    // Terminate, wait, then kill: the grace is for a bridge that closes its
    // listener and its database connections on the way out, and the kill is for
    // the one that does not — a session that will not let go of its own port is
    // not a reason to fail a start-up that can take it by force.
    run(["kill", String(pid)], { optional: true });
    for (let waited = 0; waited < 6000 && (await probePort(port)).listening; waited += 250) {
      await sleep(250);
    }
    if ((await probePort(port)).listening && isOurs(pid)) {
      run(["kill", "-9", String(pid)], { optional: true });
      for (let waited = 0; waited < 4000 && (await probePort(port)).listening; waited += 250) {
        await sleep(250);
      }
    }
  }
  if ((await probePort(port)).listening) {
    die(
      `something is still holding ${port} and will not let go of it.`,
      `   Find it with \`lsof -iTCP:${port} -sTCP:LISTEN\`, or start on another port: ETHOS_WEB_PORT=8721 npm start`,
    );
  }
}

// --------------------------------------------------------- 4 & 5. agent, then page

/** The bridge, in this terminal, for as long as it runs. */
function serveInForeground(note) {
  if (note) say(note);
  // `process.execPath`, not `node`: the bridge is started by the interpreter running
  // this script, not by whichever `node` happens to be first on PATH.
  const bridge = spawn(process.execPath, [BRIDGE_ENTRY], { cwd: ROOT, stdio: "inherit" });
  for (const signal of ["SIGINT", "SIGTERM"]) process.on(signal, () => bridge.kill(signal));
  bridge.on("exit", (code, signal) => process.exit(signal ? 1 : (code ?? 0)));
  return new Promise(() => {});
}

prepareNode();

if (buildOnly) {
  say("-- the interface is built.");
  process.exit(0);
}

preparePython();

const port = resolvePort();

// Step 3. A session already in existence is ended first, so that what steps 4 and 5
// start is a new one. The agent is stopped only when this run is going to start an
// agent: `--interface` asks for the page and nothing else, and taking somebody's
// agent down for that would be a worse surprise than the stale page it replaces.
// The port is emptied either way, because the page this run serves has to be this
// run's — `--keep` excepts a page that is already there, and still clears a holder
// that cannot serve one.
if (!keep && !interfaceOnly) stopRunningAgent();
await freePort(port, { keepServedPage: keep });

if (interfaceOnly) {
  // `--keep` together with a page already being served has nothing left to serve:
  // the port was left alone on purpose, so the page already there is the answer,
  // and starting a second bridge here would only fail on a port it was told not
  // to take. `--no-open` is implied — the browser is already wherever it was.
  if (keep && (await probePort(port)).servesPage) {
    say(`-- ready: http://127.0.0.1:${port}`);
    process.exit(0);
  }
  await serveInForeground();
} else {
  // Step 4. `ethos up` brings up Postgres, migrates, seeds, starts the supervisor and
  // waits for a sign of life. It is a no-op on an agent that is already running,
  // which is why step 3 ends one rather than relying on this to notice: a start-up
  // that starts nothing and reports the old session as a new one is worse than one
  // that says nothing happened.
  const status = run([PYTHON, "-m", "ethos", "up", ...(noOpen ? ["--no-open"] : [])]);
  if (status !== 0) {
    die("the agent did not come up.", "   `ethos logs` says why; the log paths are in the output above.");
  }

  // Step 5. The supervisor starts the bridge as an *optional* child, and optional
  // children are not restarted — correct for the agent, which must survive without
  // Node, and wrong for somebody who just asked for a page. So the page is asked
  // for, and served here when nobody else will.
  const ready = await waitForPage(port, 10_000);
  if (ready.servesPage) {
    say(`-- ready: http://127.0.0.1:${port}`);
    process.exit(0);
  }
  await serveInForeground(
    `-- the agent is up, but nothing is serving the page (${ready.detail}).\n` +
      "--   Serving it here instead; the agent keeps running either way.",
  );
}
