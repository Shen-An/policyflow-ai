"""Safety contracts for the real-provider capacity profile."""

from __future__ import annotations

import pytest

from tests.load.real_provider import _artifact_dir, _backoff_seconds, _error_class, provider_budget


def test_provider_budget_requires_explicit_request_budget() -> None:
    budget = provider_budget(
        {"REAL_PROVIDER_MAX_REQUESTS": "3", "REAL_PROVIDER_MAX_TOTAL_TOKENS": "20"}
    )
    assert budget.max_requests == 3
    assert budget.max_total_tokens == 20


def test_provider_artifacts_cannot_use_mock_directory(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("REAL_PROVIDER_ARTIFACT_DIR", str(tmp_path / "artifacts" / "load" / "mock"))
    with pytest.raises(ValueError, match="must not be stored"):
        _artifact_dir()


def test_provider_error_classification_and_capped_backoff(monkeypatch) -> None:
    class RateLimitError(Exception):
        status_code = 429

    monkeypatch.setenv("CLAUDE_RETRY_BASE_SECONDS", "2")
    monkeypatch.setenv("CLAUDE_RETRY_MAX_SECONDS", "3")
    error = RateLimitError()
    assert _error_class(error) == "rate_limit"
    assert _backoff_seconds(3, error) == 3
