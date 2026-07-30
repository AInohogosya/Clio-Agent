# Core → Retry Helper (`core/retry.py`)

`retry_async` is the single retry-with-backoff mechanism behind the agent's
"never give up on a transient failure — retry the same thing up to N times"
behavior. It is used by the LLM router (network blips / timeouts / provider
overload) and the shell tool (transient command timeouts).

## Signature

```python
await retry_async(
    action,                       # zero-arg callable returning a *fresh* awaitable
    max_attempts=5,               # total attempts (first try + retries)
    retryable_exceptions=(Exception,),
    base_delay=1.0,               # seconds before first retry
    max_delay=30.0,               # upper bound for the back-off
    backoff=2.0,                  # multiplier after each failure
    label="operation",            # used in log messages
)
```

## Behavior

1. Calls `action()` once per attempt. `action` must be safe to call repeatedly
   (e.g. a closure that performs the real call).
2. On a **retryable** exception, logs a warning and sleeps
   `min(delay, max_delay)`, then `delay *= backoff`.
3. The first attempt that returns without raising wins.
4. If every attempt fails, the **last** exception is re-raised.
5. Any **non-retryable** exception propagates immediately, without sleeping.

## Defaults

| Constant | Value |
|----------|-------|
| `DEFAULT_MAX_ATTEMPTS` | `5` |
| `DEFAULT_BASE_DELAY` | `1.0` |
| `DEFAULT_MAX_DELAY` | `30.0` |
| `DEFAULT_BACKOFF` | `2.0` |

## Example

```python
result = await retry_async(
    lambda: llm_router.chat(messages),
    retryable_exceptions=(asyncio.TimeoutError, aiohttp.ClientError),
    label="llm chat",
)
```

Note: `max_attempts < 1` raises `ValueError`. The LLM router sets
`max_chat_attempts = 5` (its own equivalent) and disables retries by setting it to
`1`.

See also: [LLM Router](llm-router.md), [Tools → Shell Tool](../tools/shell-tools.md).
