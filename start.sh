#!/usr/bin/env bash
#
# Deprecated. Kept so an existing muscle memory, an existing README link and an
# existing script keep working; the startup itself is now `ethos up`.
#
#   pip install -e .        once
#   ethos up               from anywhere, in any directory
#
# This file used to hold the whole start-up: 330 lines that created the
# virtualenv, installed the dependencies, brought up Postgres, migrated, seeded,
# found Node, installed and built the interface, ran a health check, started the
# agent detached, waited for it to answer and opened the page. That sequence now
# lives in `ethos/core/bootstrap.py`, next to the commands it runs, and there is
# one implementation of it rather than one in bash and a smaller one in the
# package the console script starts.
#
# What is left here is the one part that cannot move: building a virtualenv needs
# `uv` installed, and `uv` is what is being used to install this project, so there
# is no interpreter yet to ask to do it. Everything after that is delegated to
# `ethos up` — including the flags, which are translated rather than re-parsed
# twice.
#
# Usage (all still work, all deprecated):
#   ./start.sh [up] [--no-open] [--wait N]  the same as `ethos up`
#   ./start.sh status                      the same as `ethos status`
#   ./start.sh stop                        the same as `ethos stop`
#   ./start.sh logs                        the same as `ethos logs`
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/.venv/bin/python"

OPEN_BROWSER=1
WAIT_S="${ETHOS_AGENT_WAIT_S:-180}"
COMMAND="up"

while [ $# -gt 0 ]; do
  case "$1" in
    up|status|stop|logs) COMMAND="$1"; shift ;;
    --no-open) OPEN_BROWSER=0; shift ;;
    --wait)
      [ $# -ge 2 ] || { echo "--wait needs a number of seconds" >&2; exit 2; }
      WAIT_S="$2"; shift 2
      ;;
    -h|--help)
      cat >&2 <<'USAGE'
Usage:
  ./start.sh [up] [--no-open] [--wait N]  the same as `ethos up`
  ./start.sh status                      the same as `ethos status`
  ./start.sh stop                        the same as `ethos stop`
  ./start.sh logs                        the same as `ethos logs`

Deprecated: use `ethos up` (after `pip install -e .`), which works from any
directory and is what the README documents.
USAGE
      exit 0
      ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Said once, on stderr, before anything is built: a deprecation nobody is told
# about is a deprecation that only bites when it is removed. On stderr because
# this script's stdout is the agent's report, and a line of the agent's report
# should be the agent's report.
echo "-- ./start.sh is deprecated; this runs \`ethos ${COMMAND}\` for you." >&2
echo "-- install it once with \`pip install -e .\` and type \`ethos up\` instead." >&2

# PATH, widened once, before anything is looked for.
#
# `uv` and `node` are found where Homebrew and a version manager put them, and a
# script is run from plenty of places that never had any of that on PATH: a
# `cron` entry, a launchd job, an editor's task runner, another program's
# `subprocess`. On a machine that has both tools installed, `./start.sh` from one
# of those said `uv is required` — advice about a dependency the machine does not
# lack, which is the same class of wrong answer as "the agent is not running".
# `ethos up` looks for Node itself (`ethos.core.supervisor.node_tool`), so this is
# only about the one tool that has to exist before there is an interpreter.
for dir in /opt/homebrew/bin /usr/local/bin "$HOME/.local/bin" "$HOME/.cargo/bin"; do
  if [ -d "$dir" ]; then
    case ":$PATH:" in
      *":$dir:"*) ;;
      *) PATH="$dir:$PATH" ;;
    esac
  fi
done
export PATH

# --------------------------------------------------------------- the short verbs
#
# A start-up is the only step that needs this file. The other three are answered
# by an installed agent, and an agent that was never installed has nothing to
# answer them with — so the venv is created for `up` only, and the others say what
# is wrong instead of creating a 300 MB virtualenv to read a pid file.
if [ "$COMMAND" != "up" ]; then
  if [ ! -x "$PY" ]; then
    case "$COMMAND" in
      stop) echo "nothing to stop: there is no virtualenv in $ROOT" >&2; exit 0 ;;
      *) echo "no virtualenv in $ROOT yet — run ./start.sh (or: pip install -e .)" >&2; exit 1 ;;
    esac
  fi
  exec "$PY" -m ethos "$COMMAND"
fi

# -------------------------------------------------------------------- the venv
#
# `ethos up` needs its dependencies installed before it can be asked to do
# anything, and installing them is what this file is still for.

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to create the virtualenv (https://docs.astral.sh/uv/)." >&2
  echo "Install it, then re-run — or install into an interpreter you already have:" >&2
  echo "  python3.12 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

cd "$ROOT"

if [ ! -d .venv ]; then
  echo "-- creating Python 3.12 venv"
  uv python install 3.12
  uv venv .venv --python 3.12
fi

echo "-- installing dependencies"
uv pip install --python "$PY" -e ".[dev]" >/dev/null

# ---------------------------------------------------------------------- hand over

# An `if` and not a `&&`, for the reason the retired version of this file had a
# paragraph about: under `set -e` a failed `&&` list exits on the spot, and the
# list fails precisely when the browser *should* open — so the agent would be
# started and this script would die before reporting it.
set -- up --wait "$WAIT_S"
if [ "$OPEN_BROWSER" -eq 0 ]; then
  set -- "$@" --no-open
fi
exec "$PY" -m ethos "$@"
