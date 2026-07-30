# Tools → Web Tools (`WebSearchTool`)

Search the web and fetch URL content. Requires a configured `SEARCH_API_KEY`.

## `web_search(query, ...)`

Run a web search for `query`.

- Uses the search provider configured by `config.search_api_key`.
- Returns a list of result snippets/links.
- If no search key is configured, reports that search is unavailable.

## `fetch_url(url, ...)`

Fetch and return the content of a `url`.

- Used to read pages the agent finds via search or is pointed to.
- Returns page text/content; reports errors (unreachable, non-200, etc.) clearly.

## Prompt-injection exposure

Because the agent can fetch arbitrary pages, **malicious web content could try to
steer its actions**. The LLM settings lock limits *model* switching, but not tool
use — treat fetched content as untrusted. See [Safety](../operations/safety.md) and
[Known Limitations](../operations/known-limitations.md).

See also: [Configuration Reference](../CONFIGURATION.md) (`SEARCH_API_KEY`),
[Tools Overview](overview.md).
