.PHONY: install up db-up db-down db-status migrate seed doctor run run-dev status stop logs test test-unit test-integration test-scenarios test-bridge lint interface-install interface-build interface-dev interface tui agent clean

PY := .venv/bin/python

# `ethos up` is the recommended way to start the agent, and it is this target's
# whole job — creating the virtualenv first when there is not one yet, so `make
# up` works on a fresh clone the way `./start.sh` still does. `pip install -e .`
# in any interpreter, then `ethos up`, is the same thing without make.
.venv/bin/python:
	$(MAKE) install

up: .venv/bin/python
	$(PY) -m ethos up

install:
	uv venv .venv --python 3.12 || true
	uv pip install --python .venv/bin/python -e ".[dev]"

db-up:
	scripts/ensure_postgres.sh up

db-down:
	scripts/ensure_postgres.sh stop

db-status:
	scripts/ensure_postgres.sh status

migrate:
	$(PY) -m ethos db upgrade

seed:
	$(PY) -m ethos db seed

doctor:
	$(PY) -m ethos doctor

# The seven-process model in the foreground, for working on the agent itself.
run:
	$(PY) -m ethos supervisor

# Everything in one process, for working on the agent's own loop. It is also the
# only process, so it is the one that holds the channel doors open — which is
# what makes this the run to use when trying Telegram or Discord from a phone
# without starting the whole model.
run-dev:
	$(PY) -m ethos core --listen-channels

# Liveness and shutdown for the supervisor `ethos up` (or `make up`) starts.
status:
	$(PY) -m ethos status

stop:
	$(PY) -m ethos stop

logs:
	$(PY) -m ethos logs

test:
	$(PY) -m pytest tests/ -q

test-unit:
	$(PY) -m pytest tests/unit -q

# The bridge's own routes, over real HTTP and with no database. Separate because
# it needs the bridge's dependencies installed, which the Python suites do not.
test-bridge:
	npm test --prefix web

test-integration:
	$(PY) -m pytest tests/integration -q

test-scenarios:
	$(PY) -m pytest tests/scenarios -q

lint:
	$(PY) -m ruff check ethos tests
	# Formatting is not part of lint: the tree is not format-clean, and a
	# `--check` that cannot fail would print a thousand lines of diff and
	# still exit 0 — a lint run nobody can read the result of. Run
	# `ruff format ethos tests` deliberately when you want the reformat.

# interface/ is the interface: a browser page and a terminal client for this agent.
interface-install:
	npm install --prefix interface

interface-build:
	npm run build --workspace @project-phone/core --prefix interface
	npm run build --workspace @project-phone/web --prefix interface

# The Vite dev server, for working on the interface itself. It proxies /api and
# /events to the bridge on 8720; run the bridge separately for it to have
# something to talk to.
interface-dev:
	npm run dev --workspace @project-phone/web --prefix interface

tui:
	npm run tui --workspace @project-phone/cli --prefix interface

# The interface bridge on its own, with no agent behind it. `make up` starts this
# as part of the seven-process model; run it directly only when working on the
# bridge itself, and remember it will happily serve the agent's last known state.
agent:
	node web/server/index.mjs

# `data/home` is only removed for a checkout that still points `ETHOS_HOME` at it;
# the default home is `~/.ethos`, which `clean` deliberately leaves alone.
clean:
	rm -rf .venv web/node_modules data/home .pytest_cache
	rm -rf interface/node_modules interface/packages/*/node_modules interface/packages/*/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
