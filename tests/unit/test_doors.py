"""
The doors, and the one place a person can hand the agent a credential for one.

`config/channels.yaml` says which doors exist and names the *variable* each
credential may be in, which is the right shape for a deployment with an
`EnvironmentFile` and the wrong one for somebody who installed the agent on a
laptop: there was no way to give this agent a Telegram token except a shell, and
the settings screen had no field for one.

So a door is configured from two files — the shipped one and an overlay in the
agent's own home, merged key by key — and a credential is read from the
environment first and from the file second. These tests are about the four ways
those two could go wrong: an overlay that silently switches the other five doors
off, a credential that stops being read, a home that cannot be written being
fatal, and a door that is on and admits nobody looking ready.
"""

from __future__ import annotations

import pytest

from ethos.config import channels_path, load_config, people_path


def write_overlay(home, name: str, body: str) -> None:
    path = home / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def config_for():
    """The shipped configuration, with the agent's home overlaid on it.

    Read through `ETHOS_HOME` rather than by passing the home, because that is
    how every process reads it: an interface writes to the home the environment
    names, and the agent reads the home the environment names. Passing one here
    and setting the other would test a configuration nothing runs.
    """
    return load_config("config")


@pytest.fixture(autouse=True)
def agent_home(tmp_path, monkeypatch):
    """Every test in this file is about one home, and it is a temporary one."""
    monkeypatch.setenv("ETHOS_HOME", str(tmp_path))
    for name in ("TELEGRAM_BOT_TOKEN", "SLACK_BOT_TOKEN", "DISCORD_BOT_TOKEN", "ETHOS_WEB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


# ------------------------------------------------------------- the home overlay


def test_a_door_can_be_opened_from_the_agent_own_home(agent_home):
    write_overlay(agent_home, "channels.yaml", "telegram:\n  enabled: true\n  token: '123:abc'\n")
    channels = config_for().channels
    assert channels.telegram.enabled is True
    assert channels.credentials()["telegram.token"] == "123:abc"


def test_the_overlay_says_nothing_about_the_doors_it_does_not_name(agent_home):
    """
    The failure this shape exists to prevent.

    A home file that *replaced* the shipped one would have to carry every door to
    be right, so writing one Telegram token into it would switch off WhatsApp,
    Slack, Discord and the webhook receiver — and an agent would go quiet on four
    channels that were working a moment ago, with nothing in the file to say so.
    """
    write_overlay(agent_home, "channels.yaml", "telegram:\n  enabled: true\n")
    channels = config_for().channels
    assert channels.telegram.enabled is True
    assert channels.whatsapp.enabled is False
    assert channels.slack.enabled is False
    assert channels.discord.enabled is False
    assert channels.email.enabled is False
    assert channels.web.enabled is True, "the local line is not a door anybody can switch off here"
    assert channels.web.http_port == 8720, "and its port is the shipped one"


def test_the_shipped_file_still_configures_a_deployment(agent_home):
    """
    Nothing is lost for a deployment that keeps its keys in the environment.

    The overlay is read *after* the shipped file, so a hand-edited
    `config/channels.yaml` is still the deployment's own — which is the whole
    reason the shipped file is left alone rather than moved into the home.
    """
    config = config_for()
    assert config.channels.telegram.token_env == "TELEGRAM_BOT_TOKEN"
    assert config.channels.slack.allowed_ids == []
    assert config.channels.discord.intents == 37376


def test_a_home_file_that_will_not_parse_falls_back_rather_than_refusing(agent_home):
    """
    Forgiving, like every other file the interfaces write.

    A half-written or hand-edited document is a normal thing to find, and
    answering it by refusing to start would take the whole life loop out over a
    stray tab in a file nobody is watching. Every door off is a state a person
    can see on the settings screen and fix.
    """
    write_overlay(agent_home, "channels.yaml", "telegram: [this is not a mapping\n")
    config = config_for()
    assert config.channels.telegram.enabled is False
    assert config.channels.web.enabled is True


def test_an_address_can_be_given_without_rewriting_the_contact_book(agent_home):
    write_overlay(
        agent_home,
        "people.yaml",
        'people:\n  - id: owner\n    channels:\n      telegram: "tg:819012345678"\n',
    )
    people = {person.id: person.channels for person in config_for().people.people}
    assert people["owner"]["telegram"] == "tg:819012345678"
    # The shipped entry is still there, because this is an overlay.
    assert people["owner"]["web"] == "owner"


def test_an_address_can_be_given_up(agent_home):
    """A `null` address is a removal — the one way to hand a door over."""
    write_overlay(
        agent_home,
        "people.yaml",
        'people:\n  - id: owner\n    channels:\n      telegram: null\n',
    )
    people = {person.id: person.channels for person in config_for().people.people}
    assert "telegram" not in people["owner"]
    assert people["owner"]["web"] == "owner"


def test_the_paths_are_the_ones_an_interface_writes(agent_home):
    """Both files live in the agent's own home, which is the point of them."""
    assert channels_path(agent_home).name == "channels.yaml"
    assert people_path(agent_home).name == "people.yaml"
    assert channels_path(agent_home).parent == agent_home


# ------------------------------------------------------------- which credential


def test_the_environment_wins_over_the_file(agent_home, monkeypatch):
    """
    A deployment that exports its keys keeps working.

    The environment first is not a preference: `ethos-comms` and the interface
    bridge are separate processes, and a key in the environment of one is the only
    kind that can be rotated without rewriting a file. A person who pastes a token
    into a settings screen afterwards must not silently take that away.
    """
    write_overlay(agent_home, "channels.yaml", "telegram:\n  token: 'from-the-file'\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-the-environment")
    assert config_for().channels.credentials()["telegram.token"] == "from-the-environment"


def test_a_file_credential_is_used_when_there_is_no_variable(agent_home):
    write_overlay(agent_home, "channels.yaml", "telegram:\n  token: 'from-the-file'\n")
    assert config_for().channels.credentials()["telegram.token"] == "from-the-file"


def test_a_credential_that_is_nowhere_is_absent_rather_than_empty(agent_home):
    """
    The distinction the warnings in the comms host are made of.

    An empty string is a value the adapter would try to use, and "this deployment
    has no token" is a fact it can refuse. The answer is a key that is not there.
    """
    creds = config_for().channels.credentials()
    assert "telegram.token" not in creds
    assert config_for().channels.missing_credentials("telegram") == ["telegram.token"]


def test_one_rule_serves_every_door(agent_home):
    """
    Eight lookups written eight times is eight chances to disagree about which of
    two places a token wins, and a door whose sending half and listening half
    disagree is a channel that receives nothing.
    """
    write_overlay(
        agent_home,
        "channels.yaml",
        "whatsapp:\n  phone_number_id: '1'\n  access_token: '2'\n  app_secret: '3'\n  verify_token: '4'\n"
        "slack:\n  bot_token: 'xoxb-1'\n  signing_secret: 's'\n"
        "discord:\n  token: 'd'\n"
        "email:\n  address: 'a@b.c'\n  password: 'p'\n  imap_host: 'imap'\n  smtp_host: 'smtp'\n",
    )
    channels = config_for().channels
    assert channels.missing_credentials("whatsapp") == []
    assert channels.missing_credentials("slack") == []
    assert channels.missing_credentials("discord") == []
    assert channels.missing_credentials("email") == []
    assert set(channels.credentials()) >= {
        "whatsapp.app_secret", "slack.signing_secret", "discord.token",
        "email.imap_host", "email.smtp_host",
    }


# ------------------------------------------------------------------- reporting


def test_doctor_can_say_why_a_door_is_silent(agent_home):
    """
    The question a person runs `ethos doctor` to ask.

    A channel that is configured in a file nobody has opened and is not working
    is exactly the failure a health check exists to name — and a bot that answers
    `/id` and nothing else is the most misleading state there is, so an empty
    allowlist is said out loud rather than left to be discovered.
    """
    report = config_for().channels.door_report(agent_home)
    assert "telegram: off" in report
    assert any(line.startswith("door config:") and "not written yet" in line for line in report)

    write_overlay(agent_home, "channels.yaml", "telegram:\n  enabled: true\n  token: '1:2'\n")
    report = config_for().channels.door_report(agent_home)
    assert "telegram: on, but allowed_chat_ids is empty — it will answer nobody" in report

    write_overlay(
        agent_home,
        "channels.yaml",
        "telegram:\n  enabled: true\n  token: '1:2'\n  allowed_chat_ids: [819012345678]\n",
    )
    assert "telegram: on, admitting 1" in config_for().channels.door_report(agent_home)


def test_a_push_door_with_no_receiver_is_said_to_be_deaf(agent_home):
    """
    WhatsApp has no poller at all: with no listener there is nowhere for Meta to
    POST, so the door is on, has four credentials, and is sent nothing.
    """
    write_overlay(
        agent_home,
        "channels.yaml",
        "whatsapp:\n  enabled: true\n  phone_number_id: '1'\n  access_token: '2'\n"
        "  app_secret: '3'\n  verify_token: '4'\n  allowed_phone_numbers: ['819012345678']\n",
    )
    report = config_for().channels.door_report(agent_home)
    assert "whatsapp: on, but there is no webhook for it to arrive on" in report


def test_a_door_told_to_answer_anybody_says_so_rather_than_answering_nobody(agent_home):
    """The one line in the report that would otherwise be wrong.

    Every door below reports the size of its allowlist, and an empty one is the
    "will answer nobody" warning. A door that has been deliberately opened to
    everyone has the same empty list, so without this the health check tells an
    operator to go and configure a filter on a channel that is already working —
    and `doctor` is the command somebody runs when they think something is wrong.
    """
    write_overlay(
        agent_home,
        "channels.yaml",
        "telegram:\n  enabled: true\n  token: '1:2'\n  accept_from_anyone: true\n",
    )
    report = config_for().channels.door_report(agent_home)
    assert "telegram: on, admitting anyone who writes to it" in report
    assert not any("answer nobody" in line and line.startswith("telegram") for line in report)


def test_opening_a_door_to_anybody_is_opt_in_on_every_door(agent_home):
    """The default has to be the narrow one on all four, not just Telegram.

    Four models, four `allowlist` fields and four defaults that are each written by
    hand — and a new door added by copy-paste is a new door that quietly defaults
    to answering strangers, which for an agent holding a shell is a published
    endpoint rather than a convenience.
    """
    config = config_for()
    for door in ("telegram", "whatsapp", "slack", "discord"):
        assert getattr(config.channels, door).accept_from_anyone is False, door


def test_opening_a_door_to_anybody_reaches_the_adapter(agent_home):
    """The setting as config is not the setting in force.

    `CommsHost` builds every adapter from this file, and it is the adapter that
    decides whether to answer. A flag that exists on the config model and is never
    passed is a flag that looks like it works.
    """
    import asyncio

    from ethos.comms.host import CommsHost

    write_overlay(
        agent_home,
        "channels.yaml",
        "telegram:\n  enabled: true\n  token: '1:2'\n  accept_from_anyone: true\n",
    )
    config = config_for()
    config.channels.cli.enabled = False
    config.channels.web.enabled = False

    async def on_inbound(_message) -> None:
        return None

    host = CommsHost(config, on_inbound)
    host.build_adapters()
    telegram = host.adapters["telegram"]
    assert telegram.accept_from_anyone is True

    class Chat:
        id = 4242
        type = "private"

    assert telegram.accepts(Chat()) is True
    # And with the flag off and a list empty, the same door is mute — which is the
    # state the flag exists to distinguish from the one above.
    telegram.accept_from_anyone = False
    assert telegram.accepts(Chat()) is False
    assert asyncio.iscoroutinefunction(on_inbound)
