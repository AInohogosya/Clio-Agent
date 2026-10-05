#!/usr/bin/env bash
#
# Bring up the one thing Clio Agent 3 Beta cannot run without: PostgreSQL 16 + pgvector,
# reachable at $ETHOS_DSN, with the application role, database and extensions
# already in place.
#
# Idempotent. If a Postgres is already answering at the DSN and the agent can
# log in, this exits in under a second and changes nothing. Otherwise it tries,
# in order:
#
#   1. Docker, via docker-compose.yml (pgvector/pgvector:pg16) — the documented
#      path, and the one the Makefile talks about.
#   2. A local PostgreSQL 16: already installed, or installed from Homebrew.
#      The cluster lives in data/pgdata — gitignored, beside the agent's other
#      local state, and bound to 127.0.0.1 only. It is left alone by `make
#      clean`, because it holds the agent's memory; drop it with
#      `scripts/ensure_postgres.sh stop && rm -rf data/pgdata`.
#
# The only writes outside this repo are the Homebrew packages themselves.
#
# Usage: scripts/ensure_postgres.sh [up|stop|status]

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PG_MAJOR="${ETHOS_PG_MAJOR:-16}"
PGVECTOR_VERSION="${ETHOS_PGVECTOR_VERSION:-0.8.6}"
PGVECTOR_REPO="${ETHOS_PGVECTOR_REPO:-https://github.com/pgvector/pgvector.git}"

DATA_DIR="$ROOT/data"
PGDATA_DIR="${ETHOS_PGDATA:-$DATA_DIR/pgdata}"
LOG_DIR="$DATA_DIR/logs"
PG_LOG="$LOG_DIR/postgres.log"
READY_TIMEOUT="${ETHOS_PG_READY_TIMEOUT:-60}"

PG_BIN=""

say() { echo "-- $*"; }
warn() { echo "!! $*" >&2; }
die() {
  echo "!! $*" >&2
  exit 1
}
have() { command -v "$1" >/dev/null 2>&1; }

# --- the locale --------------------------------------------------------------
#
# Postgres refuses to start when it cannot pin a locale, and it says so as
# "postmaster became multithreaded during startup" with a hint about `LC_ALL` —
# a message that names a threading fault and is about none. An inherited
# `LC_ALL=""` is the whole cause, and an empty one is what every
# non-interactive shell has: a CI step, a launch agent, a terminal multiplexer
# started by something else, a tool session. So the postmaster is given a locale
# it can actually use rather than whatever it inherited: the caller's if there is
# one, else the first UTF-8 locale this machine has, else `C`.

usable_locale() {
  if [ -n "${LC_ALL:-}" ] || [ -n "${LANG:-}" ]; then
    echo "${LC_ALL:-${LANG:-C}}"
    return 0
  fi
  local available candidate
  available="$(locale -a 2>/dev/null || true)"
  for candidate in en_US.UTF-8 en_US.utf8 C.UTF-8 C; do
    case "$available" in
      *"$candidate"*) echo "$candidate"; return 0 ;;
    esac
  done
  echo C
}

ETHOS_LOCALE="$(usable_locale)"

# --- the DSN, parsed ---------------------------------------------------------
#
# The default matches config/ethos.yaml, so a bare ./start.sh and a bare
# make db-up converge on the same database.

dsn="${ETHOS_DSN:-postgresql://ethos:ethos@127.0.0.1:5433/ethos}"
case "$dsn" in
postgres://*) dsn="postgresql://${dsn#postgres://}" ;;
esac
dsn_body="${dsn#*://}"
[ "$dsn_body" != "$dsn" ] || die "ETHOS_DSN is not a URL: $dsn"

dsn_auth="${dsn_body%%/*}"
if [ "${dsn_body#*/}" = "$dsn_body" ]; then
  dsn_path="" # no "/database" in the URL
else
  dsn_path="${dsn_body#*/}"
fi
PGDATABASE="${dsn_path%%\?*}"
PGDATABASE="${PGDATABASE:-ethos}"

PGPASSWORD=""
if [ "${dsn_auth#*@}" != "$dsn_auth" ]; then
  dsn_creds="${dsn_auth%%@*}"
  dsn_hostport="${dsn_auth#*@}"
  PGUSER="${dsn_creds%%:*}"
  if [ "${dsn_creds#*:}" != "$dsn_creds" ]; then
    PGPASSWORD="${dsn_creds#*:}"
  fi
else
  dsn_hostport="$dsn_auth"
  PGUSER="${PGUSER:-}"
fi

if [ "${dsn_hostport#*:}" != "$dsn_hostport" ]; then
  PGHOST="${dsn_hostport%%:*}"
  PGPORT="${dsn_hostport##*:}"
else
  PGHOST="$dsn_hostport"
  PGPORT="5432"
fi
PGHOST="${PGHOST#[}"
PGHOST="${PGHOST%]}"
PGHOST="${PGHOST:-127.0.0.1}"
PGUSER="${PGUSER:-ethos}"
[[ "$PGPORT" =~ ^[0-9]+$ ]] || die "ETHOS_DSN has a non-numeric port: $PGPORT"

# Postgres caps socket paths near 100 characters and this repo may sit deep in a
# checkout; a short prefix keeps the loopback socket usable.
if [ "${#PGDATA_DIR}" -gt 60 ]; then
  SOCKET_DIR="/tmp"
else
  SOCKET_DIR="$PGDATA_DIR/sockets"
fi

rel() { echo "${1#"$ROOT"/}"; }

# --- helpers -----------------------------------------------------------------

port_open() { (exec 3<>"/dev/tcp/$1/$2") >/dev/null 2>&1; }

# The connection the agent itself will make: TCP, as the DSN's role, into the
# DSN's database. This is the only check that counts as "the database is up".
can_connect() {
  local psql
  psql="$PG_BIN/psql"
  if [ ! -x "$psql" ]; then
    if have psql; then
      psql="$(command -v psql)"
    else
      psql=""
    fi
  fi
  if [ -n "$psql" ]; then
    PGPASSWORD="$PGPASSWORD" "$psql" -X -q -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
      -d "$PGDATABASE" -tAc 'SELECT 1' >/dev/null 2>&1 && return 0
  fi
  # No psql (the docker path, before any local install): the project's own
  # driver is the next best witness.
  if [ -x "$ROOT/.venv/bin/python" ]; then
    DSN="$dsn" "$ROOT/.venv/bin/python" -c '
import asyncio, os, asyncpg
async def main():
    conn = await asyncpg.connect(os.environ["DSN"])
    await conn.close()
asyncio.run(main())' >/dev/null 2>&1 && return 0
  fi
  return 1
}

docker_usable() { have docker && docker info >/dev/null 2>&1; }

# An SDK that is actually installed. xcrun can hand back a dangling symlink, so
# fall back to the newest SDK on disk.
usable_sdk() {
  local sdk newest
  sdk="$(xcrun --show-sdk-path 2>/dev/null || true)"
  if [ -n "$sdk" ] && [ -d "$sdk" ]; then
    echo "$sdk"
    return 0
  fi
  newest="$(find /Library/Developer/CommandLineTools/SDKs \
    /Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs \
    -maxdepth 1 -name 'MacOSX*.sdk' 2>/dev/null | sort -V | tail -1 || true)"
  [ -n "$newest" ] || return 1
  echo "$newest"
}

find_pg_bin() {
  local candidate
  for candidate in \
    "$PG_BIN" \
    /opt/homebrew/opt/postgresql@${PG_MAJOR}/bin \
    /usr/local/opt/postgresql@${PG_MAJOR}/bin \
    /opt/homebrew/bin /usr/local/bin /usr/bin; do
    if [ -n "$candidate" ] && [ -x "$candidate/postgres" ] &&
      "$candidate/postgres" --version 2>/dev/null | grep -qE "PostgreSQL\) ${PG_MAJOR}\."; then
      PG_BIN="$candidate"
      return 0
    fi
  done
  if have postgres && postgres --version 2>/dev/null | grep -qE "PostgreSQL\) ${PG_MAJOR}\."; then
    PG_BIN="$(dirname "$(command -v postgres)")"
    return 0
  fi
  return 1
}

# --- docker ------------------------------------------------------------------

start_docker_db() {
  say "starting postgres in docker"
  docker compose up -d db || return 1
  for _ in $(seq 1 "$READY_TIMEOUT"); do
    if docker compose exec -T db pg_isready -U ethos -d ethos >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

# --- a local PostgreSQL 16 ---------------------------------------------------

install_postgres_homebrew() {
  have brew || return 1
  say "installing postgresql@${PG_MAJOR} and pgvector via homebrew (one time, a few minutes)"
  brew install "postgresql@${PG_MAJOR}" || return 1
  brew install pgvector >/dev/null 2>&1 || true
  find_pg_bin
}

ensure_postgres_install() {
  if find_pg_bin; then
    return 0
  fi
  if ! install_postgres_homebrew; then
    die "no PostgreSQL ${PG_MAJOR} on this machine, and homebrew is not installed.
   Fix it with one of:
     brew install postgresql@${PG_MAJOR}    # what this script would do for you
     install Docker Desktop                 # start.sh then uses docker-compose.yml
     export ETHOS_DSN=postgresql://user:pass@host:5432/db   # a Postgres you already run"
  fi
}

# pgvector is a C extension, and Homebrew's current formula is built for newer
# majors than ours. Reuse its files when they match this server's major;
# otherwise compile pgvector against it, which is what upstream `make install` does.
ensure_pgvector() {
  local sharedir major keg
  sharedir="$("$PG_BIN/pg_config" --sharedir)"
  if [ -f "$sharedir/extension/vector.control" ]; then
    return 0
  fi

  if have brew; then
    major="$("$PG_BIN/pg_config" --version | awk -F. '{print $1}')"
    keg="$(brew --prefix pgvector 2>/dev/null || true)"
    if [ -n "$keg" ] && [ -f "$keg/share/postgresql@${major}/extension/vector.control" ]; then
      say "adding pgvector to postgresql@${major} from the homebrew keg"
      cp -R "$keg/share/postgresql@${major}/extension/." "$sharedir/extension/"
      cp -R "$keg/lib/postgresql@${major}/." "$("$PG_BIN/pg_config" --pkglibdir)/"
      if [ -f "$sharedir/extension/vector.control" ]; then
        return 0
      fi
    fi
  fi

  say "building pgvector ${PGVECTOR_VERSION} for PostgreSQL ${PG_MAJOR}"
  build_dir="$(mktemp -d)"
  trap 'rm -rf "$build_dir"' EXIT
  git -c advice.detachedHead=false clone --quiet --depth 1 --branch "v${PGVECTOR_VERSION}" \
    "$PGVECTOR_REPO" "$build_dir/pgvector" ||
    die "could not fetch pgvector v${PGVECTOR_VERSION} from $PGVECTOR_REPO"

  # Homebrew bakes the SDK it compiled Postgres against into pg_config, and PGXS
  # feeds it to every extension build as -isysroot. When macOS has moved on and
  # that SDK is no longer installed, the build dies on a missing stdio.h. Point
  # it at an SDK this machine actually has.
  local build_flags=() sysroot_wanted sysroot_live
  sysroot_wanted="$("$PG_BIN/pg_config" --configure 2>/dev/null |
    grep -oE "PG_SYSROOT=[^']+" | head -1 | cut -d= -f2- || true)"
  if [ -n "$sysroot_wanted" ] && [ ! -d "$sysroot_wanted" ]; then
    sysroot_live="$(usable_sdk || true)"
    if [ -n "$sysroot_live" ]; then
      say "  (rebuilding against $sysroot_live: $sysroot_wanted is gone)"
      build_flags=("PG_SYSROOT=$sysroot_live")
    else
      die "Postgres was built against $sysroot_wanted, which is no longer installed, and no
   macOS SDK is available to rebuild pgvector against.
   Install one with:  xcode-select --install"
    fi
  fi

  local build_log="$LOG_DIR/pgvector-build.log"
  mkdir -p "$LOG_DIR"
  if ! make -C "$build_dir/pgvector" PG_CONFIG="$PG_BIN/pg_config" "${build_flags[@]}" \
    >"$build_log" 2>&1; then
    warn "$(tail -6 "$build_log")"
    die "pgvector failed to compile; the tail of its build log is above ($(rel "$build_log"))"
  fi
  if ! make -C "$build_dir/pgvector" install PG_CONFIG="$PG_BIN/pg_config" \
    "${build_flags[@]}" >>"$build_log" 2>&1; then
    warn "$(tail -6 "$build_log")"
    die "pgvector failed to install; see $(rel "$build_log")"
  fi
  [ -f "$sharedir/extension/vector.control" ] ||
    die "pgvector installed but $sharedir/extension/vector.control is missing"
  return 0
}

# --- the cluster -------------------------------------------------------------

write_config() {
  [ -f "$PGDATA_DIR/postgresql.conf" ] || return 0
  mkdir -p "$PGDATA_DIR/conf.d" "$SOCKET_DIR"
  cat >"$PGDATA_DIR/conf.d/ethos.conf" <<EOF
# Written by scripts/ensure_postgres.sh. Changes here are overwritten.
port = ${PGPORT}
listen_addresses = '${PGHOST}'
unix_socket_directories = '${SOCKET_DIR}'
max_connections = 50
EOF
  # Included exactly once, so re-running stays harmless.
  if ! grep -q "include_dir = 'conf.d'" "$PGDATA_DIR/postgresql.conf"; then
    printf "\ninclude_dir = 'conf.d'\n" >>"$PGDATA_DIR/postgresql.conf"
  fi
}

init_cluster() {
  local err
  say "creating a postgres ${PG_MAJOR} cluster in $(rel "$PGDATA_DIR")"
  err="$(mktemp)"
  # --auth=trust keeps the loopback connection usable without a password dance;
  # the server only ever listens on 127.0.0.1, and the DSN's password is still
  # set on the role below.
  if ! "$PG_BIN/initdb" -D "$PGDATA_DIR" -U "$PGUSER" -E UTF-8 --locale=en_US.UTF-8 \
    --auth-local=trust --auth-host=trust >"$err" 2>&1; then
    rm -rf "$PGDATA_DIR"
    if ! "$PG_BIN/initdb" -D "$PGDATA_DIR" -U "$PGUSER" -E UTF-8 --locale=C \
      --auth-local=trust --auth-host=trust >>"$err" 2>&1; then
      warn "$(tail -5 "$err")"
      rm -f "$err"
      die "initdb failed; the reason is above"
    fi
  fi
  rm -f "$err"
  mkdir -p "$LOG_DIR" "$SOCKET_DIR"
  write_config
}

cluster_running() { "$PG_BIN/pg_ctl" -D "$PGDATA_DIR" status >/dev/null 2>&1; }

# The port our own cluster is configured for, which is not always the DSN's:
# pg_ctl status only reports the port when it was passed on the command line, so
# ask the config Postgres is actually reading.
own_cluster_port() {
  local file port
  for file in "$PGDATA_DIR/conf.d/ethos.conf" "$PGDATA_DIR/postgresql.conf"; do
    [ -f "$file" ] || continue
    port="$(awk -F= '/^[[:space:]]*port[[:space:]]*=/ {gsub(/[[:space:]]/, "", $2); print $2; exit}' "$file")"
    if [ -n "$port" ]; then
      echo "$port"
      return 0
    fi
  done
  return 1
}

start_cluster() {
  mkdir -p "$LOG_DIR" "$SOCKET_DIR"
  write_config
  local action=start
  if cluster_running; then
    if port_open "$PGHOST" "$PGPORT"; then
      return 0
    fi
    action=restart # running, but not where the DSN points
  fi
  say "${action}ing postgres in $(rel "$PGDATA_DIR") on ${PGHOST}:${PGPORT}"
  if ! env LC_ALL="$ETHOS_LOCALE" LANG="$ETHOS_LOCALE" "$PG_BIN/pg_ctl" -D "$PGDATA_DIR" -l "$PG_LOG" \
    -w -t "$READY_TIMEOUT" -o "-p ${PGPORT}" "$action" >/dev/null; then
    warn "$(tail -5 "$PG_LOG" 2>/dev/null || true)"
    die "postgres did not start; the tail of its log is above ($(rel "$PG_LOG"))"
  fi
  for _ in $(seq 1 "$READY_TIMEOUT"); do
    if port_open "$PGHOST" "$PGPORT"; then
      return 0
    fi
    sleep 1
  done
  die "postgres is running but nothing is listening on ${PGHOST}:${PGPORT}"
}

# psql as the cluster's bootstrap superuser, over the loopback socket. The socket
# trusts the local user, so no password is put in the environment: the DSN's
# password only ever travels as a psql variable, into CREATE/ALTER ROLE.
as_superuser() {
  env -u PGPASSWORD "$PG_BIN/psql" -X -q -h "$SOCKET_DIR" -p "$PGPORT" -U "$PGUSER" "$@"
}

# The role, the database, and the two extensions the migrations expect. Creating
# an extension needs rights the application role deliberately does not have,
# which is why this does not run as the DSN's role.
provision() {
  local sql
  sql="$(mktemp)"
  {
    echo "SELECT format('CREATE ROLE %I LOGIN', :'role')"
    echo "  WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \\gexec"
    if [ -n "$PGPASSWORD" ]; then
      echo "SELECT format('ALTER ROLE %I PASSWORD %L', :'role', :'pass') \\gexec"
    fi
    echo "SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')"
    echo "  WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \\gexec"
  } >"$sql"

  say "provisioning role and database"
  if ! as_superuser -d postgres -v ON_ERROR_STOP=1 -v role="$PGUSER" -v db="$PGDATABASE" \
    -v pass="$PGPASSWORD" -f "$sql" >/dev/null; then
    rm -f "$sql"
    die "could not create the role or database on the cluster in $(rel "$PGDATA_DIR")"
  fi
  rm -f "$sql"

  if ! as_superuser -d "$PGDATABASE" -v ON_ERROR_STOP=1 >/dev/null 2>&1 <<'EOF'
SET client_min_messages = warning;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
EOF
  then
    die "could not create the vector/pg_trgm extensions in '${PGDATABASE}'; check $(rel "$PG_LOG")"
  fi
}

# --- commands ----------------------------------------------------------------

do_up() {
  # The order the README documents, and the one a machine with both Docker and
  # a local cluster needs: an existing database first — left alone — then
  # Docker, then a local install. Trying Docker first would race a running
  # local cluster for the bind, leave a dead container behind, and warn about
  # "falling back to a local install" on a machine that already has one.
  if port_open "$PGHOST" "$PGPORT"; then
    if can_connect; then
      say "postgres is already up on ${PGHOST}:${PGPORT}"
      return 0
    fi
    # Our own cluster, but the role or the database went missing: put them back.
    if find_pg_bin && cluster_running && [ "$(own_cluster_port || true)" = "$PGPORT" ]; then
      provision
      can_connect ||
        die "postgres is running in $(rel "$PGDATA_DIR") but ${PGUSER} still cannot log in over TCP; check $(rel "$PG_LOG")"
      say "postgres is already up on ${PGHOST}:${PGPORT} (role and database repaired)"
      return 0
    fi
    # Not ours. Say so, rather than fighting it for the bind and failing later
    # with a socket error.
    die "something is already listening on ${PGHOST}:${PGPORT}, but ${PGUSER} cannot log in to
   the database '${PGDATABASE}' there. Either fix that role, or point ETHOS_DSN
   at a port nothing is using."
  fi

  if docker_usable; then
    if start_docker_db && port_open "$PGHOST" "$PGPORT"; then
      say "postgres is up (docker)"
      return 0
    fi
    warn "docker could not provide postgres; setting up a local postgres ${PG_MAJOR} instead"
  elif have docker; then
    warn "docker is installed but its daemon is not running; using a local postgres"
  else
    say "docker not found; setting up a local postgres ${PG_MAJOR} instead"
  fi

  ensure_postgres_install
  ensure_pgvector
  [ -f "$PGDATA_DIR/PG_VERSION" ] || init_cluster
  start_cluster
  provision

  can_connect || die "provisioned, but ${PGUSER} still cannot log in over TCP; check $(rel "$PG_LOG")"
  say "postgres ready: ${PGUSER}@${PGHOST}:${PGPORT}/${PGDATABASE} with pgvector ${PGVECTOR_VERSION}"
}

do_stop() {
  if find_pg_bin && cluster_running; then
    say "stopping postgres"
    "$PG_BIN/pg_ctl" -D "$PGDATA_DIR" -m fast -w stop >/dev/null
  else
    say "no local postgres cluster to stop"
  fi
}

do_status() {
  if find_pg_bin; then
    if cluster_running; then
      echo "postgres ${PG_MAJOR} cluster: running ($(rel "$PGDATA_DIR"))"
    else
      echo "postgres ${PG_MAJOR} cluster: stopped ($(rel "$PGDATA_DIR"))"
    fi
  else
    echo "no local postgres ${PG_MAJOR} install found"
  fi
  if port_open "$PGHOST" "$PGPORT"; then
    echo "${PGHOST}:${PGPORT}: accepting connections"
  else
    echo "${PGHOST}:${PGPORT}: nothing listening"
  fi
}

case "${1:-up}" in
up)
  do_up
  ;;
stop)
  do_stop
  ;;
status)
  do_status
  ;;
*)
  die "usage: $0 [up|stop|status]"
  ;;
esac
