import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import pg from "pg";
import { dump as dumpYaml, load as loadYaml } from "js-yaml";
import { WebSocketServer } from "ws";

/**
 * The interface bridge.
 *
 * The interface is Clio Agent 3 Beta 1's: a browser page and a terminal client that both read
 * this agent and ask it to do things. So this process has exactly two jobs, and
 * it is a peer of the agent rather than part of it — it holds no state of its own,
 * and everything it shows is a row the agent wrote.
 *
 *   1. Serve the built interface, and
 *   2. translate between HTTP/WebSocket and the agent's own store: read a view,
 *      queue a message or a lifecycle change, and relay the agent's events.
 *
 * It is a *bridge*, not a second writer. A command from a surface is turned into
 * an `events` row, which is the same channel the agent's own adapters publish on,
 * so there is exactly one path into the life loop and one place where the guardian
 * gate and the attention scorer see what a person asked for. The one thing this
 * file writes directly is the inbound `messages` row, and it writes it under the
 * id it then publishes — the agent's own `record_inbound` inserts with
 * `ON CONFLICT DO NOTHING`, so the row is recorded once and the person's own
 * message is visible in the transcript the moment they send it.
 */

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DB_DSN = process.env.ETHOS_DSN ?? "postgresql://ethos:ethos@127.0.0.1:5433/ethos";

/**
 * Where the interface is built from.
 *
 * Resolved rather than hard-coded so a deployment can build the interface somewhere
 * else, and skipped entirely when it has not been built — the bridge is still a working
 * API without it, and refusing to start would make "run the agent, look at it
 * over HTTP" depend on a JavaScript toolchain.
 */
const PHONE_DIST = process.env.ETHOS_PHONE_DIST
  ? path.resolve(process.env.ETHOS_PHONE_DIST)
  : path.resolve(ROOT, "..", "interface", "packages", "web", "dist");

const { Pool, Client } = pg;
const pool = new Pool({ connectionString: DB_DSN, max: 8 });

const app = express();
app.use(express.json({ limit: "1mb" }));

/**
 * Every route answers, even when the database is unreachable.
 *
 * Express 4 does not catch a rejected promise from a handler, so one transient
 * database error would leave the request hanging with no response and, once the
 * rejections piled up, take the process with them. A bridge that goes silent is
 * worse than one that says it cannot reach the agent: a surface needs to be able
 * to tell "the agent said no" from "the interface is down", and only a reply can
 * tell it that.
 */
function handle(fn) {
  return (req, res) => {
    Promise.resolve()
      .then(() => fn(req, res))
      .catch((error) => {
        console.error(`ethos-web ${req.method} ${req.path}: ${error?.message ?? error}`);
        if (res.headersSent) return;
        res.status(503).json({ error: "agent_unavailable", detail: String(error?.message ?? error) });
      });
  };
}

const startedAt = Date.now();

/**
 * The document policy, as a header rather than a `<meta>` tag.
 *
 * The interface ships the same policy in its markup, but a `frame-ancestors` or a
 * `sandbox` directive delivered in a meta element is ignored by every browser —
 * it is a header-only directive. This interface is the one an agent's whole state
 * is visible through, so framing it is not a cosmetic concern and the header is
 * where the rule actually takes.
 *
 * Registered before the routes rather than after, so the API's JSON answers
 * carry `nosniff` too: a content type a client misreads is a misread on every
 * road, and the static files are not the only responses with a stake in it.
 */
const DOCUMENT_CSP = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  // The page loads one module and one stylesheet, both from this origin, and
  // talks only to this origin: the API and the event stream are both here.
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
].join("; ");

app.use((_req, res, next) => {
  res.setHeader("Content-Security-Policy", DOCUMENT_CSP);
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  res.setHeader("Cross-Origin-Opener-Policy", "same-origin");
  next();
});

async function dbUp() {
  try {
    await pool.query("SELECT 1");
    return "up";
  } catch {
    return "down";
  }
}

app.get("/api/health", handle(async (_req, res) => {
  res.json({
    ok: true,
    uptime: Math.round((Date.now() - startedAt) / 1000),
    db: await dbUp(),
  });
}));

/**
 * The hard limits the agent enforces on itself, read from the agent's own config.
 *
 * Taken from `config/permissions.yaml` — the same file the gateway reads when it
 * refuses a request — because a dashboard that hard-codes the cap it was built
 * with is a dashboard that quietly starts lying the day the cap is retuned. When
 * the file cannot be read the caps come back as zero and the panels say "no cap
 * reported", which is honest in a way that a guessed number is not.
 */
const CONFIG_DIR = process.env.ETHOS_CONFIG_DIR
  ? path.resolve(process.env.ETHOS_CONFIG_DIR)
  : path.resolve(ROOT, "..", "config");

/**
 * The port this bridge listens on.
 *
 * `ETHOS_WEB_PORT` wins, then the `web.http_port` key of `config/channels.yaml`
 * — the file the documentation points a deployment at, so editing it does what
 * it says — and 8720 when neither is set. Read here rather than at the top
 * because the config directory has to be resolved first.
 */
function resolvePort() {
  const fromEnv = Number(process.env.ETHOS_WEB_PORT);
  if (Number.isFinite(fromEnv) && Number.isInteger(fromEnv) && fromEnv > 0) return fromEnv;
  try {
    const port = readChannelsFile()?.web?.http_port;
    if (typeof port === "number" && Number.isFinite(port) && Number.isInteger(port) && port > 0) return port;
  } catch {
    /* no channels.yaml to read; the default below is the documented one */
  }
  return 8720;
}

/**
 * The agent's own home, and the one place a credential may be written.
 *
 * `config/` is a directory that is meant to be committed, so a base model — which
 * carries an API key — does not go there. It goes where the rest of the agent's
 * private state is, which is `paths.home` in `config/ethos.yaml` with
 * `ETHOS_HOME` on top: exactly the order `ethos.config.load_config` applies, so
 * what this process writes is what the gateway reads. Resolving it from the same
 * two sources rather than hard-coding `~/.ethos` is what keeps a `start.sh` run
 * and a bare `node web/server/index.mjs` run pointed at the same file.
 *
 * `~` is expanded the way `ethos/config.py` expands it — with `os.path.expanduser`
 * on the Python side, and by hand here. Doing it by hand is only safe if it is
 * done *properly*, and the obvious one-liner is not: stripping the `~` off
 * `~/.ethos` leaves `/.ethos`, which `path.resolve` then reads as an absolute
 * path and puts at the root of the filesystem. That is not a theoretical
 * mistake — it made this process read and write a different file from the agent
 * it stands in front of, and then fail to save a base model at all, with nothing
 * in the answer to say so.
 */
function expandHome(value) {
  const wanted = value.trim();
  if (wanted === "~") return os.homedir();
  if (wanted.startsWith("~/") || wanted.startsWith("~\\")) {
    return path.join(os.homedir(), wanted.slice(2));
  }
  return path.resolve(wanted);
}

/**
 * A path this process may read a credential out of, or write one into.
 *
 * The filesystem root is refused rather than used: a home that resolves to `/`
 * is the signature of the expansion above having gone wrong, and carrying on
 * would mean creating a directory at the root of a sealed system volume and
 * reporting the result as "the agent could not be reached". Falling back to the
 * conventional location instead keeps the answer wrong in the safe direction —
 * a home that is not the one the agent uses, which somebody can see — rather than
 * right in none of them.
 */
function usableHome(value) {
  return path.isAbsolute(value) && path.dirname(value) !== value ? value : null;
}

function resolveAgentHome() {
  if (process.env.ETHOS_HOME) {
    const explicit = usableHome(expandHome(process.env.ETHOS_HOME));
    if (explicit) return explicit;
  }
  try {
    const raw = fs.readFileSync(path.join(CONFIG_DIR, "ethos.yaml"), "utf8");
    const home = loadYaml(raw)?.paths?.home;
    if (typeof home === "string" && home.trim()) {
      const configured = usableHome(expandHome(home));
      if (configured) return configured;
    }
  } catch {
    /* no config to read; the default below is the agent's own default */
  }
  return usableHome(path.join(os.homedir(), ".ethos")) ?? path.resolve(".ethos");
}

const AGENT_HOME = resolveAgentHome();
const BASE_MODEL_FILE = path.join(AGENT_HOME, "base_model.yaml");
const IDENTITY_FILE = path.join(AGENT_HOME, "identity.yaml");
/**
 * The two files a channel's own settings live in, in the agent's home.
 *
 * `channels.yaml` here is an *overlay*, not a copy: the same shape as the shipped
 * `config/channels.yaml`, merged over it key by key by `readChannelsFile`, exactly
 * as `ethos.config.load_config` does on the Python side. `people.yaml` overlays
 * the contact book the same way, and a `null` address there is a removal.
 *
 * Both are here for the reason `base_model.yaml` is: a bot token and a chat id
 * belong to this installation, and `config/` is a directory meant to be committed.
 * The shipped file can only *name* the variable a token might be in, which is no
 * use at all to a person who installed the agent on a laptop and has nowhere but
 * a form to put one — which is why there was no way to give this agent a Telegram
 * token except a shell.
 */
const CHANNELS_FILE = path.join(AGENT_HOME, "channels.yaml");
const PEOPLE_FILE = path.join(AGENT_HOME, "people.yaml");

/**
 * Resolved here rather than beside the function that computes it, because the
 * function reads a file in the agent's home and the home is only resolved below.
 */
const PORT = resolvePort();

/**
 * The providers a base model may name, and the API shape each one speaks.
 *
 * A vendor, not a protocol, is what a person picks in a settings screen; what the
 * agent needs to know is which adapter to hand the request to. So this maps
 * forward into the four wire protocols `ethos/gateway/service.py` has an adapter
 * for, and a provider that is not in this table is not one the agent can be
 * pointed at.
 */
const BASE_PROVIDERS = {
  openai: { protocol: "openai", requiresKey: true, auth: "bearer", env: ["OPENAI_API_KEY"] },
  anthropic: { protocol: "anthropic", requiresKey: true, auth: "x-api-key", env: ["ANTHROPIC_API_KEY"] },
  // Two names, because the Google SDKs read either one and a deployment that set
  // up its shell for one of them is working; the first that is set wins, and
  // which one it was is reported back so the button can name it.
  gemini: { protocol: "google", requiresKey: true, auth: "x-goog-api-key", env: ["GEMINI_API_KEY", "GOOGLE_API_KEY"] },
  openrouter: { protocol: "openai_compat", requiresKey: true, auth: "bearer", env: ["OPENROUTER_API_KEY"] },
  deepseek: { protocol: "openai_compat", requiresKey: true, auth: "bearer", env: ["DEEPSEEK_API_KEY"] },
  mistral: { protocol: "openai_compat", requiresKey: true, auth: "bearer", env: ["MISTRAL_API_KEY"] },
  groq: { protocol: "openai_compat", requiresKey: true, auth: "bearer", env: ["GROQ_API_KEY"] },
  xai: { protocol: "openai_compat", requiresKey: true, auth: "bearer", env: ["XAI_API_KEY"] },
  ollama: { protocol: "openai_compat", requiresKey: false, auth: "bearer", env: ["OLLAMA_API_KEY"] },
  lmstudio: { protocol: "openai_compat", requiresKey: false, auth: "bearer", env: ["LMSTUDIO_API_KEY"] },
};

const BASE_PROVIDER_DEFAULTS = {
  openai: { baseUrl: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  anthropic: { baseUrl: "https://api.anthropic.com/v1", model: "claude-3-5-sonnet-latest" },
  gemini: { baseUrl: "https://generativelanguage.googleapis.com/v1beta", model: "gemini-2.0-flash" },
  openrouter: { baseUrl: "https://openrouter.ai/api/v1", model: "openai/gpt-4o-mini" },
  deepseek: { baseUrl: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  mistral: { baseUrl: "https://api.mistral.ai/v1", model: "mistral-small-latest" },
  groq: { baseUrl: "https://api.groq.com/openai/v1", model: "llama-3.3-70b-versatile" },
  xai: { baseUrl: "https://api.x.ai/v1", model: "grok-2-latest" },
  ollama: { baseUrl: "http://localhost:11434/v1", model: "llama3.2" },
  lmstudio: { baseUrl: "http://localhost:1234/v1", model: "qwen2.5-7b-instruct" },
};

const MAX_API_KEY_LENGTH = 512;
/**
 * A credential a *channel* needs, rather than a provider key.
 *
 * The longest of them is chosen rather than issued — a WhatsApp verify token is
 * whatever string the deployment invents — so this is deliberately larger than
 * `MAX_API_KEY_LENGTH`, and it is the same number `@project-phone/core` puts on
 * the field that fills it in. A form that capped a channel credential at an API
 * key's length would refuse a value the agent would have taken.
 */
const MAX_CREDENTIAL_LENGTH = 1_024;
const MAX_ENDPOINT_LENGTH = 2_048;
const MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$/;
/** Anything that would corrupt a stored value, a request body or a log line. */
const CONTROL = /[\x00-\x1f\x7f]/;
/** How many ids one catalogue may contribute, and how long the probe may take. */
const MAX_CATALOGUE_ENTRIES = 500;
const CATALOGUE_TIMEOUT_MS = 20_000;
const ANTHROPIC_VERSION = "2023-06-01";
/**
 * What may be used as a variable name.
 *
 * The names this process reads are its own, but a *file* may name one too, and
 * a file is plain text somebody can edit. `process.env` is an object, so a name
 * that is not a name at all can reach a prototype member rather than an
 * environment variable — and the answer to that is a crash on somebody's settings
 * screen instead of a missing key.
 */
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;

/**
 * What may be used as the agent's name: any printable characters, in any script, up
 * to a length a name can plausibly be.
 *
 * A pattern rather than a length check, because the name is not only displayed — it
 * is interpolated into the first line of the agent's own system prompt, where a
 * newline would end the sentence the prompt opens with and start another. This is
 * the cheapest prompt injection there is: a field labelled "what should I call you"
 * that silently rewrites the identity it is written into. `ethos.config` repeats the
 * rule with the same pattern when it reads the file, so a name that reaches the
 * agent some other way is refused the same way.
 */
const AGENT_NAME = /^[^\x00-\x1f\x7f]{1,64}$/;
const MAX_AGENT_NAME_LENGTH = 64;

/** Nothing named: the state a form offers to leave, rather than one it fills in. */
const NO_IDENTITY = { configured: false, self_name: "" };

function environmentValue(name) {
  if (typeof name !== "string" || !ENV_NAME.test(name)) return "";
  const value = process.env[name];
  return typeof value === "string" ? value.trim() : "";
}

/**
 * Ids offered before anybody has asked a provider for its own list.
 *
 * A short, deliberately unopinionated set: this is what a form falls back to when
 * the network is not there, and a longer list would be a longer list of guesses
 * presented as though it were a catalogue. It is the same table the terminal and
 * the browser use, so all three agree on what the fallback is.
 */
const STATIC_MODELS = {
  openai: ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "o4-mini"],
  anthropic: ["claude-3-5-sonnet-latest", "claude-3-5-haiku-latest", "claude-3-opus-latest"],
  gemini: ["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro"],
  openrouter: ["openai/gpt-4o-mini", "anthropic/claude-3.5-sonnet", "google/gemini-2.0-flash-001"],
  deepseek: ["deepseek-chat", "deepseek-reasoner"],
  mistral: ["mistral-small-latest", "mistral-large-latest", "codestral-latest", "open-mistral-nemo"],
  groq: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "open-mixtral-8x7b-32768", "gemma2-9b-it"],
  xai: ["grok-2-latest", "grok-2-mini", "grok-beta"],
  ollama: ["llama3.2", "llama3.1", "mistral", "qwen2.5"],
  lmstudio: ["qwen2.5-7b-instruct", "llama-3.2-3b-instruct", "gemma-2-2b-it"],
};

function text(value, max) {
  return typeof value === "string" && value.length > 0 && value.length <= max && !CONTROL.test(value)
    ? value
    : "";
}

function baseUrlOf(provider, value) {
  const trimmed = text(value, MAX_ENDPOINT_LENGTH).trim().replace(/\/+$/, "");
  if (trimmed) return trimmed;
  return BASE_PROVIDER_DEFAULTS[provider].baseUrl;
}

/**
 * Refuses an endpoint the agent must not be handed.
 *
 * The agent posts its own reasoning to this address with a credential attached,
 * so the check is about where that traffic can go rather than about whether the
 * string parses: plain `http` only to this machine, and never a private,
 * link-local or unspecified address. `ethos.config.is_valid_base_url` repeats this
 * when the file is read; doing it here as well means a bad address is refused
 * with an answer the person can see, instead of being written and then ignored.
 */
function endpointAllowed(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    return false;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false;
  if (url.username || url.password || url.search || url.hash) return false;
  const host = url.hostname.replace(/^\[|\]$/g, "").replace(/\.$/, "").toLowerCase();
  if (!host) return false;
  const loopback = host === "localhost" || host.endsWith(".localhost") || host === "::1" || /^127\./.test(host);
  const privateHost = loopback
    || host === "local" || host.endsWith(".local") || host.endsWith(".internal")
    || /^10\./.test(host) || /^192\.168\./.test(host) || /^169\.254\./.test(host)
    || /^172\.(1[6-9]|2\d|3[01])\./.test(host) || host === "0.0.0.0";
  if (url.protocol === "http:" && !loopback) return false;
  return loopback || !privateHost;
}

function keyHint(key) {
  return key ? `••••${key.slice(-4)}` : "";
}

/**
 * The vendor's key as the environment holds it, or `null`.
 *
 * Read here rather than in the page, because a browser cannot read a process's
 * environment at all: there is no `process.env` on the other side of a document.
 * The page is therefore told *that* a variable is set and never *what* it says,
 * and asking for a catalogue is what makes this process use it — the same
 * arrangement as a key on file, which is also never handed out.
 *
 * Only this process's own variables count, which is the honest limit of the
 * feature: the key has to have been in the shell that started the bridge, the
 * same way it has to have been in the shell that started the agent.
 */
function envKeyFor(vendor) {
  const definition = BASE_PROVIDERS[vendor];
  if (!definition) return null;
  for (const variable of definition.env) {
    const value = environmentValue(variable);
    if (value) return { variable, value };
  }
  return null;
}

/**
 * Every vendor's environment credential, as a fact rather than as a secret.
 *
 * One entry per provider whether or not it has one, because a form offering the
 * button only for a provider somebody already selected would answer a question
 * the reader cannot ask yet. The value is not here; the last four characters are,
 * because a hint is what lets somebody tell `OPENAI_API_KEY` they set an hour ago
 * from the one they meant to set.
 */
function readEnvKeys() {
  return Object.keys(BASE_PROVIDERS).map((provider) => {
    const found = envKeyFor(provider);
    return {
      provider,
      variables: BASE_PROVIDERS[provider].env,
      present: Boolean(found),
      variable: found?.variable ?? "",
      hint: keyHint(found?.value ?? ""),
    };
  });
}

const NO_BASE_MODEL = {
  configured: false,
  provider: "",
  protocol: "",
  model: "",
  base_url: "",
  key_present: false,
  key_hint: "",
  key_env: "",
  key_env_present: false,
};

/**
 * The base model file as it is on disk, or `null` when there is nothing to read.
 *
 * A file that will not parse is reported as no base model rather than as an
 * error: the agent is already refusing to use one it cannot read, so the honest
 * answer to "what is configured" is "nothing I can see", and the settings screen
 * can then offer to write a good one over it.
 */
function readBaseModelFile() {
  let data;
  try {
    data = loadYaml(fs.readFileSync(BASE_MODEL_FILE, "utf8"));
  } catch {
    return null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  return data;
}

/** The base model as a surface may see it: never the key, only whether there is one. */
function readBaseModel() {
  const data = readBaseModelFile();
  if (!data) return { ...NO_BASE_MODEL };
  const key = typeof data.api_key === "string" ? data.api_key : "";
  const protocol = typeof data.provider === "string" ? data.provider : "";
  // Which variable the file points at, and whether *this* process can read it.
  // Both are needed: the name says what a hand-edited file asked for, and the
  // boolean says whether asking it would actually produce a key — a file written
  // by one shell and read by another is the normal way for the two to disagree.
  const keyEnv = typeof data.api_key_env === "string" ? data.api_key_env : "";
  const envValue = environmentValue(keyEnv);
  return {
    configured: true,
    // The vendor a person chose, which is what a settings form has to put back
    // into a picker. It is reported beside the protocol rather than instead of
    // it, because two vendors share one protocol often enough that a form given
    // only the protocol could not tell which endpoint and which key belong
    // together. A hand-written file with no `vendor` falls back to the protocol.
    provider: typeof data.vendor === "string" && data.vendor ? data.vendor : protocol,
    protocol,
    model: typeof data.model === "string" ? data.model : "",
    base_url: typeof data.base_url === "string" ? data.base_url : "",
    key_present: key.length > 0,
    key_hint: keyHint(key),
    key_env: keyEnv,
    key_env_present: envValue.length > 0,
  };
}

/**
 * Writes the base model, keeping a stored key unless a new one was given.
 *
 * Written to a sibling temporary file and renamed, so the gateway — which may be
 * reading this file at any moment — never sees a half-written document, and at
 * 0600 from the moment it exists, so a key is never briefly readable by other
 * accounts between the write and a following chmod.
 */
function writeBaseModel(body) {
  if (body?.enabled === false) {
    try {
      fs.unlinkSync(BASE_MODEL_FILE);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    return { ...NO_BASE_MODEL };
  }

  const vendor = text(body?.provider, 64).trim();
  if (!Object.prototype.hasOwnProperty.call(BASE_PROVIDERS, vendor)) return { error: "unknown_provider" };
  const model = text(body?.model, 256).trim();
  if (!MODEL_NAME.test(model)) return { error: "invalid_model" };
  const baseUrl = baseUrlOf(vendor, body?.base_url);
  if (!endpointAllowed(baseUrl)) return { error: "invalid_endpoint" };

  // An absent key means "keep the one already stored", so a person can change the
  // model from a page that never held the credential. `clear_key` is how a key is
  // removed, because an empty field cannot be told from one nobody filled in —
  // and it has to come *after* that decision, or the carry-over below would put
  // the key straight back and the button would do nothing.
  const stored = readBaseModelFile();
  const storedKey = stored && typeof stored.api_key === "string" ? stored.api_key : "";
  // A name that is not a name is not carried across: the file keeps whatever
  // somebody wrote by hand, and this write records a reference that can actually
  // be resolved rather than one that only looks like it can.
  const storedEnvRaw = stored && typeof stored.api_key_env === "string" ? stored.api_key_env : "";
  const storedEnv = ENV_NAME.test(storedEnvRaw) ? storedEnvRaw : "";
  // Carrying a key across is only right for the *same* vendor: two vendors share
  // the OpenAI-compatible protocol often enough that comparing protocols would
  // post one company's key to another company's endpoint.
  const sameVendor = Boolean(stored) && stored.vendor === vendor;
  const clearing = body?.clear_key === true;
  // `use_env` is the button on the settings screen: use the variable, and record
  // its *name* rather than its value. The file then says where the key lives,
  // which is what `BaseModelConfig.api_key_env` is for and what the gateway
  // already prefers over a literal — so a deployment whose key is rotated in the
  // environment does not have to be rewritten from a page to pick it up. The key
  // itself never enters this process's memory beyond the one fetch, and never
  // enters the file at all.
  const usingEnv = body?.use_env === true;
  const env = usingEnv ? envKeyFor(vendor) : null;
  if (usingEnv && !env) return { error: "env_key_missing" };
  let apiKey = clearing ? "" : text(body?.api_key, MAX_API_KEY_LENGTH).trim();
  // A key that was just typed takes the variable's name with it. The gateway
  // prefers the environment over the file, so leaving both would record a key
  // that is never used and a variable that is never consulted — a save that
  // reported success and changed nothing.
  let apiKeyEnv = clearing || usingEnv || apiKey ? "" : (sameVendor ? storedEnv : "");
  if (!apiKey && sameVendor && !clearing) apiKey = storedKey;
  if (usingEnv) apiKeyEnv = env.variable;
  if (!apiKey && !apiKeyEnv && BASE_PROVIDERS[vendor].requiresKey) return { error: "key_required" };

  const document = {
    // The vendor is kept alongside the protocol so the file says what a person
    // chose and not only what the adapter is called, which is what makes the
    // carry-over decision above possible to reach at all.
    vendor,
    provider: BASE_PROVIDERS[vendor].protocol,
    model,
    base_url: baseUrl,
    api_key: apiKey,
    // Empty rather than absent, so a file that had one and no longer does does not
    // keep a reference the agent would resolve over the key that replaced it.
    api_key_env: apiKeyEnv,
    tiers: ["T1", "T2", "T3"],
  };
  // A home this process cannot write is its own answer, not an outage. Thrown as
  // a failure it arrives as a 503, which a surface reads as "the agent is not
  // answering" — advice to start something that is already running, sent to
  // somebody whose only problem is a directory. Said as a refusal it names the
  // path, which is the one thing that can be acted on.
  const temporary = `${BASE_MODEL_FILE}.${process.pid}.tmp`;
  try {
    fs.mkdirSync(AGENT_HOME, { recursive: true, mode: 0o700 });
    fs.writeFileSync(temporary, dumpYaml(document), { encoding: "utf8", mode: 0o600 });
    fs.renameSync(temporary, BASE_MODEL_FILE);
    fs.chmodSync(BASE_MODEL_FILE, 0o600);
  } catch (error) {
    try {
      fs.unlinkSync(temporary);
    } catch {
      /* nothing was written */
    }
    // The path goes to the log and not to the page: a surface is shown what to
    // do about it, and the log line is where the directory itself belongs.
    const detail = `${AGENT_HOME}: ${error?.code ?? error?.message ?? error}`;
    console.error(`ethos-web base model not written — ${detail}`);
    return { error: "home_unwritable", detail };
  }
  return { ...readBaseModel(), configured: true };
}

/**
 * The identity file as it is on disk, or `null` when there is nothing to read.
 *
 * A file that will not parse answers as no name rather than as an error, for the same
 * reason `readBaseModelFile` does: the agent is already refusing to use a name it
 * cannot read, so the honest answer to "what is it called" is "nothing I can see",
 * and the settings screen can then offer to write a good one over it.
 */
function readIdentityFile() {
  let data;
  try {
    data = loadYaml(fs.readFileSync(IDENTITY_FILE, "utf8"));
  } catch {
    return null;
  }
  if (!data || typeof data !== "object" || Array.isArray(data)) return null;
  return data;
}

/**
 * What the agent is called, as a surface may see it.
 *
 * `configured` is the honest half of this answer. An empty `self_name` does not mean
 * the agent has no name — it has whatever `config/ethos.yaml` calls it — it means no
 * person has chosen one, which is a different thing, and the one a settings screen has
 * to offer to fix rather than display as a fact.
 */
function readIdentity() {
  const data = readIdentityFile();
  if (!data) return { ...NO_IDENTITY };
  const name = typeof data.self_name === "string" ? data.self_name.trim() : "";
  if (!AGENT_NAME.test(name)) return { ...NO_IDENTITY };
  return { configured: true, self_name: name };
}

/**
 * Writes the name a person chose, or takes it back.
 *
 * `enabled: false` removes the file rather than writing an empty name into it, so
 * "put it back to how it was" is one operation and not a special case inside the
 * document — and so the file never describes a name that is not there.
 *
 * Written to a sibling temporary file and renamed, for the reason the base model is:
 * the agent reads this file when it starts, and a half-written document read at that
 * moment is a name that was never chosen.
 */
function writeIdentity(body) {
  if (body?.enabled === false) {
    try {
      fs.unlinkSync(IDENTITY_FILE);
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    return { ...NO_IDENTITY };
  }

  // Trimmed first, then checked, and the trimmed form is what gets written — the same
  // order `ethos.config.normalise_agent_name` uses, so both sides agree about which
  // names exist and neither can write a name it would then refuse. `text` has already
  // rejected anything carrying a control character, which is the half that matters:
  // the check and the write have to be about the same string, or a name could be
  // refused on one side of the bridge and used on the other.
  const name = text(body?.self_name, MAX_AGENT_NAME_LENGTH).trim();
  if (!AGENT_NAME.test(name)) return { error: "invalid_name" };

  const temporary = `${IDENTITY_FILE}.${process.pid}.tmp`;
  try {
    fs.mkdirSync(AGENT_HOME, { recursive: true, mode: 0o700 });
    fs.writeFileSync(temporary, dumpYaml({ self_name: name }), { encoding: "utf8", mode: 0o600 });
    fs.renameSync(temporary, IDENTITY_FILE);
    fs.chmodSync(IDENTITY_FILE, 0o600);
  } catch (error) {
    try {
      fs.unlinkSync(temporary);
    } catch {
      /* nothing was written */
    }
    const detail = `${AGENT_HOME}: ${error?.code ?? error?.message ?? error}`;
    console.error(`ethos-web agent name not written — ${detail}`);
    return { error: "home_unwritable", detail };
  }
  return readIdentity();
}

/**
 * The vendor's own model list, asked of the vendor.
 *
 * Asked here rather than in the page because this process is the one holding the
 * key: a browser that had to fetch a catalogue itself would have to be given the
 * credential first, and a credential that travels to a page is a credential that
 * can leak from one. So the page asks for a list, and the key stays where it is.
 * It is also the only arrangement that can work at all from a page this process
 * served: its document policy allows a request to its own origin and no further,
 * so a catalogue fetched by the page itself is refused before it leaves.
 *
 * The credential is the typed one when there is one — somebody part-way through
 * configuring a second vendor holds a key the file does not have yet, and a key
 * they just typed is the one they meant to try — then the stored one, for the
 * same vendor only, exactly as on a write, and then the environment's, which is
 * the same order the gateway itself resolves a key in.
 *
 * `key_source` travels back with the answer so a form can say which of the three
 * was used. It names a source, never a value.
 *
 * Every detail of the request matches the one interface's own provider client makes,
 * because it has to: the auth header and the shape of the answer are per-vendor,
 * and two implementations of "list a catalogue" that disagree is one more thing
 * to be wrong. Anything that goes wrong answers with the fallback list and a
 * reason, never with an exception: a form with nothing to show is recoverable,
 * a form that threw is not.
 */
async function listModels(body) {
  const vendor = text(body?.provider, 64).trim();
  if (!Object.prototype.hasOwnProperty.call(BASE_PROVIDERS, vendor)) {
    return { models: [], source: "offline", error: "unknown_provider", key_source: "none" };
  }
  const stored = readBaseModelFile();
  const storedKey = stored && typeof stored.api_key === "string" ? stored.api_key : "";
  const sameVendor = Boolean(stored) && stored.vendor === vendor;
  const typed = text(body?.api_key, MAX_API_KEY_LENGTH).trim();
  // The variable the stored file points at, which for a base model saved with
  // the environment button is the only credential there is. It is asked *before*
  // the file's own key because the gateway asks it first too — `ModelRouter`
  // reads `api_key_env` and falls back to the literal — and a catalogue fetched
  // with a different credential than the agent will use is a catalogue about a
  // model this installation cannot reach.
  const storedEnvName = sameVendor && stored && typeof stored.api_key_env === "string" ? stored.api_key_env : "";
  // Only a vendor that wants a key looks for one: a local server is sent no
  // credential whatever the environment holds, so reporting a source for it would
  // be reporting a decision that is not made.
  const env = BASE_PROVIDERS[vendor].requiresKey ? envKeyFor(vendor) : null;
  let apiKey = typed;
  let keySource = typed ? "typed" : "none";
  const fromEnv = (variable, value) => {
    if (apiKey) return;
    apiKey = value;
    keySource = "environment";
  };
  if (storedEnvName) fromEnv(storedEnvName, environmentValue(storedEnvName));
  if (!apiKey && sameVendor && storedKey) {
    apiKey = storedKey;
    keySource = "stored";
  }
  // A vendor being configured for the first time has no file, so the variable its
  // own table names is what there is — the same default a catalogue entry in
  // `models.yaml` would have been reading all along.
  if (env) fromEnv(env.variable, env.value);
  if (!apiKey && BASE_PROVIDERS[vendor].requiresKey) {
    return { models: STATIC_MODELS[vendor], source: "offline", error: "missing_credentials", key_source: "none" };
  }
  const baseUrl = baseUrlOf(vendor, body?.base_url);
  if (!endpointAllowed(baseUrl)) {
    return { models: STATIC_MODELS[vendor], source: "offline", error: "invalid_endpoint", key_source: keySource };
  }

  const headers = { Accept: "application/json" };
  // Keyed on `requiresKey`, not on whether a key happens to be present: a local
  // server is sent no credential at all, because a local server has no use for
  // one and some of them answer an unexpected `Authorization` with a 401.
  if (apiKey && BASE_PROVIDERS[vendor].requiresKey) {
    if (BASE_PROVIDERS[vendor].auth === "x-api-key") {
      headers["x-api-key"] = apiKey;
      headers["anthropic-version"] = ANTHROPIC_VERSION;
    } else if (BASE_PROVIDERS[vendor].auth === "x-goog-api-key") {
      headers["x-goog-api-key"] = apiKey;
    } else {
      headers.Authorization = `Bearer ${apiKey}`;
    }
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CATALOGUE_TIMEOUT_MS);
  try {
    const response = await fetch(`${baseUrl}/models`, {
      method: "GET",
      headers,
      // The three settings that keep this from becoming a way to be walked onto
      // another origin with the key attached: no cookies, no referrer, and
      // redirects surfaced rather than followed.
      cache: "no-store",
      credentials: "omit",
      redirect: "manual",
      referrerPolicy: "no-referrer",
      signal: controller.signal,
    });
    if (response.status >= 300 && response.status < 400) {
      return { models: STATIC_MODELS[vendor], source: "offline", error: "redirect_refused", key_source: keySource };
    }
    if (!response.ok) {
      return { models: STATIC_MODELS[vendor], source: "offline", error: `http_${response.status}`, key_source: keySource };
    }
    const models = extractModelIds(await response.json(), vendor);
    if (models.length === 0) {
      return { models: STATIC_MODELS[vendor], source: "offline", error: "empty_catalog", key_source: keySource };
    }
    return { models, source: "remote", key_source: keySource };
  } catch (error) {
    return {
      models: STATIC_MODELS[vendor],
      source: "offline",
      error: error?.name === "AbortError" ? "timeout" : "network_unavailable",
      key_source: keySource,
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * The ids out of whichever shape this vendor answers in.
 *
 * Gemini names its models `models/<id>` and wraps them under a different key from
 * everyone else; everyone else uses `data[].id`. Ids go through the same pattern
 * the agent's own reader applies, because an id that would be refused there is
 * worse than no id at all: it would be offered here, chosen, and then ignored.
 */
function extractModelIds(payload, vendor) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return [];
  const items = vendor === "gemini"
    ? (Array.isArray(payload.models) ? payload.models : [])
    : (Array.isArray(payload.data) ? payload.data : []);
  const seen = new Set();
  for (const item of items.slice(0, MAX_CATALOGUE_ENTRIES)) {
    if (!item || typeof item !== "object") continue;
    const raw = vendor === "gemini" ? item.name : item.id;
    if (typeof raw !== "string") continue;
    const id = (vendor === "gemini" ? raw.replace(/^models\//, "") : raw).trim();
    if (MODEL_NAME.test(id)) seen.add(id);
  }
  return [...seen].sort();
}

/**
 * A configuration file as it is on disk, and whether it could be read at all.
 *
 * The two answers are separate because the read path and the write path want
 * different things from a broken file, and agreeing about them is what loses a
 * person's settings. Reading is forgiving — an unreadable file is reported as no
 * overrides, so the shipped configuration stands and every door is visibly off,
 * which a person can see on the settings screen and fix. Writing must not be
 * forgiving in the same direction, because a save is built from what could be
 * read and then replaces the whole file: an unreadable file read as an empty one
 * turns one save into a deletion of every other door. So `ok` says whether
 * whatever is on disk is a document this code can read back and write out again,
 * and a file that is missing or empty is `ok` — there is nothing there to lose,
 * and refusing every save until somebody deleted the file by hand would be worse
 * than writing over nothing.
 */
function parseYamlFile(file) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch {
    return { ok: true, data: {} };
  }
  if (!raw.trim()) return { ok: true, data: {} };
  let data;
  try {
    data = loadYaml(raw);
  } catch {
    return { ok: false, data: {} };
  }
  if (data === null || data === undefined) return { ok: true, data: {} };
  // A scalar or a list at the top level is not a document this can write back,
  // and it is a thing somebody put there on purpose.
  if (typeof data !== "object" || Array.isArray(data)) return { ok: false, data: {} };
  return { ok: true, data };
}

/**
 * A configuration file, or `{}` when it is not there or will not parse.
 *
 * Forgiving on purpose, and for the reason `readBaseModelFile` is: these files
 * are written by an interface at the moment somebody presses save, and a
 * half-written or hand-edited one is a normal thing to find. A file that will not
 * read is reported as absent rather than as a reason to serve nothing, so the
 * shipped configuration is what stands and every door is off — a state a person
 * can see on the settings screen — instead of a bridge that answers errors.
 */
function readYamlFile(file) {
  return parseYamlFile(file).data;
}

/** One section of configuration, with the agent's home saying what it says on top. */
function mergeSection(base, overlay) {
  const merged = { ...base };
  for (const [key, value] of Object.entries(overlay)) {
    const current = merged[key];
    merged[key] = current && typeof current === "object" && !Array.isArray(current)
      && value && typeof value === "object" && !Array.isArray(value)
      ? mergeSection(current, value)
      : value;
  }
  return merged;
}

/**
 * Every door, as this deployment has it: the shipped file with the home's on top.
 *
 * Merged key by key rather than replaced, and that is the point. A replacement
 * would require the home file to carry every door to be right, so writing one
 * Telegram token into it would silently switch off WhatsApp, Slack, Discord and
 * the webhook receiver. `ethos.config.load_config` merges the same two files the
 * same way, so what a settings screen writes and what the agent runs cannot
 * disagree about what this machine is configured to do.
 */
function readChannelsFile() {
  return mergeSection(readYamlFile(path.join(CONFIG_DIR, "channels.yaml")), readYamlFile(CHANNELS_FILE));
}

/** The contact book, with the addresses written in the agent's home on top. */
function readPeopleFile() {
  const overlay = readYamlFile(PEOPLE_FILE);
  if (!Array.isArray(overlay.people) || overlay.people.length === 0) {
    return readYamlFile(path.join(CONFIG_DIR, "people.yaml"));
  }
  const merged = readYamlFile(path.join(CONFIG_DIR, "people.yaml"));
  const people = [...(Array.isArray(merged.people) ? merged.people : [])];
  for (const incoming of overlay.people) {
    if (!incoming || typeof incoming !== "object") continue;
    const id = typeof incoming.id === "string" ? incoming.id.trim() : "";
    if (!id) continue;
    const existing = people.find((person) => person && person.id === id);
    if (!existing) {
      people.push({ ...incoming, id });
      continue;
    }
    // A `null` address is a removal, which is how a door is given up: the
    // alternative would be an empty string, which the contact book reads as "not
    // an address" anyway, so the two would be indistinguishable.
    const addresses = { ...(existing.channels ?? {}) };
    for (const [door, address] of Object.entries(incoming.channels ?? {})) {
      if (address === null) delete addresses[door];
      else if (typeof address === "string" && address.trim()) addresses[door] = address.trim();
    }
    Object.assign(existing, { ...incoming, channels: addresses });
  }
  return { ...merged, people };
}

/**
 * The doors a person can turn on, and what each one needs before it can be.
 *
 * One table, for the same reason `DOOR_CREDENTIALS` is one table in
 * `ethos/config.py`: three readers — the comms host that skips a door it cannot
 * build, `doctor` that says why, and this form — would otherwise each carry their
 * own copy of the same list and disagree. The credential keys are the fields on
 * the channel's own configuration, so there is no name here to keep in step.
 *
 * `env` is the field naming the variable that credential may instead live in, and
 * `prefix` is what an address on that door looks like — the same prefixes the
 * adapters refuse to send to anything else, checked here so a form says "that is
 * not a Telegram address" instead of a send that is refused at the last step.
 */
const DOOR_SETUP = [
  {
    id: "telegram",
    allowlist: "allowed_chat_ids",
    allowKind: "chat",
    prefix: "tg:",
    credentials: [{ key: "token", env: "token_env", label: "Bot token" }],
  },
  {
    id: "whatsapp",
    allowlist: "allowed_phone_numbers",
    allowKind: "phone",
    prefix: "wa:",
    needsWebhook: true,
    credentials: [
      { key: "phone_number_id", env: "phone_number_id_env", label: "Phone number id" },
      { key: "access_token", env: "access_token_env", label: "Access token" },
      { key: "app_secret", env: "app_secret_env", label: "App secret" },
      { key: "verify_token", env: "verify_token_env", label: "Verify token" },
    ],
  },
  {
    id: "slack",
    allowlist: "allowed_ids",
    allowKind: "id",
    prefix: "slack:",
    needsWebhook: true,
    credentials: [
      { key: "bot_token", env: "bot_token_env", label: "Bot token (xoxb-…)" },
      { key: "signing_secret", env: "signing_secret_env", label: "Signing secret" },
    ],
  },
  {
    id: "discord",
    allowlist: "allowed_ids",
    allowKind: "id",
    prefix: "dc:",
    credentials: [{ key: "token", env: "token_env", label: "Bot token" }],
  },
  {
    id: "email",
    allowlist: null,
    allowKind: null,
    prefix: "email:",
    credentials: [
      { key: "address", env: "address_env", label: "Address" },
      { key: "password", env: "password_env", label: "Password or app password" },
      { key: "imap_host", env: "imap_host_env", label: "IMAP host" },
      { key: "smtp_host", env: "smtp_host_env", label: "SMTP host" },
    ],
  },
];

/**
 * The doors as a form may see them: never a credential, only where one is.
 *
 * Both answers are needed per credential, and they are different answers. `env`
 * is the name a hand-edited file asked for, and `env_present` is whether *this*
 * process can actually read it — which is the normal way for the bridge and the
 * comms process to disagree, because a file written by one shell is read by
 * another. `present` and `hint` are the stored value, which is never handed out;
 * a hint is enough to tell the token typed an hour ago from the one just pasted.
 *
 * The allowlist and the address are reported because both decide whether a door
 * can be used rather than merely opened: an empty allowlist admits nobody, and a
 * door with no address for this person cannot be answered on.
 *
 * `acceptFromAnyone` is reported beside them because it is the third way to answer
 * no one, and the only one of the three that is a setting rather than a list
 * somebody forgot to fill in. A form that showed "this door admits nobody" without
 * it would be wrong for a deployment that has deliberately turned this on, and a
 * form that let a person turn it on without saying so would be how a bot token
 * becomes a published endpoint.
 */
function readChannelSetup(person = "owner") {
  const channels = readChannelsFile();
  const book = readAddressBook();
  const doors = DOOR_SETUP.map((door) => {
    const config = channels[door.id];
    const present = config && typeof config === "object" ? config : {};
    const allowed = Array.isArray(present[door.allowlist]) ? present[door.allowlist] : [];
    const openToAll = present.accept_from_anyone === true;
    return {
      id: door.id,
      enabled: present.enabled === true,
      credentials: door.credentials.map(({ key, env, label }) => {
        const value = typeof present[key] === "string" ? present[key].trim() : "";
        const variable = ENV_NAME.test(String(present[env] ?? "")) ? present[env] : "";
        return {
          key,
          label,
          env: variable,
          present: value.length > 0,
          hint: keyHint(value),
          env_present: environmentValue(variable).length > 0,
        };
      }),
      allowlist: door.allowlist,
      allowed: allowed.map((value) => String(value)),
      accept_from_anyone: openToAll,
      // An empty list with the door open to all is still a door that answers
      // people, so `admits` reads the two together rather than the list alone.
      admits: openToAll || (door.allowlist ? allowed.length > 0 : true),
      prefix: door.prefix,
      address: book.get(person)?.get(door.id) ?? "",
      needs_webhook: door.needsWebhook === true,
      // The path a push channel is declared at, which is the one thing a person
      // setting WhatsApp or Slack up cannot work out from this machine: the app
      // is told a URL somewhere else, and the URL is the tunnel's.
      path: typeof present.path === "string" ? present.path : "",
      // Whether this door is on at all is a fact, and one that costs the reader
      // a guess otherwise: a push door with every credential and no receiver is
      // open, complete and deaf.
      webhook_on: channels.webhook?.enabled === true,
    };
  });
  const receiver = channels.webhook && typeof channels.webhook === "object" ? channels.webhook : {};
  return {
    person,
    file: CHANNELS_FILE,
    doors: [...doors, {
      // The shared receiver, as a door of its own because turning one on is part
      // of turning WhatsApp or Slack on: with no listener there is nowhere for a
      // POST to land, and the channel looks configured and receives nothing.
      id: "webhook",
      enabled: receiver.enabled === true,
      credentials: [],
      allowlist: null,
      allowed: [],
      accept_from_anyone: false,
      admits: true,
      prefix: "",
      address: "",
      needs_webhook: false,
      webhook_on: true,
      host: typeof receiver.host === "string" ? receiver.host : "127.0.0.1",
      port: Number.isInteger(receiver.port) ? receiver.port : 8730,
    }],
  };
}

/**
 * Writes a door's settings into the agent's home, or refuses to.
 *
 * Everything the request does not mention is left exactly as it is, because a form
 * saving one field must not clear the two beside it — and a credential that is
 * absent and a credential that is being removed are different instructions, so
 * `null` means remove and a string means set.
 *
 * Two files, written to a sibling temporary and renamed, at 0600 from the moment
 * they exist: the channel document and the contact-book address. Both are read by
 * the agent when `ethos-comms` next starts, so a half-written one would be a door
 * that is half configured.
 */
function writeChannelSetup(body) {
  const id = text(body?.id, 64).trim();
  if (id === "webhook") {
    if (body?.enabled !== undefined && typeof body.enabled !== "boolean") return { error: "invalid_value" };
    // The receiver is a listener, not a door: it posts to nothing and answers to
    // nobody, so it has no allowlist, no address and no "answer anyone" setting.
    // Accepting one and ignoring it would answer 200 to a save that changed
    // nothing, which is the state that teaches a form to report success while the
    // thing they asked for never happened. Refused with the same code a stranger
    // field on any other door gets.
    for (const key of ["credentials", "allowed", "address", "accept_from_anyone"]) {
      if (body?.[key] !== undefined) return { error: "unknown_field" };
    }
    const written = writeHomeDocument(CHANNELS_FILE, (document) => {
      document.webhook = { ...(document.webhook ?? {}), enabled: body.enabled === true };
      return document;
    }, "channel doors");
    if (written?.error) return written;
    return { ...readChannelSetup(), applies_without_restart: true };
  }

  const door = DOOR_SETUP.find((entry) => entry.id === id);
  if (!door) return { error: "unknown_channel" };
  if (body?.enabled !== undefined && typeof body.enabled !== "boolean") return { error: "invalid_value" };

  const credentials = {};
  for (const [key, value] of Object.entries(body?.credentials ?? {})) {
    if (!door.credentials.some((entry) => entry.key === key)) return { error: "unknown_field" };
    if (value === null) {
      credentials[key] = null;
      continue;
    }
    const secret = text(value, MAX_CREDENTIAL_LENGTH).trim();
    // An empty string is not a credential, and writing one would leave a key that
    // reads as present to this process and as missing to the agent.
    if (secret) credentials[key] = secret;
  }

  let allowed;
  if (body?.allowed !== undefined) {
    allowed = [];
    for (const value of Array.isArray(body.allowed) ? body.allowed : []) {
      const entry = text(value, 128).trim();
      if (!entry) continue;
      if (!allowedIdOf(door, entry)) return { error: "invalid_allowed_id" };
      allowed.push(allowedIdOf(door, entry));
    }
  }

  // Only a boolean, and only a whole key is written — a form saving one field must
  // not clear the one beside it, so a request that does not mention this at all
  // leaves whatever is on file. Refused rather than coerced for the same reason
  // `enabled` is: "yes please" arriving as the string `"yes"` and being stored as
  // `true` is a door opened by a form that thought it was writing something else.
  let acceptFromAnyone;
  if (body?.accept_from_anyone !== undefined) {
    if (typeof body.accept_from_anyone !== "boolean") return { error: "invalid_value" };
    acceptFromAnyone = body.accept_from_anyone;
  }

  let address;
  if (body?.address !== undefined) {
    if (body.address === null) {
      address = null;
    } else {
      const wanted = text(body.address, 128).trim();
      if (!wanted.startsWith(door.prefix) || wanted.length === door.prefix.length) {
        return { error: "invalid_address" };
      }
      address = wanted;
    }
  }

  const person = text(body?.person, 64).trim() || "owner";
  // Both files this save touches are checked before either is written, because a
  // door and the address it is reachable at are one decision written in two places.
  // Refusing after the first would leave a door open that nothing can be sent to,
  // and answer an error to a form whose first half did land.
  for (const file of [CHANNELS_FILE, ...(address !== undefined ? [PEOPLE_FILE] : [])]) {
    if (parseYamlFile(file).ok) continue;
    const detail = `${file} is not a readable settings document; nothing was written to it`;
    console.error(`ethos-web channel doors not written — ${detail}`);
    return { error: "settings_unreadable", detail };
  }
  const written = writeHomeDocument(CHANNELS_FILE, (document) => {
    const target = { ...(document[id] ?? {}) };
    if (body?.enabled !== undefined) target.enabled = body.enabled;
    for (const [key, value] of Object.entries(credentials)) {
      if (value === null) delete target[key];
      else target[key] = value;
    }
    if (allowed !== undefined) target[door.allowlist] = allowed;
    if (acceptFromAnyone !== undefined) target.accept_from_anyone = acceptFromAnyone;
    document[id] = target;
    // A push door with no listener has nowhere for a POST to land, so turning
    // one on turns the receiver on with it: same document, same write, and
    // nowhere for the two halves to disagree. Only ever on — a save that
    // configures Slack must not switch off a webhook WhatsApp is answering on,
    // and a person who wants it off turns it off here on purpose.
    if (door.needsWebhook && body?.enabled === true) {
      document.webhook = { ...(document.webhook ?? {}), enabled: true };
    }
    return document;
  }, "channel doors");
  if (written?.error) return written;

  if (address !== undefined) {
    const saved = writeHomeDocument(PEOPLE_FILE, (document) => {
      const people = Array.isArray(document.people) ? [...document.people] : [];
      const existing = people.find((entry) => entry && entry.id === person);
      // The instruction is recorded rather than resolved: an address the shipped
      // contact book has and this file has never heard of is still removed by
      // `null` here, which is the only way to give a door up without editing a
      // committed file to take the address out of it.
      const channels = { ...(existing?.channels ?? {}), [id]: address ?? null };
      if (existing) Object.assign(existing, { channels });
      else people.push({ id: person, channels });
      return { ...document, people };
    }, "the contact book");
    if (saved?.error) return saved;
  }
  return { ...readChannelSetup(person), applies_without_restart: true };
}

/**
 * An allowlist entry as this door stores it, or `null` when it is not one.
 *
 * Checked per door because the four are four different namespaces: a chat id is
 * a number, a phone number is digits without a `+`, and a Slack or Discord id is
 * a short token. A list that accepted any of them in any door would produce a
 * door that looks configured and admits nobody — the bot answers `/id` and
 * nothing else, which is the most misleading state a channel can be in.
 */
function allowedIdOf(door, value) {
  if (door.allowKind === "chat") {
    return /^-?\d{1,20}$/.test(value) ? Number(value) : null;
  }
  if (door.allowKind === "phone") {
    return /^\d{5,20}$/.test(value) ? value : null;
  }
  if (door.allowKind === "id") {
    return /^[A-Za-z0-9_-]{2,64}$/.test(value) ? value : null;
  }
  return value || null;
}

/**
 * Writes one document in the agent's home, atomically and privately.
 *
 * The same arrangement as the base model and the name, and the same reason: a
 * bot token in a file other accounts can read is not a credential, and a file the
 * comms process may read while it is being written is a door that is half
 * configured. A home this process cannot write is its own answer rather than an
 * outage — thrown as a failure it arrives as a 503, which a surface reads as "the
 * agent is not answering", which is advice to start something already running.
 *
 * The file on disk is left exactly as it is unless this can read it, and that is
 * the whole of the safety here. The document is the file plus one change, so a
 * file that parsed as nothing is a file whose every other door has just been
 * deleted by a form that was only ever asked to change one of them. Refusing is
 * an answer a person can act on — the file is named in the refusal and is still
 * there to be read — so the save is refused rather than the credentials replaced.
 */
function writeHomeDocument(file, change, what) {
  const stored = parseYamlFile(file);
  if (!stored.ok) {
    const detail = `${file} is not a readable settings document; nothing was written to it`;
    console.error(`ethos-web ${what} not written — ${detail}`);
    return { error: "settings_unreadable", detail };
  }
  const document = change({ ...stored.data });
  const temporary = `${file}.${process.pid}.tmp`;
  try {
    fs.mkdirSync(AGENT_HOME, { recursive: true, mode: 0o700 });
    fs.writeFileSync(temporary, dumpYaml(document), { encoding: "utf8", mode: 0o600 });
    fs.renameSync(temporary, file);
    fs.chmodSync(file, 0o600);
  } catch (error) {
    try {
      fs.unlinkSync(temporary);
    } catch {
      /* nothing was written */
    }
    const detail = `${AGENT_HOME}: ${error?.code ?? error?.message ?? error}`;
    console.error(`ethos-web ${what} not written — ${detail}`);
    return { error: "home_unwritable", detail };
  }
  return { ok: true };
}

/**
 * Who the agent knows, and where each of them can be reached.
 *
 * `config/people.yaml` is the contact book the agent seeds its `people` table
 * from, and each entry's `channels` maps a door to the address that door knows
 * the person by: `{web: "owner", telegram: "tg:819012345678"}`. The agent's own
 * home overlays it, which is where an interface writes an address — see
 * `readPeopleFile`.
 *
 * This is read here rather than from the table because the same two answers have
 * to be available when the database is down — a bridge that can only tell a
 * surface which doors are open while Postgres is unreachable is a bridge that
 * reports the doors as unusable at exactly the moment somebody is trying to work
 * out why they are unusable. It is also the only place the mapping exists in a
 * form a surface can use: `people.channels` is a column nothing else reads.
 */
function readAddressBook() {
  const book = new Map();
  for (const raw of readPeopleFile().people ?? []) {
    if (!raw || typeof raw !== "object") continue;
    const id = typeof raw.id === "string" ? raw.id.trim() : "";
    if (!id) continue;
    const addresses = new Map();
    const channels = raw.channels;
    if (channels && typeof channels === "object" && !Array.isArray(channels)) {
      for (const [channel, address] of Object.entries(channels)) {
        const name = typeof channel === "string" ? channel.trim() : "";
        const value = typeof address === "string" ? address.trim() : "";
        if (name && value) addresses.set(name, value);
      }
    }
    book.set(id, addresses);
  }
  return book;
}

/**
 * The address a channel knows one person by, or `null` when the channel cannot
 * reach them.
 *
 * Two spellings of one person is the thing this whole lookup exists to prevent.
 * The adapters address people in their own namespaces — `TelegramAdapter.send`
 * refuses anything that is not `tg:<chat id>` and `WhatsappAdapter.send` refuses
 * anything that is not `wa:<number>` — so a message recorded as coming from
 * `owner` on the Telegram door is a row the Telegram adapter cannot answer: the
 * conversation is in the transcript, the deliberation happens, and the reply is
 * refused at the last step with a person waiting for it. Reading the address out
 * of the contact book instead is what makes "send this from the browser through
 * Telegram" mean the same thing as "this person typed it on their phone".
 *
 * `null` is a real answer and not a fallback: a door with no address for this
 * person has no destination, and writing the row anyway is what fabricates a
 * conversation.
 */
function resolveAddress(book, person, channel) {
  const addresses = book.get(person);
  const address = addresses?.get(channel);
  if (typeof address === "string" && address) return address;
  return null;
}

/**
 * Which doors the agent has open, read from the same file the comms process reads.
 *
 * Reported rather than guessed, and only the facts a surface can act on: whether
 * the door is enabled, whether it will admit anybody at all, and whom it can
 * reach. The second one is the reason this route exists — a Telegram channel that
 * is on but whose allowlist is empty answers no one, and a filter offering it as
 * a place to talk would send the person's messages into silence. The third is
 * what makes the second actionable: a surface that knows a door is open still
 * cannot use it without the address it has to send to, and that address lives in
 * the contact book, not in the transcript. Whether the credentials are present is
 * deliberately not reported: this process cannot know, and answering "probably"
 * would be worse than saying nothing.
 *
 * Total on an unreadable file: it lists `web` and nothing else, because the web
 * channel is the one door whose being open is a property of this process.
 */
function readChannels(book = readAddressBook()) {
  const contactsOn = (channel) => [...book.entries()]
    .map(([person, addresses]) => ({ person, address: addresses.get(channel) }))
    .filter((contact) => contact.address);
  // The local line's own contact is the person id, because that is what a
  // transcript is addressed by — so a deployment with no contact book configured
  // still has exactly one person it can talk to here, which is the case every
  // other answer below is built on top of.
  const local = contactsOn("web").length > 0 ? contactsOn("web") : [{ person: "owner", address: "owner" }];
  const list = [{ id: "web", enabled: true, admits: true, contacts: local }];
  try {
    const channels = readChannelsFile();
    if (!channels || typeof channels !== "object") return list;

    // Every door that admits by an allowlist, reported the same way: on, and
    // whether the list is empty. Written as a loop over the one shape they share
    // rather than five copies of the same four lines, because a fifth channel
    // added by copy-paste is a fifth channel that quietly forgets one of the two
    // facts a surface needs.
    const allowlisted = [
      { id: "telegram", config: channels.telegram, list: "allowed_chat_ids" },
      { id: "whatsapp", config: channels.whatsapp, list: "allowed_phone_numbers" },
      { id: "slack", config: channels.slack, list: "allowed_ids" },
      { id: "discord", config: channels.discord, list: "allowed_ids" },
    ];
    for (const door of allowlisted) {
      if (!door.config || typeof door.config !== "object" || door.config.enabled !== true) continue;
      const allowed = Array.isArray(door.config[door.list]) ? door.config[door.list] : [];
      list.push({
        id: door.id,
        enabled: true,
        // The two ways to be a door that answers nobody, and they have to be read
        // together rather than one or the other: an empty allowlist admits nobody,
        // and so does a non-empty one with `accept_from_anyone: false` when the
        // person on the other end is not on it. Reporting the list alone would
        // call a door "usable" that refuses every message sent to it, which is how
        // a composer offers a channel and the agent never answers.
        admits: door.config.accept_from_anyone === true || allowed.length > 0,
        contacts: contactsOn(door.id),
      });
    }

    // The terminal socket is the one door whose being open is a property of a
    // config file and not of this process, so it is read rather than assumed —
    // and it is reported, because `MESSAGE_CHANNELS` has always accepted it and
    // a door a surface cannot name in a message is a door it cannot filter the
    // transcript by. Like `web` it needs no address in the contact book: it
    // delivers to whoever is reading.
    const cli = channels.cli;
    if (cli && typeof cli === "object" && cli.enabled === true) {
      const terminal = contactsOn("cli");
      list.push({
        id: "cli",
        enabled: true,
        admits: true,
        contacts: terminal.length > 0 ? terminal : local,
      });
    }

    // The email door has no allowlist to report: a mailbox admits whoever
    // writes to it, so `admits` is the same fact as `enabled` here, and the
    // address a reply goes to is the one in the contact book.
    const email = channels.email;
    if (email && typeof email === "object" && email.enabled === true) {
      list.push({ id: "email", enabled: true, admits: true, contacts: contactsOn("email") });
    }
  } catch {
    return list;
  }
  return list;
}

/**
 * The share of the daily cap the gateway reserves for committed work.
 *
 * Read from `gateway.budget.commitment_reserve_pct` in `config/ethos.yaml` —
 * the same key `ethos/gateway/cost.py` splits the cap by — so a dashboard
 * retuning the reserve does not quietly disagree with the gate that enforces
 * it. The default matches the file's, not the code's, so an unreadable config
 * and an untouched one answer the same.
 */
function commitmentReserveShare() {
  try {
    const raw = fs.readFileSync(path.join(CONFIG_DIR, "ethos.yaml"), "utf8");
    const share = loadYaml(raw)?.gateway?.budget?.commitment_reserve_pct;
    if (typeof share === "number" && Number.isFinite(share) && share >= 0 && share <= 1) return share;
  } catch {
    /* no config to read; the default below is the file's default */
  }
  return 0.5;
}

async function budgetCaps() {
  const none = { daily_usd: 0, monthly_usd: 0, commitment_usd: 0, discretionary_usd: 0 };
  try {
    const raw = await fs.promises.readFile(path.join(CONFIG_DIR, "permissions.yaml"), "utf8");
    const limits = loadYaml(raw)?.hard_limits;
    if (!limits || typeof limits !== "object") return none;
    const number = (value) => (typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0);
    const daily = number(limits.H2_daily_spend_usd);
    const commitment = daily * commitmentReserveShare();
    return {
      daily_usd: daily,
      monthly_usd: number(limits.H2_monthly_spend_usd),
      commitment_usd: commitment,
      discretionary_usd: daily - commitment,
    };
  } catch {
    return none;
  }
}

/**
 * How many models the agent's own catalogue holds.
 *
 * Read from the same file `ethos/gateway/service.py` routes over, so the number
 * on the screen is the number the router has. Zero when the file cannot be read:
 * a count is a thing to be right about, and a guessed one would put a number in
 * front of somebody that no process is using.
 */
function catalogueSize() {
  try {
    const models = loadYaml(fs.readFileSync(path.join(CONFIG_DIR, "models.yaml"), "utf8"))?.models;
    return Array.isArray(models) ? models.length : 0;
  } catch {
    return 0;
  }
}

async function controlState() {  const row = await pool.query(
    "SELECT attrs FROM env_entities WHERE kind = 'control' AND key = 'lifecycle'",
  );
  const attrs = row.rows[0]?.attrs ?? {};
  return {
    paused: Boolean(attrs.paused),
    pause_actions: Boolean(attrs.pause_actions),
    stopped: Boolean(attrs.stopped),
    emergency: Boolean(attrs.emergency),
    preview: Boolean(attrs.preview),
  };
}

/**
 * The whole agent, as one payload.
 *
 * One round trip on purpose: a surface that refetched everything on every event
 * turned a single thought into fifteen queries, and an agent that thinks several
 * times a minute made that the dominant cost of watching it.
 */
async function readView() {
  const [
    latestCycle,
    recentCycles,
    lastPresence,
    thoughts,
    intentions,
    messages,
    actions,
    byBucket,
    byModel,
    daily,
    trash,
    snapshots,
    integrity,
    auditHead,
    protectedCount,
    caps,
  ] = await Promise.all([
    pool.query("SELECT state, focus, ts FROM cycles ORDER BY id DESC LIMIT 1"),
    pool.query("SELECT state, ts, tier FROM cycles ORDER BY id DESC LIMIT 20"),
    pool.query("SELECT ts FROM events WHERE kind = 'presence.update' ORDER BY ts DESC LIMIT 1"),
    pool.query(
      "SELECT id::text AS id, ts, summary FROM episodes WHERE kind = 'thought' ORDER BY ts DESC LIMIT 50",
    ),
    pool.query(
      "SELECT id::text AS id, parent_id::text AS parent_id, kind, title, desired_end_state, status, priority, deadline, commissioned_by, budget_usd, spent_usd, origin FROM intentions ORDER BY priority DESC, created_at ASC",
    ),
    pool.query(
      "SELECT id::text AS id, ts, direction, channel, person_id, sender_name, sender_username, text, delivery FROM messages ORDER BY ts ASC LIMIT 100 OFFSET (SELECT GREATEST(0, COUNT(*) - 100) FROM messages)",
    ),
    pool.query(
      "SELECT id, ts_start, ts_end, thread_id, tool, args_redacted, reason, status, result_digest, undo_ref FROM action_journal ORDER BY ts_start DESC LIMIT 50",
    ),
    pool.query(
      "SELECT budget_bucket AS bucket, COALESCE(SUM(cost_usd), 0) AS total FROM model_calls WHERE ts >= current_date GROUP BY budget_bucket",
    ),
    pool.query(
      "SELECT model, COALESCE(SUM(cost_usd), 0) AS total FROM model_calls WHERE ts >= current_date GROUP BY model ORDER BY total DESC LIMIT 8",
    ),
    pool.query(
      "SELECT to_char(date_trunc('day', ts), 'YYYY-MM-DD') AS day, COALESCE(SUM(cost_usd), 0) AS total FROM model_calls WHERE ts >= current_date - 6 GROUP BY 1 ORDER BY 1",
    ),
    pool.query("SELECT attrs FROM env_entities WHERE kind = 'trash' ORDER BY key LIMIT 100"),
    pool.query("SELECT attrs FROM env_entities WHERE kind = 'snapshot' ORDER BY key DESC LIMIT 20"),
    pool.query("SELECT attrs FROM env_entities WHERE kind = 'integrity' AND key = 'report'"),
    pool.query(
      "SELECT seq, ts, summary, encode(hash, 'hex') AS hash FROM audit_log ORDER BY seq DESC LIMIT 1",
    ),
    pool.query(
      "SELECT COUNT(*) AS n FROM artifacts WHERE protection IN ('commissioned','pinned')",
    ),
    budgetCaps(),
  ]);
  const cycle = latestCycle.rows[0] ?? null;
  // When the agent was last *heard*, not when it last finished a cycle.
  //
  // A cycle is only recorded when it completes, so an agent whose every cycle is
  // failing — an expired key, a provider with nothing eligible for the tier —
  // writes no cycle rows at all. A surface that dated the agent by the last one
  // then read that agent as gone, and said so with the sentence that sends a
  // person to start it: advice for a machine that is running and stuck, which is
  // the one situation where starting it again changes nothing. The life loop
  // publishes `presence.update` after a failed cycle as well as a good one, and
  // that is the same row the event stream carries, so a surface reading this
  // snapshot and a surface listening on the socket now agree on one definition of
  // alive.
  const heardAt = [cycle?.ts, lastPresence.rows[0]?.ts]
    .filter((value) => value instanceof Date)
    .sort((a, b) => b - a)[0] ?? null;
  let focus = null;
  if (cycle?.focus) {
    try {
      focus = typeof cycle.focus === "string" ? JSON.parse(cycle.focus) : cycle.focus;
    } catch {
      focus = null;
    }
  }
  return {
    presence: {
      state: cycle?.state ?? "UNKNOWN",
      focus,
      ts: heardAt ? heardAt.toISOString() : null,
      recentCycles: recentCycles.rows.map((r) => ({ state: r.state, ts: r.ts, tier: r.tier })),
    },
    // What the agent would think with, so a surface can say why it is silent
    // without asking a second question. The key itself never leaves this
    // process — only whether there is one.
    model: readBaseModel(),
    catalogue: catalogueSize(),
    // What the agent is called, so a surface can show it without asking a second
    // question — and so the name is on screen everywhere the agent is, rather than
    // only on the screen that happens to be able to change it.
    identity: readIdentity(),
    control: await controlState(),
    thoughts: thoughts.rows,
    intentions: intentions.rows,
    messages: messages.rows,
    actions: actions.rows,
    budget: {
      todayByBucket: byBucket.rows.map((r) => ({ bucket: r.bucket, total: Number(r.total) })),
      byModel: byModel.rows.map((r) => ({ model: r.model, total: Number(r.total) })),
      daily: daily.rows.map((r) => ({ day: r.day, total: Number(r.total) })),
      caps,
    },
    guardian: {
      trash: trash.rows.map((r) => r.attrs),
      snapshots: snapshots.rows.map((r) => r.attrs),
      integrity: integrity.rows[0]?.attrs ?? null,
      auditHead: auditHead.rows[0] ?? null,
      protectedCount: Number(protectedCount.rows[0]?.n ?? 0),
    },
  };
}

// No inner catch: `handle` is the one error shape this bridge answers with —
// 503 `agent_unavailable`, with the reason — so a surface can tell "the agent
// is down" from every other refusal by the code alone.
app.get("/api/snapshot", handle(async (_req, res) => {
  res.json(await readView());
}));

/**
 * The agent's base model, and writing one.
 *
 * This is the one route that writes a credential, so it is also the one route
 * that checks where the request came from. The bridge listens on loopback and
 * trusts its own owner, the way it trusts a terminal on the same machine — but
 * loopback is exactly where a page in somebody's browser can also reach, and a
 * cross-origin `POST` here would install an endpoint the agent sends its own
 * reasoning to. A page this bridge served is same-origin; a page it did not is
 * refused, and a request with no browser provenance at all — a terminal, `curl`
 * — is allowed, because that is the owner.
 */
function sameOwner(req) {
  const site = req.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") return false;
  const origin = req.get("origin");
  if (!origin) return true;
  const host = req.get("host");
  return origin === `http://${host}` || origin === `https://${host}`;
}

/**
 * Which doors are open, and how each one is configured.
 *
 * Two answers in one document because they are one file: `channels` is what a
 * surface filters the transcript by, and `setup` is what a settings form edits.
 * They are read from the same place at the same moment, so a door cannot be shown
 * as open on one and closed on the other.
 *
 * `setup` reports that a credential is *present*, never what it says — a browser
 * that could read `TELEGRAM_BOT_TOKEN` would be a browser that could post it
 * somewhere. The hint is four characters, which is enough to tell the token typed
 * an hour ago from the one just pasted and useless to anybody else.
 */
app.get("/api/channels", handle(async (_req, res) => {
  const person = text(_req.query?.person, 64).trim() || "owner";
  res.json({ channels: readChannels(), setup: readChannelSetup(person) });
}));

/**
 * Turning a door on: its credentials, who it admits, and where it reaches you.
 *
 * Guarded like every other route that writes a credential or changes what the
 * agent will do, and for the same reason. This is also the route that fixes the
 * one thing that made every messaging channel unreachable from this process: a
 * bot token used to exist only as the *name* of an environment variable, so the
 * only way to hand this agent one was a shell, and a person who ran `./start.sh`
 * had a settings screen with no field for it and no way to add one.
 */
app.post("/api/channels", handle(async (req, res) => {
  if (!sameOwner(req)) {
    return res.status(403).json({ error: "cross_origin_refused" });
  }
  const result = writeChannelSetup(req.body);
  if (result?.error) {
    return res.status(400).json({ error: result.error, ...(result.detail ? { detail: result.detail } : {}) });
  }
  res.json({ ...result, channels: readChannels() });
}));

app.get("/api/model", handle(async (_req, res) => {
  res.json(readBaseModel());
}));

/**
 * What the agent is called, and writing a name for it.
 *
 * Guarded like the base model even though no credential is involved, because this is
 * the one route that changes what the agent believes about itself: a cross-origin
 * `POST` here would rewrite the first line of its system prompt, which is the same
 * class of attack as installing an endpoint the agent posts its reasoning to, and
 * for the same reason — loopback is where a page in somebody's browser reaches too.
 */
app.get("/api/identity", handle(async (_req, res) => {
  res.json(readIdentity());
}));

app.post("/api/identity", handle(async (req, res) => {
  if (!sameOwner(req)) {
    return res.status(403).json({ error: "cross_origin_refused" });
  }
  const result = writeIdentity(req.body);
  if (result?.error) {
    return res.status(400).json({ error: result.error, ...(result.detail ? { detail: result.detail } : {}) });
  }
  res.json(result);
}));

/**
 * Which providers have a key in this process's environment.
 *
 * The one route that answers a question about credentials without being able to
 * give one away, which is why it reports a name and a hint and never a value: a
 * page that could read `OPENAI_API_KEY` would be a page that could post it
 * somewhere, and a hint is enough to tell one variable from another. Every
 * provider is listed whether or not it has one, because a form can only offer
 * the button for a provider somebody has already picked otherwise.
 *
 * Unguarded on purpose, like `GET /api/model` next to it: this process sends no
 * CORS headers, so a page on another site can make the request but not read a
 * byte of the answer.
 */
app.get("/api/env-keys", handle(async (_req, res) => {
  res.json({ keys: readEnvKeys() });
}));

app.post("/api/model", handle(async (req, res) => {
  if (!sameOwner(req)) {
    return res.status(403).json({ error: "cross_origin_refused" });
  }
  const result = writeBaseModel(req.body);
  if (result?.error) {
    // 400 rather than 422: the body is the wrong shape or names something that
    // does not exist, which is a request the caller has to change, not a state
    // the agent is in.
    return res.status(400).json({ error: result.error, ...(result.detail ? { detail: result.detail } : {}) });
  }
  res.json(result);
}));

/**
 * The catalogue, for the base-model form to offer.
 *
 * A `POST` carrying an optional candidate, for the same reason the write is one:
 * a person part-way through configuring a vendor has a key or an endpoint the
 * file does not have yet, and a `GET` cannot carry either. It is guarded like the
 * write because it can carry a credential, and because it is a request this
 * process makes on somebody's behalf to an address they supplied.
 *
 * Never fails. A provider that is down, a key that is wrong and an endpoint that
 * is refused all answer with the fallback list and a reason, because the form has
 * to keep working when the network does not.
 */
app.post("/api/models", handle(async (req, res) => {
  if (!sameOwner(req)) {
    return res.status(403).json({ error: "cross_origin_refused" });
  }
  const result = await listModels(req.body);
  res.json({
    models: result.models.slice(0, MAX_CATALOGUE_ENTRIES),
    source: result.source,
    // Where the key came from, so the form can say "asked with the key you
    // typed" or "asked with OPENAI_API_KEY" rather than leaving the reader to
    // work out which of the two they just exercised.
    ...(result.key_source ? { key_source: result.key_source } : {}),
    ...(result.error ? { error: result.error } : {}),
  });
}));

/**
 * Publishing an event, which is the only way anything reaches the agent.
 *
 * The `events` table is the agent's bus: every adapter writes here and the core
 * subscribes, so a message from a browser and a message from Telegram arrive by
 * the same road and are scored by the same attention model.
 */
async function publish(kind, source, payload) {
  const inserted = await pool.query(
    "INSERT INTO events (ts, kind, source, payload) VALUES (now(), $1, $2, $3::jsonb) RETURNING id",
    [kind, source, JSON.stringify(payload)],
  );
  await pool.query("SELECT pg_notify('ethos_events', $1)", [
    JSON.stringify({ id: Number(inserted.rows[0].id) }),
  ]);
  return Number(inserted.rows[0].id);
}

/**
 * The lifecycle verbs.
 *
 * The flags are merged rather than replaced, so `stopped` survives a later
 * `resume`: a shallow replace would let "resume" silently restart an agent that
 * somebody had stopped on purpose, which is the one restart nobody asked for.
 */
const CONTROL_FLAGS = {
  pause_actions: { pause_actions: true },
  pause_all: { paused: true, pause_actions: true },
  resume: { paused: false, pause_actions: false, emergency: false },
  stop: { stopped: true },
  emergency_stop: { paused: true, pause_actions: true, stopped: true, emergency: true },
  // `preview` is a mode, not a hold, and it is kept out of the verbs above on
  // purpose. It gates every tool the way `pause_actions` does, but it answers a
  // different question, so folding the two together would make one of them lie:
  // `resume` would silently hand an agent back its ability to act while the
  // checkbox still said it was in preview, and `emergency_stop` would report
  // itself as a preview somebody asked for. Merged or not, the flags are written
  // by key, so a surface that only knows `preview` never touches the others.
  preview_on: { preview: true },
  preview_off: { preview: false },
};

app.post("/api/control", handle(async (req, res) => {
  // Guarded like the other routes that change the agent: a cross-origin form
  // POST can publish a lifecycle event just as surely as a page can.
  if (!sameOwner(req)) {
    return res.status(403).json({ error: "cross_origin_refused" });
  }
  const action = req.body?.action;
  // `hasOwnProperty`, not a truthiness check: `CONTROL_FLAGS` is a plain
  // object, so `"toString"` and `"constructor"` are truthy through the
  // prototype chain and would pass a check like `CONTROL_FLAGS[action]`.
  if (!Object.prototype.hasOwnProperty.call(CONTROL_FLAGS, action)) {
    return res.status(400).json({ error: "unknown_action" });
  }
  const by = typeof req.body?.by === "string" ? req.body.by : "web";
  await pool.query(
    `INSERT INTO env_entities (id, kind, key, attrs, owner, last_verified_at, health)
     VALUES (gen_random_uuid(), 'control', 'lifecycle', $1::jsonb, $2, now(), 'ok')
     ON CONFLICT (key) DO UPDATE SET attrs = (
       SELECT jsonb_object_agg(key, value) FROM (
         SELECT key, value FROM jsonb_each(COALESCE(env_entities.attrs, '{}'::jsonb))
         WHERE key NOT IN (SELECT key FROM jsonb_each($1::jsonb))
         UNION ALL
         SELECT key, value FROM jsonb_each($1::jsonb)
       ) merged
     ), last_verified_at = now()`,
    [JSON.stringify({ ...CONTROL_FLAGS[action], updated_at: new Date().toISOString(), updated_by: by }), by],
  );
  await publish("control.update", "web", { action, by });
  res.json({ ok: true });
}));

/**
 * The channels a surface may name in a message.
 *
 * Checked here rather than trusted, because `channel` decides where the agent's
 * *reply* goes: the life loop answers on the channel a message arrived on, so a
 * free-text channel would be a way to make this process send the agent's own
 * words to an arbitrary destination. The set is fixed and short — a new channel
 * means a new adapter, and an adapter is a deliberate thing to add.
 */
const MESSAGE_CHANNELS = new Set(["web", "cli", "telegram", "whatsapp", "slack", "discord", "email"]);

/** The doors whose `person_id` is an address in the channel's own namespace. */
const ADDRESSED_CHANNELS = new Set(["telegram", "whatsapp", "slack", "discord", "email"]);

/**
 * A message from a surface, recorded and published.
 *
 * The row is written under the same id that goes into the event payload. The
 * agent's own inbound handler inserts with `ON CONFLICT (id) DO NOTHING`, so the
 * message is stored once whether it arrives through a bridge, a terminal socket
 * or Telegram — and it is on screen immediately rather than one life cycle later.
 *
 * `channel` is the door the agent will answer through. It defaults to `web` so
 * every existing caller is unchanged, and it is how a person reads and replies to
 * one conversation from the browser while that conversation is also happening on
 * Telegram: name the channel, and the reply lands where the question was asked
 * rather than in a transcript the person is no longer reading.
 *
 * `person` is who the surface is speaking as, and `address` is where the chosen
 * channel reaches them. They are the same word on the local line and different
 * words everywhere else, so they are carried separately rather than one value
 * doing both jobs: a row written with `person_id: "owner"` on the Telegram door is
 * a conversation the agent has and the Telegram adapter cannot answer, and the
 * reply is refused after the deliberation rather than before it.
 */
/**
 * The longest message a surface may send: the same limit the interface's own composer
 * and agent client enforce, so what this process accepts and what its own
 * surfaces will send agree.
 */
const MAX_MESSAGE_LENGTH = 32_000;

async function queueMessage(text, person, address, channel = "web") {
  const content = String(text ?? "").trim();
  if (!content) return { error: "empty_message" };
  // Refused rather than truncated. A message that was cut down would be shown
  // in the transcript as though it had been sent whole, and the sender's own
  // copy is the only record of what went missing; a loud refusal is one the
  // sender can act on, and a silent cut is one they cannot even see.
  if (content.length > MAX_MESSAGE_LENGTH) {
    return { error: "message_too_long", limit: MAX_MESSAGE_LENGTH };
  }
  const who = address || person;
  const conversationKey = `${channel}:${who}`;
  const messageId = crypto.randomUUID();
  await pool.query(
    `INSERT INTO messages (id, ts, direction, channel, conversation_key, person_id, text)
     VALUES ($1, now(), 'inbound', $2, $3, $4, $5)
     ON CONFLICT (id) DO NOTHING`,
    [messageId, channel, conversationKey, who, content],
  );
  await publish("msg.inbound", channel, {
    message_id: messageId,
    channel,
    person_id: who,
    text: content,
    conversation_key: conversationKey,
    is_untrusted: false,
    urgency: 0.3,
    summary: `Message from ${who}: ${content.slice(0, 200)}`,
    sender_person: who,
    // `known`, not `owner`: the attention scorer weights senders by class and
    // has never heard of a class called "owner", so the name was scoring zero.
    sender_class: "known",
  });
  return { id: messageId };
}

/**
 * Who a surface is speaking as, and where the chosen door reaches them.
 *
 * `null` for the address is a refusal, and the route turns it into one. The
 * alternative — writing the row and letting the adapter refuse the reply — is the
 * failure this exists to prevent: the transcript shows a conversation on a door
 * that never carried it, the agent deliberates about it as though a person had
 * asked, and the answer is discarded at the last step, so what the reader sees is
 * a question they asked, a long pause, and silence.
 */
function resolveDestination(body, book) {
  const requested = typeof body?.channel === "string" ? body.channel.trim() : "web";
  const channel = MESSAGE_CHANNELS.has(requested) ? requested : "web";
  const person = typeof body?.person_id === "string" && body.person_id.trim()
    ? body.person_id.trim().slice(0, 64)
    : "owner";
  // The local line needs no entry in the contact book: it delivers to whoever is
  // reading the transcript, so the person is the address. Only the doors that
  // post somewhere require one.
  if (!ADDRESSED_CHANNELS.has(channel)) return { channel, person, address: person };
  // An address handed over directly is honoured, because a surface talking to a
  // deployment it knows well is the only thing that can name an address the
  // contact book has not been taught yet — and the adapter is the last word on
  // whether it is usable, which is exactly where a bad one is caught.
  const offered = typeof body?.address === "string" ? body.address.trim().slice(0, 128) : "";
  return { channel, person, address: offered || resolveAddress(book, person, channel) || null };
}

app.post("/api/message", handle(async (req, res) => {
  if (!sameOwner(req)) {
    return res.status(403).json({ error: "refused" });
  }
  // A named channel is a destination for the agent's reply, so this route now
  // writes to the world rather than to a local transcript — the same reason the
  // base-model route above checks where the request came from.
  const book = readAddressBook();
  const { channel, person, address } = resolveDestination(req.body, book);
  if (!address) {
    // The door is open but this person is not on it, so there is nowhere for the
    // reply to go. Refused here, with the reason, rather than after a
    // deliberation whose result is discarded.
    return res.status(409).json({
      error: "channel_unaddressed",
      channel,
      person_id: person,
      detail: `no address for ${person} on ${channel}; add one to channels: in config/people.yaml`,
    });
  }
  const queued = await queueMessage(req.body?.text, person, address, channel);
  if (queued?.error) {
    return res.status(400).json({ error: queued.error, ...(queued.limit ? { limit: queued.limit } : {}) });
  }
  // `person_id` is the person, `address` is where the door reaches them — the
  // same distinction the request made, kept in the answer so a caller reading
  // back what it sent is not told its person became a `tg:` id.
  res.json({ ok: true, message_id: queued.id, channel, person_id: person, address });
}));

/**
 * An undo, and a cancelled intention.
 *
 * Both are *requests*, published as events and left for the agent to honour. The
 * interface never flips a row itself: the guardian decides whether an action is
 * reversible, and the agent decides whether a goal still stands.
 */
app.post("/api/actions/:id/undo", handle(async (req, res) => {
  if (!sameOwner(req)) {
    return res.status(403).json({ error: "cross_origin_refused" });
  }
  const requestedBy = typeof req.body?.requested_by === "string" ? req.body.requested_by : "web";
  await publish("guardian.undo", "web", { action_id: req.params.id, requested_by: requestedBy });
  res.json({ queued: true });
}));

app.post("/api/intentions/:id/close", handle(async (req, res) => {
  if (!sameOwner(req)) {
    return res.status(403).json({ error: "cross_origin_refused" });
  }
  const by = typeof req.body?.by === "string" ? req.body.by : "web";
  await publish("intention.update", "web", {
    intention_id: req.params.id,
    status: "cancelled",
    action: "cancel",
    by,
  });
  res.json({ queued: true });
}));

// The interface's own local bridge (its dev server) owns this prefix. Answering with an
// empty body rather than the single-page app means a browser that probes for it
// gets "not here" instead of HTML where it expected JSON — and, unlike a 404, it
// does not print an error in the reader's console on every load.
app.use("/__phone", (_req, res) => res.status(204).end());

/**
 * The build, as a request-time question rather than a startup-time one.
 *
 * This used to be `if (fs.existsSync(PHONE_DIST))` around the route registration,
 * which decided once, at startup, whether this process would ever serve a page.
 * A bridge that started before `npm run build` finished therefore had no static
 * routes for its whole life, and the only cure was restarting it — while every
 * signal it could give said everything was fine: `/api/health` answered, so
 * `ethos status` printed "interface: up", and a second start found the port taken
 * and reported that the page was already being served.
 *
 * Asking at request time costs one `stat` and makes the answer true. The build
 * can land a second after this process started, or be replaced under it, and the
 * page is served from the first request that finds it. A missing build is still
 * reported rather than fatal, because the API without a UI is still useful —
 * `phone agent` is exactly that case.
 */
const NOT_BUILT =
  `The interface is not built yet.\n\n  npm run build          # from the checkout root\n` +
  `  # or point ETHOS_PHONE_DIST at an existing build\n\nThe API is live on this port regardless.\n`;

const indexFile = () => path.join(PHONE_DIST, "index.html");

// A missing root is not an error for express.static: it finds nothing and calls
// next(), which is what sends an unbuilt request on to the handler below.
app.use(express.static(PHONE_DIST));

// An /api path that no route answered is a wrong URL or an older client, and
// answering it with the single-page app would hand a script a 200 and a
// screenful of HTML where it expected JSON. "Not here" is the honest answer,
// and it is the one the browser's own console reads as an error.
app.get("/api/*", (_req, res) => res.status(404).json({ error: "not_found" }));

app.get("*", (_req, res) => {
  const index = indexFile();
  if (!fs.existsSync(index)) {
    res.status(503).type("text/plain").send(NOT_BUILT);
    return;
  }
  res.sendFile(index);
});

/**
 * The last resort for anything the framework itself rejects: malformed JSON in
 * a request body, an unmatched route, a handler that threw outside `handle`.
 *
 * An HTML page with a stack trace is the default, and it is the wrong default
 * twice over — the caller on the other end is a surface speaking this API, not a
 * person reading a document, and a stack trace is a map of this machine that a
 * caller has no business being given. The answer is JSON, the status the
 * framework chose, and a code a client can match.
 */
app.use((error, req, res, _next) => {
  console.error(`ethos-web ${req.method} ${req.path}: ${error?.message ?? error}`);
  if (res.headersSent) return;
  const status = typeof error?.status === "number" ? error.status
    : typeof error?.statusCode === "number" ? error.statusCode
      : 500;
  res.status(status).json({ error: status >= 500 ? "internal_error" : "bad_request" });
});

const server = http.createServer(app);
const wss = new WebSocketServer({ server, path: "/events" });

function broadcast(frame) {
  const text = JSON.stringify(frame);
  for (const client of wss.clients) {
    if (client.readyState === 1) {
      try {
        client.send(text);
      } catch {
        /* drop */
      }
    }
  }
}

/**
 * The `sameOwner` rule for the socket road, where there is no `req.get`.
 *
 * WebSockets are not subject to CORS, so a page this bridge did not serve can
 * open one just as easily as it can post to an HTTP route — and a socket it
 * accepted would hand it every event the agent emits, inbound messages
 * included. Browsers always send `Origin` on a WebSocket; a client with none is
 * a terminal or a script on this machine, which is the owner.
 */
function sameOwnerUpgrade(request) {
  const site = request.headers["sec-fetch-site"];
  if (site && site !== "same-origin" && site !== "none") return false;
  const origin = request.headers.origin;
  if (!origin) return true;
  const host = request.headers.host;
  return origin === `http://${host}` || origin === `https://${host}`;
}

wss.on("connection", (socket, request) => {
  if (!sameOwnerUpgrade(request)) {
    // The same refusal the HTTP roads answer with, as a close code: 4403 is
    // "cross origin refused", which is a thing a surface can report rather
    // than a mystery disconnect it has to guess at.
    socket.close(4403, "cross_origin_refused");
    return;
  }
  // The first frame asks a surface to read the agent, so a reconnect
  // resynchronises the whole view instead of only the changes since it dropped.
  socket.send(JSON.stringify({ type: "snapshot_available" }));
  socket.on("message", async (raw) => {
    let payload;
    try {
      payload = JSON.parse(String(raw));
    } catch {
      socket.send(JSON.stringify({ type: "error", error: "invalid_json" }));
      return;
    }
    // Only one inbound frame is honoured. Anything else is dropped rather than
    // answered, because a stream that replies to every frame it does not
    // understand is a stream a surface cannot trust to be quiet.
    if (payload?.type !== "message") return;
    try {
      // The same destination rule as the HTTP route above, and deliberately the
      // same function: a surface that reaches the agent over a socket instead of
      // over HTTP must not be able to do something the other road refuses, or the
      // owner check and the address lookup become the weaker of the two.
      const book = readAddressBook();
      const { channel, person, address } = resolveDestination(payload, book);
      if (!address) {
        socket.send(JSON.stringify({
          type: "error",
          error: "channel_unaddressed",
          channel,
          person_id: person,
        }));
        return;
      }
      const queued = await queueMessage(payload.text, person, address, channel);
      if (queued?.error) {
        // The same codes the HTTP road answers with, for the same failures:
        // one road, one vocabulary.
        socket.send(JSON.stringify({ type: "error", error: queued.error }));
        return;
      }
      // The chosen door, so a surface can tell which conversation the question
      // belongs to without reading the transcript back.
      socket.send(JSON.stringify({ type: "queued", message_id: queued.id, channel }));
    } catch {
      socket.send(JSON.stringify({ type: "error", error: "agent_unavailable" }));
    }
  });
});

let listenBackoffMs = 1000;

/**
 * `LISTEN`/`NOTIFY` for the agent's bus, with a fresh connection each time.
 *
 * A dedicated client rather than a pooled one: `LISTEN` is session state, and a
 * pooled connection that went quiet would stop delivering events with no error
 * anywhere. A dropped connection is a reconnect, so the backoff is the only state
 * this keeps.
 */
async function maintainListener() {
  for (;;) {
    const client = new Client({ connectionString: DB_DSN });
    try {
      const disconnected = new Promise((resolve) => {
        client.on("end", () => resolve());
        client.on("error", () => resolve());
      });
      client.on("notification", async (msg) => {
        try {
          const parsed = JSON.parse(msg.payload ?? "{}");
          const row = await pool.query("SELECT kind, payload FROM events WHERE id = $1", [
            Number(parsed.id),
          ]);
          if (row.rows[0]) {
            broadcast({ type: "event", kind: row.rows[0].kind, payload: row.rows[0].payload ?? {} });
          }
        } catch {
          /* ignore one bad notify */
        }
      });
      await client.connect();
      await client.query("LISTEN ethos_events");
      listenBackoffMs = 1000;
      await disconnected;
    } catch {
      /* connect failed; back off below */
    } finally {
      try {
        await client.end();
      } catch {
        /* ignore */
      }
    }
    await new Promise((resolve) => setTimeout(resolve, listenBackoffMs));
    listenBackoffMs = Math.min(listenBackoffMs * 2, 30000);
  }
}

void maintainListener();

/**
 * Saying why the port could not be had, instead of the stack trace Node prints.
 *
 * An unhandled `error` on a listening server is an uncaught exception: the
 * process dies with `Error: listen EADDRINUSE` and a Node stack on stderr, which
 * the supervisor files under `ethos-web.log` where nobody reads it. And the
 * situation is usually not a fault at all — the supervisor starts this bridge
 * alongside an agent that may already have one running, so the ordinary case is a
 * second bridge finding the port taken.
 *
 * "Taken" is not the same as "served", though, and this used to conflate them: it
 * announced that the page on that port was the other bridge's, and exited 0,
 * without asking. When the bridge holding the port started before the interface
 * was built it has no static routes at all, and answers `/` with a 404 — so a
 * second `ethos up`, a second `npm start`, and `ethos status` all reported a
 * working interface while the page was unreachable. Asking costs one request and
 * is the difference between a start command that is true and one that has to be
 * believed.
 *
 * It is registered on the WebSocket server as well as on the HTTP one because
 * that is where the error surfaces when the two share a server: `ws` attaches to
 * the HTTP server's listening handle, and the failure arrives as a `WebSocketServer`
 * 'error' — a handler on the HTTP server alone never sees it.
 */
/**
 * What the process already holding the port actually does about the page.
 *
 * A 200 with an HTML content type is the one answer that means "the interface is
 * being served, and there is nothing left to do". Everything else is reported as
 * it was found — the status and the content type, because "something is on the
 * port" and "the page is on the port" are different claims and only the second
 * one is worth exiting 0 over.
 */
function probeExistingListener() {
  return new Promise((settle) => {
    const request = http.get(
      { host: "127.0.0.1", port: PORT, path: "/", timeout: 2000 },
      (response) => {
        const type = String(response.headers["content-type"] ?? "");
        const servesPage = response.statusCode === 200 && type.includes("text/html");
        response.resume();
        settle({
          servesPage,
          detail: `answered ${response.statusCode} as ${type.split(";")[0] || "nothing"}`,
        });
      },
    );
    request.on("timeout", () => request.destroy());
    request.on("error", (err) => settle({ servesPage: false, detail: `did not answer (${err.code ?? err.message})` }));
  });
}

function reportListenFailure(error) {
  if (error?.code === "EADDRINUSE") {
    void probeExistingListener().then((answer) => {
      if (answer.servesPage) {
        console.error(
          `ethos-web: ${PORT} is already in use — another bridge is serving the interface there. ` +
            `Nothing to do: the page on http://127.0.0.1:${PORT} is that one.`,
        );
        process.exit(0);
      }
      console.error(
        `ethos-web: ${PORT} is already in use, but nothing there is serving the interface — ` +
          `asking it for the page ${answer.detail}.`,
      );
      console.error(
        "   That is what a bridge started before the interface was built looks like: it holds\n" +
          "   the port and answers its API, and serves no page. Stop it and start this one again:",
      );
      // `make stop`, not `ethos stop`: `ethos` is the checkout's console script, so
      // it is only on PATH in a shell that has activated the virtualenv, and this
      // message is read by people whose shell has not. A remedy that is itself
      // "command not found" is the same failure as the one being reported.
      console.error(`     make stop && npm start          # stop the agent holding ${PORT}`);
      console.error("   Or leave it alone and run a second one on its own port:");
      console.error("     ETHOS_WEB_PORT=8721 npm start");
      process.exit(1);
    });
    return;
  }
  if (error?.code === "EACCES") {
    console.error(`ethos-web: not allowed to listen on ${PORT} — pick another with ETHOS_WEB_PORT.`);
    process.exit(1);
  }
  console.error(`ethos-web: could not listen on ${PORT}: ${error?.message ?? error}`);
  process.exit(1);
}

server.on("error", reportListenFailure);
wss.on("error", reportListenFailure);

server.listen(PORT, "127.0.0.1", () => {
  // Stated as a fact about the build's presence at this moment, not a promise
  // about it: the page is looked up per request, so a build that lands after
  // this line is served without a restart and the line above it is not a
  // prediction that has since stopped being true.
  const ui = fs.existsSync(indexFile())
    ? `Clio Agent 3 Beta 1 on http://127.0.0.1:${PORT}`
    : "interface not built yet (the API is live; the page appears once `npm run build` finishes)";
  console.log(`ethos-web listening on http://127.0.0.1:${PORT} — ${ui}`);
});
