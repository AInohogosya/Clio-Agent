# Clio Agent 3

## Unreleased

- Rebuilt cost accounting end to end, because the budget pane was fiction.
  Models missing from the price table — including the pinned base model and
  every `:free` route — were billed at an invented default, so free traffic
  reported spend; providers that fold cached and reasoning tokens into their
  reported totals were billed for those tokens twice; Anthropic cache writes
  were priced at nothing; and the dashboard read the ledger with a different
  status filter and day boundary than the cap it is drawn against. Prices now
  resolve per route (live provider pricing, cached, over a verified checked-in
  table, over a logged pessimistic high-water mark — no silent default), token
  counts are normalized to disjoint buckets per provider, a provider-reported
  cost is used verbatim, the guard and the pane read through one shared spend
  query, and migration `0004` reprices historical `model_calls` from their
  stored token counts. See `ethos/pricing.py` and `tests/unit/test_pricing.py`.
- Standardized the product name. The product was known variously as "Ethos",
  "Clio Agent 3 Beta" and "Agent"; it is now **Clio Agent 3** everywhere it is
  user-facing. The designation carries no stage suffix — pre-release state is
  recorded by the `3.0.0-beta.3` version in `package.json` and `pyproject.toml`,
  which is unchanged, and by this entry. The `ethos` package, `ethos` command,
  `ETHOS_*` variables, `~/.ethos` home, database and service names are
  identifiers rather than branding and are unchanged.

## v3.0.0-beta.3