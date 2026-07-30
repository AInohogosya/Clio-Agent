"""
Regression test for the tenfold increase of the LLM response time limit.

Originally a single completion request was capped at ``aiohttp.ClientTimeout(total=120)``
and the overall per-message watchdog at ``MESSAGE_PROCESS_TIMEOUT = 360``.
Both were raised tenfold (to 1200s and 3600s respectively) so a slow-but-alive
provider is never cut off mid-response by the interface watchdog.

This test pins those values so the change is not silently reverted.
"""

from clio_agent_2.core.agent import MESSAGE_PROCESS_TIMEOUT
from clio_agent_2.core.llm_router import LLM_REQUEST_TIMEOUT


def test_per_attempt_llm_timeout_is_tenfold():
    assert LLM_REQUEST_TIMEOUT == 1200.0  # 10x the original 120s


def test_message_process_timeout_is_tenfold():
    assert MESSAGE_PROCESS_TIMEOUT == 3600.0  # 10x the original 360s


def test_provider_completion_uses_raised_timeout():
    """Every chat_completion must derive its timeout from the raised constant."""
    import pathlib

    source = pathlib.Path(
        "clio_agent_2/core/llm_router.py"
    ).read_text(encoding="utf-8")
    # The per-attempt timeout must read from kwargs with the raised default
    # rather than the hardcoded original 120. ``list_models`` may keep its
    # lighter 60s timeout.
    assert "LLM_REQUEST_TIMEOUT" in source
    assert 'kwargs.get("request_timeout", LLM_REQUEST_TIMEOUT)' in source
    assert "aiohttp.ClientTimeout(total=120)" not in source
