"""Tests for notifier.py — ntfy POST and cooldown logic."""
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from bot.supervisor.checkpoint import bootstrap, insert_escalation
from bot.supervisor.notifier import escalate, _cooldown_ok, _post_ntfy


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    bootstrap(db_path=db_path)
    return db_path


class TestPostNtfy:
    def test_success_on_first_attempt(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=mock_resp) as mock_post:
            result = _post_ntfy("test-topic", "Test Title", "Test body")
        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "prudentia" not in call_kwargs.args[0] or "test-topic" in call_kwargs.args[0]
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Priority") == "high"
        assert headers.get("Title") == "Test Title"

    def test_failure_returns_false(self):
        with patch("httpx.post", side_effect=Exception("connection refused")):
            with patch("time.sleep"):  # speed up retries
                result = _post_ntfy("test-topic", "Test", "body")
        assert result is False


class TestCooldownOk:
    def test_no_prior_escalation(self, db):
        assert _cooldown_ok("drawdown", 3600, db_path=db) is True

    def test_within_cooldown(self, db):
        insert_escalation("drawdown", {"dd": 0.06}, db_path=db)
        assert _cooldown_ok("drawdown", 14400, db_path=db) is False

    def test_cooldown_zero_always_ok(self, db):
        insert_escalation("manual", {}, db_path=db)
        assert _cooldown_ok("manual", 0, db_path=db) is True

    def test_different_causes_independent(self, db):
        insert_escalation("drawdown", {}, db_path=db)
        assert _cooldown_ok("dsr_degrade", 14400, db_path=db) is True


class TestEscalate:
    def test_escalation_fires_and_records(self, db):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.post", return_value=mock_resp):
            result = escalate(
                cause="drawdown",
                title="Test",
                body="body",
                payload={"dd": 0.06},
                cooldown_seconds=0,
                db_path=db,
            )
        assert result is True
        # Second call within cooldown should be suppressed
        with patch("httpx.post", return_value=mock_resp):
            result2 = escalate(
                cause="drawdown",
                title="Test",
                body="body",
                payload={"dd": 0.06},
                cooldown_seconds=14400,
                db_path=db,
            )
        assert result2 is False

    def test_dry_run_does_not_post(self, db):
        with patch("httpx.post") as mock_post:
            result = escalate(
                cause="manual",
                title="Dry run test",
                body="body",
                payload={},
                cooldown_seconds=0,
                db_path=db,
                dry_run=True,
            )
        mock_post.assert_not_called()
        assert result is True

    def test_graceful_failure_on_http_error(self, db):
        with patch("httpx.post", side_effect=Exception("timeout")):
            with patch("time.sleep"):
                result = escalate(
                    cause="drawdown",
                    title="Test",
                    body="body",
                    payload={},
                    cooldown_seconds=0,
                    db_path=db,
                )
        assert result is False
