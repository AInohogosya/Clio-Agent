"""Authentication and session management for Clio Agent integration."""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
import structlog
from passlib.context import CryptContext

from neuro_scaffold.agent.models import AuthPayload, SessionInfo
from neuro_scaffold.config.settings import Settings

logger = structlog.get_logger(__name__)

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated=["auto"])


class AuthenticationError(Exception):
    """Raised when authentication fails."""
    pass


class SessionExpiredError(Exception):
    """Raised when a session has expired."""
    pass


class RateLimiter:
    """Simple in-memory rate limiter for auth attempts."""

    def __init__(self, max_attempts: int = 5, lockout_seconds: int = 300) -> None:
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds
        self._attempts: dict[str, list[float]] = {}

    def is_locked_out(self, key: str) -> bool:
        now = time.monotonic()
        attempts = self._attempts.get(key, [])
        attempts = [t for t in attempts if now - t < self._lockout_seconds]
        self._attempts[key] = attempts
        return len(attempts) >= self._max_attempts

    def record_attempt(self, key: str) -> None:
        now = time.monotonic()
        self._attempts.setdefault(key, []).append(now)

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)


class AuthManager:
    """Handles API key verification and JWT token management."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._rate_limiter = RateLimiter(
            max_attempts=settings.max_auth_attempts,
            lockout_seconds=settings.lockout_seconds,
        )

    def verify_api_key(self, provided_key: str) -> bool:
        """Verify an API key against the configured key using constant-time comparison."""
        expected = self._settings.clio_api_key_plain
        return hmac.compare_digest(
            hashlib.sha256(provided_key.encode()).hexdigest(),
            hashlib.sha256(expected.encode()).hexdigest(),
        )

    def create_token(self, agent_id: str, permissions: list[str] | None = None) -> str:
        """Create a signed JWT token."""
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self._settings.jwt_expiry_seconds)
        payload = {
            "sub": agent_id,
            "iat": now,
            "exp": expires,
            "jti": str(uuid.uuid4()),
            "permissions": permissions or ["read", "execute"],
        }
        return jwt.encode(
            payload,
            self._settings.jwt_secret_plain,
            algorithm=self._settings.jwt_algorithm,
        )

    def verify_token(self, token: str) -> dict[str, Any]:
        """Verify and decode a JWT token."""
        try:
            return jwt.decode(
                token,
                self._settings.jwt_secret_plain,
                algorithms=[self._settings.jwt_algorithm],
            )
        except jwt.ExpiredSignatureError:
            raise SessionExpiredError("Token has expired")
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError(f"Invalid token: {exc}")

    def authenticate(self, payload: AuthPayload) -> str:
        """Full authentication flow: verify key, check rate limit, return token."""
        client_key = payload.api_key

        if self._rate_limiter.is_locked_out(client_key):
            logger.warning("Rate limit exceeded", agent_id=payload.agent_id)
            raise AuthenticationError("Too many auth attempts. Try again later.")

        if not self.verify_api_key(client_key):
            self._rate_limiter.record_attempt(client_key)
            logger.warning("Invalid API key", agent_id=payload.agent_id)
            raise AuthenticationError("Invalid API key")

        self._rate_limiter.reset(client_key)
        token = self.create_token(payload.agent_id, payload.permissions)
        logger.info("Authentication successful", agent_id=payload.agent_id)
        return token


class SessionManager:
    """Manages active sessions."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._sessions: dict[str, SessionInfo] = {}

    def create_session(
        self,
        agent_id: str,
        permissions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionInfo:
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=self._settings.jwt_expiry_seconds)
        session = SessionInfo(
            agent_id=agent_id,
            expires_at=expires,
            permissions=permissions or ["read", "execute"],
            metadata=metadata or {},
        )
        self._sessions[session.session_id] = session
        logger.info("Session created", session_id=session.session_id, agent_id=agent_id)
        return session

    def get_session(self, session_id: str) -> SessionInfo | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        if datetime.now(timezone.utc) > session.expires_at:
            self._sessions.pop(session_id, None)
            return None
        return session

    def destroy_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info("Session destroyed", session_id=session_id)
            return True
        return False

    def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        expired = [sid for sid, s in self._sessions.items() if now > s.expires_at]
        for sid in expired:
            del self._sessions[sid]
        if expired:
            logger.info("Cleaned up expired sessions", count=len(expired))
        return len(expired)

    @property
    def active_session_count(self) -> int:
        return len(self._sessions)
