from __future__ import annotations

import httpx

from ethos.gateway.normalizer import (
    build_name_map,
    messages_to_openai_compat,
    reasoning_from_openai_compat,
    response_from_openai_compat,
    was_truncated,
)
from ethos.gateway.providers.base import Provider, ProviderError


class OpenAICompatProvider(Provider):
    """OpenAI-compatible adapter: OpenRouter, NVIDIA NIM, local vLLM, DeepSeek, Groq."""

    name = "openai_compat"

    def __init__(self, api_key: str | None, base_url: str, quirks, http: httpx.AsyncClient, timeout_s: float = 180.0):
        super().__init__(api_key, base_url, quirks, http, timeout_s)
        # Which models on this endpoint have taken the reasoning parameter. There
        # is no way to know before asking, and asking wrong is cheap here only
        # because the gateway retries: a 400 is not retryable in itself, so the
        # error travels up, the loop comes back round, and by then this knows.
        #
        # Remembered per model, not per endpoint, because the answer is not a
        # property of the endpoint. OpenRouter serves reasoning and non-reasoning
        # models from one host and refuses the parameter for the second kind, so
        # a single per-endpoint flag would throw away the bound for the first
        # kind the moment anybody asked the second one. Endpoints that predate
        # the parameter ignore what they do not know, which costs nothing.
        self._no_reasoning: set[str] = set()
        # Which models this endpoint has been seen to reason by default: a reply
        # that was reasoning from start to truncation, with no answer in it and
        # no bound in the request that could have stopped it. Dropping the
        # refused `reasoning` parameter leaves thinking unbounded, and on an
        # endpoint like NVIDIA NIM whose reasoning models think by default that
        # means the whole of `max_tokens` is spent on thought and the answer
        # never arrives -- which is exactly the failure that looked like the
        # agent "keeps failing" while its chat worked. The one lever left is
        # the model's own template toggle, and the next attempt sends it.
        self._default_thinkers: set[str] = set()
        # And the models that refused the toggle itself, where sending it again
        # would turn every later call into a 400 for nothing. The fallback is
        # then the retry loop's own escalation of `max_tokens` -- worse, but a
        # lever that exists.
        self._no_template_kwargs: set[str] = set()

    def available(self) -> bool:
        if self.api_key:
            return True
        try:
            from urllib.parse import urlsplit

            host = urlsplit(self.base_url).hostname or ""
            if host.rstrip(".").lower() in ("localhost",) or host.rstrip(".").lower().endswith(".localhost"):
                return True
            import ipaddress

            return ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        return headers

    def _refuses_parameter(self, exc: ProviderError, parameter: str) -> bool:
        """Whether a 400 is the endpoint saying it has no such parameter."""
        if exc.status != 400:
            return False
        text = str(exc).lower()
        if parameter not in text:
            return False
        return any(word in text for word in (
            "unsupported", "not support", "unknown", "unrecognized", "unrecognised",
            "unexpected", "invalid", "extra", "not allowed", "does not",
        ))

    @staticmethod
    def _spent_it_all_thinking(data: dict) -> bool:
        """Whether this reply was reasoning from start to truncation, with no answer.

        The signature of a model that thinks by default with no bound in effect:
        reasoning content present, `content` empty, and the provider's own word
        for having run out of output room. An answer, or reasoning that ended of
        its own accord, is a model behaving and not one that needs reining in.
        """
        choices = data.get("choices") or []
        if not choices:
            return False
        message = choices[0].get("message") or {}
        if (message.get("content") or "").strip():
            return False
        if not (reasoning_from_openai_compat(message) or "").strip():
            return False
        return was_truncated(choices[0].get("finish_reason"))

    async def complete(self, request, model: str):
        payload = messages_to_openai_compat(request, model, self.quirks)
        if model in self._no_reasoning:
            payload.pop("reasoning", None)
        # Read after the pop: a model whose bound was refused has none in
        # effect, which is the situation the toggle below exists for.
        bounded = "reasoning" in payload
        if model in self._default_thinkers and not bounded and model not in self._no_template_kwargs:
            payload["chat_template_kwargs"] = {"thinking": False}
        try:
            data = await self.post_json(
                f"{self.base_url}/chat/completions", self._headers(), payload
            )
        except ProviderError as exc:
            if "reasoning" in payload and self._refuses_parameter(exc, "reasoning"):
                self._no_reasoning.add(model)
            if "chat_template_kwargs" in payload and self._refuses_parameter(exc, "chat_template_kwargs"):
                self._no_template_kwargs.add(model)
            raise
        # A reply of reasoning and nothing else, with no bound in the request,
        # is the model reporting that it thinks by default. Remembered so the
        # next attempt for this model carries the toggle: the service's own
        # lever -- halving a reasoning budget -- has nothing to halve here.
        if not bounded and model not in self._no_template_kwargs and self._spent_it_all_thinking(data):
            self._default_thinkers.add(model)
        return response_from_openai_compat(data, self.name, model, build_name_map(request.tools))
