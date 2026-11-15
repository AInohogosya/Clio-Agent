"""The OAuth SaaS connector broker: tokens that never enter the model's world.

A connector is how the agent uses a service that speaks OAuth — GitHub,
Linear, Slack, Google Workspace, Notion, or a deployment's own provider. The
broker holds the whole lifecycle:

1. **Authorization** — `begin_authorization` returns the URL a person clicks,
   with PKCE (S256) and a one-time state. The interactive step is theirs by
   design: OAuth exists so that consent is given by a human, and an agent that
   could grant itself scopes has an OAuth implementation for decoration only.
2. **Token custody** — the callback exchanges the code for tokens and seals
   them at rest with the space's envelope key (`connector_accounts.sealed_tokens`).
   Access and refresh tokens are decrypted inside the toolhost's execution
   path and used there; they are never returned to the model, never rendered
   into a prompt, and never written to a log.
3. **Refresh** — an expired access token is refreshed with its refresh token
   (rotating and re-sealing when the provider rotates), under a per-connector
   lock so two steps refreshing at once is one refresh and not two.
4. **Per-project scopes** — a `project_scope_json` on the account records
   which projects may use the connector and with which scopes, so a
   connector granted to one project is not automatically the other's.
5. **Isolated execution** — `authorized_request` runs in the toolhost: the
   request goes out with the token attached and only the response body comes
   back. Executing inside the broker is what makes "raw OAuth tokens never
   enter LLM context" a property of the architecture rather than a promise.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from clio.observability.logger import get_logger

logger = get_logger("clio.toolhost.connectors")


class ConnectorError(RuntimeError):
    """A connector could not be authorized, refreshed or used."""


@dataclass
class ProviderDefinition:
    """One OAuth provider: where to send the person, where to trade the code."""

    name: str
    authorize_url: str
    token_url: str
    default_scopes: list[str] = field(default_factory=list)
    scope_separator: str = " "
    extra_authorize_params: dict[str, str] = field(default_factory=dict)
    pkce: bool = True

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ProviderDefinition:
        return cls(
            name=name,
            authorize_url=str(data.get("authorize_url") or ""),
            token_url=str(data.get("token_url") or ""),
            default_scopes=[str(s) for s in data.get("default_scopes", [])],
            scope_separator=str(data.get("scope_separator") or " "),
            extra_authorize_params={str(k): str(v) for k, v in (data.get("extra_authorize_params") or {}).items()},
            pkce=bool(data.get("pkce", True)),
        )


KNOWN_PROVIDERS: dict[str, ProviderDefinition] = {
    "github": ProviderDefinition(
        name="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        default_scopes=["repo", "read:user"],
    ),
    "linear": ProviderDefinition(
        name="linear",
        authorize_url="https://linear.app/oauth/authorize",
        token_url="https://api.linear.app/oauth/token",
        default_scopes=["read", "issues:create"],
    ),
    "slack": ProviderDefinition(
        name="slack",
        authorize_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        default_scopes=["channels:read", "chat:write"],
        scope_separator=",",
    ),
    "google": ProviderDefinition(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        default_scopes=["https://www.googleapis.com/auth/gmail.readonly"],
        extra_authorize_params={"access_type": "offline", "prompt": "consent"},
    ),
    "notion": ProviderDefinition(
        name="notion",
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        default_scopes=[],
    ),
}


def providers_from_config(config: Any) -> dict[str, ProviderDefinition]:
    """Known providers, overridden and extended by `connectors.providers`."""
    providers = dict(KNOWN_PROVIDERS)
    configured = getattr(getattr(config, "connectors", None), "providers", None) or {}
    for name, data in configured.items():
        providers[str(name)] = ProviderDefinition.from_dict(str(name), dict(data or {}))
    return providers


@dataclass
class PendingAuthorization:
    provider: str
    state: str
    code_verifier: str
    redirect_uri: str
    project_id: str | None
    space_id: str
    scopes: list[str]
    created_at: float = field(default_factory=time.time)


def _pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


class ConnectorBroker:
    """Authorize, hold, refresh and spend OAuth credentials — in the toolhost."""

    def __init__(self, db: Any, envelope: Any, *, providers: dict[str, ProviderDefinition] | None = None,
                 redirect_base: str = "http://127.0.0.1:8720",
                 client_credentials: dict[str, tuple[str, str]] | None = None) -> None:
        self.db = db
        self.envelope = envelope
        self.providers = providers or dict(KNOWN_PROVIDERS)
        self.redirect_base = redirect_base.rstrip("/")
        # client_id/client_secret per provider, from the environment by
        # convention: CLIO_CONNECTOR_<PROVIDER>_CLIENT_ID/SECRET.
        self.client_credentials = dict(client_credentials or {})
        self._pending: dict[str, PendingAuthorization] = {}
        self._refresh_locks: dict[str, Any] = {}

    def client_credentials_for(self, provider: str) -> tuple[str, str]:
        if provider in self.client_credentials:
            return self.client_credentials[provider]
        from clio.env import env

        upper = provider.upper().replace("-", "_")
        client_id = env(f"CONNECTOR_{upper}_CLIENT_ID", "") or ""
        client_secret = env(f"CONNECTOR_{upper}_CLIENT_SECRET", "") or ""
        if not client_id:
            raise ConnectorError(
                f"no OAuth client for provider {provider!r}; set "
                f"CLIO_CONNECTOR_{upper}_CLIENT_ID and _CLIENT_SECRET"
            )
        return client_id, client_secret

    # ---- authorization ---------------------------------------------------------

    def begin_authorization(self, provider: str, *, project_id: str | None = None,
                            space_id: str = "primary", tenant_id: str = "local",
                            scopes: list[str] | None = None,
                            redirect_uri: str | None = None) -> dict[str, Any]:
        definition = self.providers.get(provider)
        if definition is None:
            raise ConnectorError(f"unknown connector provider {provider!r}")
        client_id, _ = self.client_credentials_for(provider)
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(24)
        redirect = redirect_uri or f"{self.redirect_base}/api/connectors/{provider}/callback"
        pending = PendingAuthorization(
            provider=provider, state=state, code_verifier=verifier,
            redirect_uri=redirect, project_id=project_id, space_id=space_id,
            scopes=list(scopes or definition.default_scopes),
        )
        self._pending[state] = pending
        params = {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect,
            "state": state,
            "scope": definition.scope_separator.join(pending.scopes),
            **definition.extra_authorize_params,
        }
        if definition.pkce:
            params["code_challenge"] = challenge
            params["code_challenge_method"] = "S256"
        return {
            "provider": provider,
            "state": state,
            "url": f"{definition.authorize_url}?{urlencode(params)}",
            "scopes": pending.scopes,
            "project_id": project_id,
        }

    async def complete_authorization(self, provider: str, code: str, state: str,
                                     *, tenant_id: str = "local") -> dict[str, Any]:
        pending = self._pending.pop(state, None)
        if pending is None or pending.provider != provider:
            raise ConnectorError("unknown or replayed OAuth state; start the authorization again")
        definition = self.providers[provider]
        client_id, client_secret = self.client_credentials_for(provider)
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": pending.redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if definition.pkce:
            payload["code_verifier"] = pending.code_verifier
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                definition.token_url, data=payload,
                headers={"Accept": "application/json"},
            )
        if response.status_code >= 400:
            raise ConnectorError(f"token exchange failed: HTTP {response.status_code} {response.text[:200]}")
        try:
            tokens = response.json()
        except ValueError as exc:
            raise ConnectorError("token endpoint answered non-JSON") from exc
        access = tokens.get("access_token")
        if not access:
            raise ConnectorError(f"token endpoint returned no access_token: {sorted(tokens)}")
        connector_id = f"conn-{provider}-{secrets.token_hex(6)}"
        sealed = await self._seal_tokens(pending.space_id, pending.project_id, connector_id, tokens)
        expires_at = None
        if tokens.get("expires_in"):
            expires_at = float(tokens["expires_in"])
        await self.db.execute(
            "INSERT INTO connector_accounts (connector_id, tenant_id, space_id, provider, "
            "display_name, sealed_tokens, scopes, project_scope_json, status, expires_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb, 'connected', "
            "CASE WHEN $9::float8 IS NULL THEN NULL ELSE now() + make_interval(secs => $9::float8) END)",
            connector_id, tenant_id, pending.space_id, provider,
            f"{provider} connection",
            json.dumps(sealed), json.dumps(pending.scopes),
            json.dumps({pending.project_id: pending.scopes} if pending.project_id else {}),
            expires_at,
        )
        logger.info("connectors.authorized", provider=provider, connector=connector_id)
        return {"connector_id": connector_id, "provider": provider,
                "scopes": pending.scopes, "project_id": pending.project_id}

    # ---- token custody ------------------------------------------------------------

    async def _seal_tokens(self, space_id: str, project_id: str | None, connector_id: str,
                           tokens: dict[str, Any]) -> dict[str, Any]:
        """The token payload, sealed; the row stores this dict, never plaintext."""
        payload = {
            "access_token": tokens.get("access_token"),
            "refresh_token": tokens.get("refresh_token"),
            "token_type": tokens.get("token_type", "Bearer"),
            "scope": tokens.get("scope"),
            "obtained_at": time.time(),
        }
        sealed = await self.envelope.seal(
            json.dumps(payload), space_id, aad=f"connector:{connector_id}",
        )
        # The envelope names its space; the row keeps the space beside it so a
        # reader never has to guess which key opens which account.
        return {"space_id": space_id, "envelope": sealed}

    async def _open_tokens(self, row: Any) -> dict[str, Any]:
        sealed = row["sealed_tokens"]
        if isinstance(sealed, str):
            sealed = json.loads(sealed)
        space_id = sealed.get("space_id") or row.get("space_id") or "primary"
        raw = await self.envelope.open(sealed["envelope"], space_id)
        return json.loads(raw)

    async def access_token(self, connector_id: str, *, project_id: str | None = None,
                           tenant_id: str = "local") -> str:
        """A live access token, refreshed if needed — used inside the toolhost.

        This is the only door to a token, and it never hands one to a model:
        callers are the broker's own `authorized_request` and tool executors
        running in the toolhost process. A connector scoped away from this
        project refuses here, before a token is even unwrapped.
        """
        row = await self.db.fetchrow(
            "SELECT connector_id, provider, space_id, tenant_id, sealed_tokens, scopes, "
            "project_scope_json, status, expires_at FROM connector_accounts "
            "WHERE connector_id = $1 AND tenant_id = $2",
            connector_id, tenant_id,
        )
        if row is None:
            raise ConnectorError(f"no connector account {connector_id!r}")
        if str(row["status"]) == "revoked":
            raise ConnectorError(f"connector {connector_id!r} was revoked")
        scope = row["project_scope_json"]
        if isinstance(scope, str):
            scope = json.loads(scope or "{}")
        if project_id is not None and scope and project_id not in scope:
            raise ConnectorError(
                f"connector {connector_id!r} is not scoped to project {project_id!r}"
            )
        tokens = await self._open_tokens(row)
        expires_at = row["expires_at"]
        needs_refresh = (
            expires_at is not None and expires_at.timestamp() - time.time() < 60
            and tokens.get("refresh_token")
        )
        if needs_refresh:
            tokens = await self._refresh(row, tokens)
        return str(tokens["access_token"])

    async def _refresh(self, row: Any, tokens: dict[str, Any]) -> dict[str, Any]:
        connector_id = str(row["connector_id"])
        lock = self._refresh_locks.setdefault(connector_id, __import__("asyncio").Lock())
        async with lock:
            # Re-read: a second waiter may find the refresh already done.
            fresh = await self.db.fetchrow(
                "SELECT sealed_tokens, expires_at FROM connector_accounts WHERE connector_id = $1",
                connector_id,
            )
            if fresh is not None and fresh["expires_at"] is not None and \
                    fresh["expires_at"].timestamp() - time.time() > 60:
                return await self._open_tokens(fresh)
            provider = str(row["provider"])
            definition = self.providers[provider]
            client_id, client_secret = self.client_credentials_for(provider)
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    definition.token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Accept": "application/json"},
                )
            if response.status_code >= 400:
                await self.db.execute(
                    "UPDATE connector_accounts SET status = 'expired' WHERE connector_id = $1",
                    connector_id,
                )
                raise ConnectorError(
                    f"refresh failed for {connector_id}: HTTP {response.status_code}"
                )
            refreshed = response.json()
            # Providers rotate refresh tokens; when they do not, the old one
            # is the one that still works.
            if "refresh_token" not in refreshed:
                refreshed["refresh_token"] = tokens.get("refresh_token")
            space_id = str(row["space_id"])
            sealed = await self._seal_tokens(space_id, None, connector_id, refreshed)
            expires_in = float(refreshed.get("expires_in") or 3600)
            await self.db.execute(
                "UPDATE connector_accounts SET sealed_tokens = $2::jsonb, status = 'connected', "
                "expires_at = now() + make_interval(secs => $3::float8) "
                "WHERE connector_id = $1",
                connector_id, json.dumps(sealed), expires_in,
            )
            logger.info("connectors.refreshed", connector=connector_id)
            return refreshed

    async def revoke(self, connector_id: str, *, tenant_id: str = "local") -> bool:
        """Revoke an account. The sealed tokens stay sealed but unusable."""
        result = await self.db.execute(
            "UPDATE connector_accounts SET status = 'revoked' WHERE connector_id = $1 AND tenant_id = $2",
            connector_id, tenant_id,
        )
        return result.endswith(" 1")

    async def list_accounts(self, *, tenant_id: str = "local", space_id: str | None = None) -> list[dict[str, Any]]:
        rows = await self.db.fetch(
            "SELECT connector_id, provider, display_name, scopes, project_scope_json, status, expires_at "
            "FROM connector_accounts WHERE tenant_id = $1 "
            "AND ($2::text IS NULL OR space_id = $2) ORDER BY connector_id",
            tenant_id, space_id,
        )
        return [
            {
                "connector_id": str(r["connector_id"]), "provider": str(r["provider"]),
                "display_name": str(r["display_name"]), "status": str(r["status"]),
                "scopes": r["scopes"] if not isinstance(r["scopes"], str) else json.loads(r["scopes"] or "[]"),
                "expires_at": r["expires_at"].isoformat() if r["expires_at"] else None,
            }
            for r in rows
        ]

    # ---- isolated execution ----------------------------------------------------------

    async def authorized_request(self, connector_id: str, method: str, url: str, *,
                                 project_id: str | None = None, tenant_id: str = "local",
                                 json_body: Any = None, params: dict[str, Any] | None = None,
                                 headers: dict[str, str] | None = None,
                                 timeout_s: float = 60.0) -> dict[str, Any]:
        """Run one HTTP call with the connector's token attached.

        Execution happens here, in the toolhost: the token is attached to the
        request and only the response comes back. The model asked for *the
        call*; it never holds what authorizes it.
        """
        token = await self.access_token(connector_id, project_id=project_id, tenant_id=tenant_id)
        request_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=False) as client:
            response = await client.request(
                method.upper(), url, json=json_body, params=params, headers=request_headers,
            )
        body: Any
        try:
            body = response.json()
        except ValueError:
            body = response.text[:20_000]
        return {
            "status": response.status_code,
            "headers": {k: v for k, v in response.headers.items()
                        if k.lower() in ("content-type", "x-ratelimit-remaining", "link")},
            "body": body,
            "trust": "untrusted",
        }


__all__ = [
    "KNOWN_PROVIDERS",
    "ConnectorBroker",
    "ConnectorError",
    "PendingAuthorization",
    "ProviderDefinition",
    "providers_from_config",
]
