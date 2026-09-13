"""Budget-gated real Anthropic provider profile.

This module is intentionally separate from ``locustfile.py``.  It refuses to
make a provider call unless the caller explicitly opts in, supplies a key, and
sets bounded request/token/cost budgets.  Its artifacts never share the mock
capacity directory.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from locust import User, between, events, task
except RecursionError:  # pragma: no cover - pytest may import ssl before Locust

    class _EventHook:
        def add_listener(self, function):
            return function

    class _Events:
        test_start = _EventHook()

    class User:  # type: ignore[no-redef]
        environment = None

    def between(min_wait, max_wait):  # type: ignore[no-redef]
        return (min_wait, max_wait)

    def task(function):  # type: ignore[no-redef]
        return function

    events = _Events()  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class ProviderBudget:
    max_requests: int
    max_total_tokens: int
    max_budget_usd_cents: int
    max_retries: int = 2


class BudgetExceededError(RuntimeError):
    """Raised before a provider call would exceed an explicit test budget."""


class RealProviderDisabledError(RuntimeError):
    """Raised unless real-provider testing is explicitly enabled."""


def _env_int(name: str, default: int = 0) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def provider_budget(environ: dict[str, str] | None = None) -> ProviderBudget:
    env = os.environ if environ is None else environ
    return ProviderBudget(
        max_requests=_env_int("REAL_PROVIDER_MAX_REQUESTS", 0)
        if environ is None
        else int(env.get("REAL_PROVIDER_MAX_REQUESTS", "0")),
        max_total_tokens=_env_int("REAL_PROVIDER_MAX_TOTAL_TOKENS", 0)
        if environ is None
        else int(env.get("REAL_PROVIDER_MAX_TOTAL_TOKENS", "0")),
        max_budget_usd_cents=_env_int("REAL_PROVIDER_BUDGET_USD_CENTS", 0)
        if environ is None
        else int(env.get("REAL_PROVIDER_BUDGET_USD_CENTS", "0")),
        max_retries=min(int(env.get("CLAUDE_MAX_RETRIES", "2")), 5),
    )


class ProviderBudgetState:
    def __init__(self, budget: ProviderBudget) -> None:
        self.budget = budget
        self.requests = 0
        self.total_tokens = 0
        self.cost_usd_cents = 0
        self._lock = threading.Lock()

    def reserve_request(self) -> None:
        with self._lock:
            if self.budget.max_requests <= 0:
                raise BudgetExceededError("REAL_PROVIDER_MAX_REQUESTS must be positive")
            if self.requests >= self.budget.max_requests:
                raise BudgetExceededError("real provider request budget exhausted")
            self.requests += 1

    def record_usage(self, *, tokens: int, cost_usd_cents: int) -> None:
        with self._lock:
            if (
                self.budget.max_total_tokens
                and self.total_tokens + tokens > self.budget.max_total_tokens
            ):
                raise BudgetExceededError("real provider token budget exhausted")
            if (
                self.budget.max_budget_usd_cents
                and self.cost_usd_cents + cost_usd_cents > self.budget.max_budget_usd_cents
            ):
                raise BudgetExceededError("real provider cost budget exhausted")
            self.total_tokens += tokens
            self.cost_usd_cents += cost_usd_cents


def _artifact_dir() -> Path:
    root = Path(os.environ.get("REAL_PROVIDER_ARTIFACT_DIR", "artifacts/provider/anthropic"))
    if "artifacts/load" in root.as_posix().replace("\\", "/"):
        raise ValueError("real provider artifacts must not be stored under artifacts/load")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cost_cents(input_tokens: int, output_tokens: int) -> int:
    input_rate = float(os.environ.get("CLAUDE_INPUT_COST_USD_CENTS_PER_MILLION", "0"))
    output_rate = float(os.environ.get("CLAUDE_OUTPUT_COST_USD_CENTS_PER_MILLION", "0"))
    return int(round((input_tokens * input_rate + output_tokens * output_rate) / 1_000_000))


def _retry_after_seconds(error: Exception) -> float | None:
    """Read Retry-After from provider exceptions without depending on SDK internals."""

    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) or getattr(error, "headers", None) or {}
    raw = (
        headers.get("retry-after") or headers.get("Retry-After")
        if hasattr(headers, "get")
        else None
    )
    if raw is None:
        return None
    try:
        return max(0.0, min(float(raw), float(os.environ.get("CLAUDE_RETRY_MAX_SECONDS", "30"))))
    except (TypeError, ValueError):
        return None


def _error_status(error: Exception) -> int | None:
    status = getattr(error, "status_code", None) or getattr(
        getattr(error, "response", None), "status_code", None
    )
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _error_class(error: Exception) -> str:
    status = _error_status(error)
    if status == 429:
        return "rate_limit"
    if status in {408, 409}:
        return "request_timeout" if status == 408 else "conflict"
    if status is not None and status >= 500:
        return "provider_5xx"
    name = type(error).__name__.lower()
    if "timeout" in name:
        return "timeout"
    if "connection" in name or "network" in name:
        return "network_error"
    return "provider_error"


def _is_retryable(error: Exception) -> bool:
    return _error_class(error) in {
        "rate_limit",
        "request_timeout",
        "provider_5xx",
        "timeout",
        "network_error",
    }


def _backoff_seconds(attempt: int, error: Exception) -> float:
    retry_after = _retry_after_seconds(error)
    if retry_after is not None:
        return retry_after
    base = max(0.0, float(os.environ.get("CLAUDE_RETRY_BASE_SECONDS", "1")))
    cap = max(base, float(os.environ.get("CLAUDE_RETRY_MAX_SECONDS", "30")))
    return min(cap, base * (2 ** max(0, attempt - 1)))


class RealProviderUser(User):
    """Locust user for explicit, bounded Anthropic latency/token measurements."""

    wait_time = between(1.0, 3.0)
    _state: ProviderBudgetState | None = None

    def on_start(self) -> None:
        if os.environ.get("POLICYFLOW_ALLOW_REAL_PROVIDER", "").casefold() not in {
            "1",
            "true",
            "yes",
        }:
            raise RealProviderDisabledError(
                "set POLICYFLOW_ALLOW_REAL_PROVIDER=true to run provider tests"
            )
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RealProviderDisabledError("ANTHROPIC_API_KEY is required for provider tests")
        if self._state is None:
            type(self)._state = ProviderBudgetState(provider_budget())
        self.model = os.environ.get("CLAUDE_MODEL", "claude-opus-5")
        self.max_tokens = _env_int("CLAUDE_MAX_OUTPUT_TOKENS", 1024)
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RealProviderDisabledError("anthropic package is not installed") from exc
        self.client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    @task
    def measure_provider_latency(self) -> None:
        if self._state is None:
            raise RealProviderDisabledError("provider budget state is not initialized")
        started = time.perf_counter()
        first_token_ms: float | None = None
        exception: Exception | None = None
        response_length = 0
        usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        attempt_count = 0
        final_status = "success"
        for attempt in range(1, self._state.budget.max_retries + 2):
            attempt_count = attempt
            try:
                self._state.reserve_request()
                stream_started = time.perf_counter()
                text_parts: list[str] = []
                with self.client.messages.stream(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "user", "content": "Return the word READY."}],
                ) as stream:
                    for text in stream.text_stream:
                        if first_token_ms is None:
                            first_token_ms = (time.perf_counter() - stream_started) * 1000
                        text_parts.append(str(text))
                    response = stream.get_final_message()
                response_length = len("".join(text_parts))
                raw_usage = getattr(response, "usage", None)
                usage = {
                    "input_tokens": int(getattr(raw_usage, "input_tokens", 0)),
                    "output_tokens": int(getattr(raw_usage, "output_tokens", 0)),
                }
                self._state.record_usage(
                    tokens=sum(usage.values()), cost_usd_cents=_cost_cents(**usage)
                )
                exception = None
                break
            except BudgetExceededError as exc:
                exception = exc
                final_status = "budget_exhausted"
                break
            except Exception as exc:  # Locust records provider failures as samples.
                exception = exc
                final_status = _error_class(exc)
                if attempt > self._state.budget.max_retries or not _is_retryable(exc):
                    break
                time.sleep(_backoff_seconds(attempt, exc))
        elapsed_ms = (time.perf_counter() - started) * 1000
        root = _artifact_dir()
        with (root / "requests.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "provider": "anthropic",
                        "model": self.model,
                        "attempts": attempt_count,
                        "status": final_status if exception else "success",
                        "first_token_ms": first_token_ms,
                        "elapsed_ms": elapsed_ms,
                        **usage,
                        "cost_usd_cents": _cost_cents(**usage),
                        "error_class": _error_class(exception) if exception else None,
                        "error": str(exception)[:500] if exception else None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        self.environment.events.request.fire(
            request_type="ANTHROPIC",
            name="provider-latency",
            response_time=elapsed_ms,
            response_length=response_length,
            exception=exception,
            context={
                "model": self.model,
                "attempts": attempt_count,
                "first_token_ms": first_token_ms,
                "status": final_status if exception else "success",
                **usage,
                "artifact_dir": str(root),
            },
        )


@events.test_start.add_listener
def write_provider_metadata(environment, **_: Any) -> None:
    root = _artifact_dir()
    payload = {
        "mode": "real_provider",
        "provider": "anthropic",
        "model": os.environ.get("CLAUDE_MODEL", "claude-opus-5"),
        "budget": provider_budget().__dict__
        if hasattr(provider_budget(), "__dict__")
        else {
            "max_requests": provider_budget().max_requests,
            "max_total_tokens": provider_budget().max_total_tokens,
            "max_budget_usd_cents": provider_budget().max_budget_usd_cents,
        },
    }
    (root / "run-metadata.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
