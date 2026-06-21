"""Unit tests for authentication and session management."""

from __future__ import annotations

from datetime import timedelta

import pytest

from neuro_scaffold.config.settings import Settings
from neuro_scaffold.security.auth import (
    AuthManager,
    AuthenticationError,
    RateLimiter,
    SessionExpiredError,
    SessionManager,
)


class TestRateLimiter:
    def test_not_locked_initially(self) -> None:
        rl = RateLimiter(max_attempts=3, lockout_seconds=60)
        assert rl.is_locked_out("client1") is False

    def test_locked_after_max_attempts(self) -> None:
        rl = RateLimiter(max_attempts=3, lockout_seconds=60)
        rl.record_attempt("client1")
        rl.record_attempt("client1")
        rl.record_attempt("client1")
        assert rl.is_locked_out("client1") is True

    def test_different_clients_independent(self) -> None:
        rl = RateLimiter(max_attempts=2, lockout_seconds=60)
        rl.record_attempt("client1")
        rl.record_attempt("client1")
        assert rl.is_locked_out("client1") is True
        assert rl.is_locked_out("client2") is False

    def test_reset_clears_attempts(self) -> None:
        rl = RateLimiter(max_attempts=2, lockout_seconds=60)
        rl.record_attempt("client1")
        rl.record_attempt("client1")
        rl.reset("client1")
        assert rl.is_locked_out("client1") is False


class TestAuthManager:
    @pytest.fixture
    def auth_manager(self, test_settings: Settings) -> AuthManager:
        return AuthManager(test_settings)

    def test_verify_valid_api_key(self, auth_manager: AuthManager, test_settings: Settings) -> None:
        assert auth_manager.verify_api_key(test_settings.clio_api_key_plain) is True

    def test_verify_invalid_api_key(self, auth_manager: AuthManager) -> None:
        assert auth_manager.verify_api_key("wrong-key") is False

    def test_create_and_verify_token(self, auth_manager: AuthManager) -> None:
        token = auth_manager.create_token("agent-1", ["read", "execute"])
        payload = auth_manager.verify_token(token)
        assert payload["sub"] == "agent-1"
        assert "read" in payload["permissions"]

    def test_authenticate_success(self, auth_manager: AuthManager, test_settings: Settings) -> None:
        from neuro_scaffold.agent.models import AuthPayload
        payload = AuthPayload(
            api_key=test_settings.clio_api_key_plain,
            agent_id="test-agent",
            task="test",
        )
        token = auth_manager.authenticate(payload)
        assert isinstance(token, str)
        assert len(token) > 0

    def test_authenticate_failure(self, auth_manager: AuthManager) -> None:
        from neuro_scaffold.agent.models import AuthPayload
        payload = AuthPayload(
            api_key="wrong-key",
            agent_id="test-agent",
            task="test",
        )
        with pytest.raises(AuthenticationError):
            auth_manager.authenticate(payload)

    def test_rate_limiting(self, auth_manager: AuthManager, test_settings: Settings) -> None:
        from neuro_scaffold.agent.models import AuthPayload
        for _ in range(test_settings.max_auth_attempts):
            try:
                auth_manager.authenticate(
                    AuthPayload(api_key="wrong", agent_id="test", task="")
                )
            except AuthenticationError:
                pass
        with pytest.raises(AuthenticationError, match="Too many"):
            auth_manager.authenticate(
                AuthPayload(api_key="wrong", agent_id="test", task="")
            )


class TestSessionManager:
    @pytest.fixture
    def session_mgr(self, test_settings: Settings) -> SessionManager:
        return SessionManager(test_settings)

    def test_create_session(self, session_mgr: SessionManager) -> None:
        session = session_mgr.create_session("agent-1")
        assert session.agent_id == "agent-1"
        assert session.session_id is not None

    def test_get_session(self, session_mgr: SessionManager) -> None:
        created = session_mgr.create_session("agent-1")
        retrieved = session_mgr.get_session(created.session_id)
        assert retrieved is not None
        assert retrieved.session_id == created.session_id

    def test_get_nonexistent_session(self, session_mgr: SessionManager) -> None:
        assert session_mgr.get_session("nonexistent") is None

    def test_destroy_session(self, session_mgr: SessionManager) -> None:
        session = session_mgr.create_session("agent-1")
        assert session_mgr.destroy_session(session.session_id) is True
        assert session_mgr.get_session(session.session_id) is None

    def test_destroy_nonexistent(self, session_mgr: SessionManager) -> None:
        assert session_mgr.destroy_session("nonexistent") is False

    def test_active_count(self, session_mgr: SessionManager) -> None:
        session_mgr.create_session("agent-1")
        session_mgr.create_session("agent-2")
        assert session_mgr.active_session_count == 2
        session_mgr.destroy_session(session_mgr.create_session("agent-3").session_id)
        assert session_mgr.active_session_count == 2
