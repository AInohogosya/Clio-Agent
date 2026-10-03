from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ethos.core import bootstrap

"""
What `ethos up` reports about the interface, which is what it tells a person to open.

`_prepare_interface` answers one question — will there be a page when the agent is
up — and its answer is used twice: to decide whether the start-up says "open
<url>", and to decide whether a browser is launched at it. Both were wrong for a
while, in the way this project's failures usually are: not a crash, a confident
answer that nobody checked.

The first way was a rename nobody followed. The interface moved from `Phone/` to
`interface/`, and this function kept looking for `Phone/`: `if phone.is_dir()` was
false on every checkout in the repository, so the interface was never installed and
never built by `ethos up`, and no error was raised — a directory that is not there
skips quietly. Every other step of the start-up carried on and reported success.

The second was the return value, which was `True` on any path that did not raise:
a checkout with no `interface/` in it, and a build that failed to produce an
`index.html`, both came back `True`, so `ethos up` opened a browser onto whatever
the bridge had — which is a 503 that says the interface is not built, or a 404.

So the answer is asked of the build rather than inferred from having tried.
"""


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A checkout-shaped directory: the three directories this function looks in."""
    for name in ("config", "web", "interface/packages/web/dist"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "web" / "node_modules").mkdir(exist_ok=True)
    (tmp_path / "interface" / "node_modules").mkdir(exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def never_run_npm(monkeypatch: pytest.MonkeyPatch):
    """Make any attempt to shell out to npm or a build fail loudly.

    These tests are about what the function *concludes*, so a real `npm install` or
    `vite build` would make them slow, network-dependent, and — worse — able to
    pass by actually building something. Every subprocess in the module is replaced
    by one that records the call and reports failure.

    `ETHOS_NODE` is cleared as well, and deliberately through `monkeypatch`. This
    function's real side effect is to put the Node it found in the environment for
    the supervisor to inherit, so a fake one installed here outlives the test and
    is read by the supervisor's own tests — as the command it starts the bridge
    with, which is how a leak in this file became a failure in another.
    """
    attempted: list[list[str]] = []

    def refuse(command, **kwargs):  # noqa: ANN001, ANN003
        attempted.append(list(command))
        return subprocess.CompletedProcess(command, returncode=1)

    monkeypatch.setattr(bootstrap, "_npm_install", lambda *a, **k: 1)
    monkeypatch.setattr(bootstrap.subprocess, "run", refuse)
    monkeypatch.setattr(bootstrap, "node_tool", lambda name, home=None: f"/nonexistent/{name}")
    monkeypatch.delenv("ETHOS_NODE", raising=False)
    return attempted


def test_a_built_interface_is_reported_as_built(root: Path, never_run_npm: list[list[str]]):
    (root / "interface" / "packages" / "web" / "dist" / "index.html").write_text("<!doctype html>")

    assert bootstrap._prepare_interface(root, root / "logs") is True
    assert never_run_npm == [], "a build that already exists must not be rebuilt to answer this"


def test_an_unbuilt_interface_is_not_reported_as_built(root: Path, never_run_npm):
    """No `index.html`, and a build that fails to produce one.

    This is the case that used to answer `True`: the steps ran, nothing raised, and
    the start-up opened a browser onto a page that was not there.
    """
    assert bootstrap._prepare_interface(root, root / "logs") is False
    assert never_run_npm, "it should have tried to build the interface"


def test_a_checkout_without_the_interface_is_not_reported_as_built(tmp_path: Path, never_run_npm):
    """`interface/` absent entirely — a deployment that never cloned it.

    Also used to answer `True`, which is how a headless deployment was told to open
    a browser page it has no way to serve.
    """
    (tmp_path / "config").mkdir()
    (tmp_path / "web" / "node_modules").mkdir(parents=True)

    assert bootstrap._prepare_interface(tmp_path, tmp_path / "logs") is False


def test_no_node_means_no_page_and_no_crash(root: Path, monkeypatch: pytest.MonkeyPatch):
    """Without Node the agent still runs, and the page is reported as absent."""
    monkeypatch.setattr(bootstrap, "node_tool", lambda name, home=None: None)

    assert bootstrap._prepare_interface(root, root / "logs") is False


def test_the_interface_is_looked_for_where_it_lives(root: Path, never_run_npm):
    """The regression itself: the directory this function reads is `interface/`.

    Written as a test on the name rather than on the behaviour so that renaming the
    directory again fails here, loudly, instead of skipping the interface silently.
    """
    source = Path(bootstrap.__file__).read_text()

    assert '"Phone"' not in source, "bootstrap still refers to the pre-rename directory"
    assert 'root / "interface"' in source
