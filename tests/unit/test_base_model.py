"""
The base model: the one model a person can hand the agent directly.

`config/models.yaml` is a routing table over several models, each needing its own
key in the environment. A laptop with one key has no way to express that, so the
agent also accepts a single pinned model in its own home. These tests are about
the two things that could go wrong with it: a pin that does not actually win, and
a file that can point the agent somewhere it must not post its reasoning to.
"""

from __future__ import annotations

import pytest

from ethos.config import (
    BaseModelConfig,
    ModelEntry,
    ModelsConfig,
    base_model_path,
    is_valid_base_url,
    load_base_model,
    load_config,
)
from ethos.gateway.cost import CostCalculator
from ethos.gateway.router import ModelRouter
from ethos.gateway.service import GatewayService
from ethos.schemas.models import Tier
from tests.conftest import simple_request


def write_base_model(home, body: str) -> None:
    path = base_model_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


VALID = """
provider: openai_compat
model: some/model-id
base_url: https://api.example.com/v1
api_key: sk-example
"""


# ------------------------------------------------------------------ reading it


def test_a_configured_base_model_is_read(tmp_path):
    write_base_model(tmp_path, VALID)
    base = load_base_model(tmp_path)
    assert base is not None
    assert base.model == "some/model-id"
    assert base.api_key == "sk-example"


def test_no_file_is_no_base_model(tmp_path):
    assert load_base_model(tmp_path) is None


def test_the_base_model_goes_in_front_of_the_catalogue():
    models = ModelsConfig(base=BaseModelConfig(provider="openai_compat", model="pinned/id"))
    resolved = models.resolved()
    assert resolved[0].model == "pinned/id"
    assert resolved[0].pinned
    assert len(resolved) == 1


def test_resolving_prepends_to_the_catalogue():
    """
    `resolved()` is a pure function of `base` and `models`, and it is the
    *service* that keeps `models` from ever holding a resolved list. Asserting
    that here would only re-state the caller, so the growth test lives with the
    caller, in `test_a_base_model_written_while_running_is_picked_up`.
    """
    catalogue = [ModelEntry(provider="openai", model="a", tiers=["T1"], quality=0.9,
                            latency_s=1.0, rpm=1, tpm=1)]
    models = ModelsConfig(base=BaseModelConfig(provider="openai_compat", model="pinned/id"), models=catalogue)
    resolved = models.resolved()
    assert [entry.model for entry in resolved] == ["pinned/id", "a"]
    assert resolved[0].pinned and not resolved[1].pinned


def test_it_loads_with_the_rest_of_the_configuration(tmp_path, monkeypatch):
    write_base_model(tmp_path, VALID)
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    config = load_config("config")
    assert config.models.base is not None
    assert config.models.models[0].model == "some/model-id"


# -------------------------------------------------- what it refuses to be made


@pytest.mark.parametrize(
    "body",
    [
        # Not YAML at all.
        "provider: [unclosed\n  broken: {",
        # YAML, but not a mapping of what a base model needs.
        "- a\n- list\n",
        "model: 42",
        "",
    ],
)
def test_an_unusable_file_falls_back_rather_than_refusing_to_start(tmp_path, body):
    """A typo in a file nobody is watching must not take the agent down with it."""
    write_base_model(tmp_path, body)
    assert load_base_model(tmp_path) is None


def test_a_base_model_serving_no_tier_is_refused():
    with pytest.raises(ValueError):
        BaseModelConfig(provider="openai_compat", model="m", tiers=["T9"])


def test_a_provider_with_no_adapter_is_refused():
    with pytest.raises(ValueError):
        BaseModelConfig(provider="skynet", model="m")


@pytest.mark.parametrize("model", ["", "has space", "-leading", "x" * 257, "new\nline"])
def test_an_unusable_model_name_is_refused(model):
    with pytest.raises(ValueError):
        BaseModelConfig(provider="openai_compat", model=model)


def test_model_names_vendors_actually_write_are_accepted():
    for model in (
        "claude-3-5-sonnet-latest",
        "stealth/space-bunny-alpha",
        "Qwen/Qwen2.5-32B-Instruct-AWQ",
        "gpt-4.1",
    ):
        assert BaseModelConfig(provider="openai_compat", model=model).model == model


# -------------------------------------------------------- where it may point


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://openrouter.ai/api/v1",
        "https://generativelanguage.googleapis.com/v1beta",
        "http://127.0.0.1:11434/v1",
        "http://localhost:1234/v1",
    ],
)
def test_endpoints_the_agent_may_be_given(url):
    assert is_valid_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        # Plain http to somewhere that is not this machine: the key and the
        # prompt would go out in the clear.
        "http://api.openai.com/v1",
        # Where a rewritten endpoint would aim.
        "https://169.254.169.254/latest/meta-data",
        "https://192.168.1.10/v1",
        "https://10.0.0.1/v1",
        "https://172.16.0.1/v1",
        "https://[::ffff:169.254.169.254]/latest",
        "https://evil.internal/v1",
        "https://box.local/v1",
        # So the URL that was checked is the URL that is used.
        "https://user:secret@api.openai.com/v1",
        "https://api.openai.com/v1?redirect=evil",
        "https://api.openai.com/v1#x",
        # Not an endpoint at all.
        "file:///etc/passwd",
        "not a url",
        "https://",
    ],
)
def test_endpoints_the_agent_must_refuse(url):
    assert not is_valid_base_url(url)


def test_a_refused_endpoint_never_reaches_the_configuration(tmp_path):
    write_base_model(tmp_path, "provider: openai_compat\nmodel: m\nbase_url: https://169.254.169.254/\n")
    assert load_base_model(tmp_path) is None


# ------------------------------------------------------------------- routing


def test_a_pinned_model_is_the_answer_for_every_tier():
    """The whole point: one model with a key, not the catalogue's best guess."""
    config = load_config("config")
    config.models.base = BaseModelConfig(
        provider="openai_compat", model="pinned/id", quality=0.10, latency_s=99.0,
    )
    config.models.models = config.models.resolved()
    router = ModelRouter(config, CostCalculator(config))
    for tier in (Tier.T1, Tier.T2, Tier.T3):
        assert router.select(simple_request(tier=tier), is_available=lambda e: True).model == "pinned/id"


def test_a_pin_still_has_to_be_able_to_do_what_was_asked():
    """Pinned is a preference, not an override: a model with no tools is not used."""
    config = load_config("config")
    config.models.base = BaseModelConfig(
        provider="openai_compat", model="pinned/id", supports_tools=False,
    )
    config.models.models = config.models.resolved()
    router = ModelRouter(config, CostCalculator(config))
    request = simple_request(tier=Tier.T1)  # carries tools
    assert request.tools, "this test is only meaningful for a request that needs them"
    assert router.select(request, is_available=lambda e: True).model != "pinned/id"


def test_a_pin_that_cannot_think_is_skipped_for_one_that_can():
    config = load_config("config")
    config.models.base = BaseModelConfig(
        provider="openai_compat", model="pinned/id", supports_thinking=False,
    )
    config.models.models = config.models.resolved()
    router = ModelRouter(config, CostCalculator(config))
    # T3, because T1 is where the shipped catalogue has nothing that thinks.
    entry = router.select(
        simple_request(tier=Tier.T3, thinking_budget=1024), is_available=lambda e: True,
    )
    assert entry.model != "pinned/id"
    assert entry.supports_thinking


def test_an_unavailable_pin_falls_through_to_the_catalogue():
    config = load_config("config")
    config.models.base = BaseModelConfig(provider="openai_compat", model="pinned/id")
    config.models.models = config.models.resolved()
    router = ModelRouter(config, CostCalculator(config))
    entry = router.select(
        simple_request(tier=Tier.T1), is_available=lambda e: not e.pinned,
    )
    assert not entry.pinned


# -------------------------------------------------------------- the gateway


def test_the_key_comes_from_the_file_when_there_is_no_variable(tmp_path, monkeypatch):
    write_base_model(tmp_path, VALID)
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    monkeypatch.delenv("PINNED_API_KEY", raising=False)
    service = GatewayService(load_config("config"), providers={})
    assert service._provider_for(service.config.models.models[0]).api_key == "sk-example"


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch):
    """A variable set before the process starts is a more deliberate thing."""
    write_base_model(
        tmp_path,
        "provider: openai_compat\nmodel: m\napi_key: from-file\napi_key_env: PINNED_API_KEY\n",
    )
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    monkeypatch.setenv("PINNED_API_KEY", "from-env")
    service = GatewayService(load_config("config"), providers={})
    assert service._provider_for(service.config.models.models[0]).api_key == "from-env"


def test_a_base_model_written_while_running_is_picked_up(tmp_path, monkeypatch):
    """Otherwise the setting could not be changed from the page that found it wrong."""
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    service = GatewayService(load_config("config"), providers={})
    assert service.config.models.base is None
    catalogue = len(service.config.models.models)

    write_base_model(tmp_path, VALID)
    service.sync_base_model()
    assert service.config.models.base is not None
    assert service.config.models.models[0].model == "some/model-id"
    assert len(service.config.models.models) == catalogue + 1


def test_removing_it_keeps_the_catalogue_the_same_size(tmp_path, monkeypatch):
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    service = GatewayService(load_config("config"), providers={})
    write_base_model(tmp_path, VALID)
    service.sync_base_model()
    with_base = len(service.config.models.models)

    base_model_path(tmp_path).unlink()
    service.sync_base_model()
    assert service.config.models.base is None
    assert len(service.config.models.models) == with_base - 1


def test_a_corrupt_file_leaves_the_agent_running(tmp_path, monkeypatch):
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    service = GatewayService(load_config("config"), providers={})
    write_base_model(tmp_path, VALID)
    service.sync_base_model()
    size = len(service.config.models.models)

    write_base_model(tmp_path, "provider: [unclosed\n  broken: {")
    service.sync_base_model()
    assert service.config.models.base is None
    assert len(service.config.models.models) == size - 1
    assert service.config.models.models, "the catalogue is still there"


def test_a_rewrite_of_the_same_size_is_still_picked_up(tmp_path, monkeypatch):
    """The reload is gated on a size+mtime stamp, and mtime has a resolution."""
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    service = GatewayService(load_config("config"), providers={})
    write_base_model(tmp_path, "provider: openai_compat\nmodel: aaa\napi_key: k")
    service.sync_base_model()
    assert service.config.models.models[0].model == "aaa"

    # The same number of characters, a different model. A stamp that compared
    # only sizes would keep routing to the model that was just replaced.
    write_base_model(tmp_path, "provider: openai_compat\nmodel: bbb\napi_key: k")
    service.sync_base_model()
    assert service.config.models.models[0].model == "bbb"


def test_the_status_reports_the_key_without_revealing_it(tmp_path, monkeypatch):
    write_base_model(tmp_path, VALID)
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    service = GatewayService(load_config("config"), providers={})
    status = service.model_status()
    assert status["base"]["configured"] is True
    assert status["base"]["key_present"] is True
    assert "sk-example" not in repr(status)
    assert status["catalogue"] == len(service.config.models.models) - 1


def test_a_key_in_the_environment_counts_as_a_key(tmp_path, monkeypatch):
    """A base model may name a variable and hold no key at all."""
    write_base_model(
        tmp_path,
        "provider: openai_compat\nmodel: some/model-id\napi_key_env: ETHOS_TEST_BASE_KEY\n",
    )
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    monkeypatch.setenv("ETHOS_TEST_BASE_KEY", "sk-from-the-environment")
    service = GatewayService(load_config("config"), providers={})
    status = service.model_status()
    # Reporting "no key" here would say a working install cannot think, and the
    # variable is named so a surface can say which one it is reading.
    assert status["base"]["key_present"] is True
    assert status["base"]["key_env"] == "ETHOS_TEST_BASE_KEY"
    assert "sk-from-the-environment" not in repr(status)

    # And with the variable gone, the same file is honestly a model with no
    # credential — which is what makes the first answer worth anything.
    monkeypatch.delenv("ETHOS_TEST_BASE_KEY")
    service = GatewayService(load_config("config"), providers={})
    assert service.model_status()["base"]["key_present"] is False
