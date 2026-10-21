"""Executable route-level Locust profiles for the migration baseline."""

from __future__ import annotations

import csv
import itertools
import json
import os
import platform
import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from locust import HttpUser, between, events, task
except RecursionError:  # pragma: no cover

    class _EventHook:
        def add_listener(self, function):
            return function

    class _Events:
        test_start = _EventHook()
        test_stop = _EventHook()
        init = _EventHook()
        init_command_line_parser = _EventHook()
        request = _EventHook()

    class HttpUser:  # type: ignore[no-redef]
        environment = None

    def between(min_wait, max_wait):  # type: ignore[no-redef]
        return (min_wait, max_wait)

    def task(function):  # type: ignore[no-redef]
        return function

    events = _Events()  # type: ignore[assignment]

try:
    from tests.load.seed_data import build_seed_data
except ModuleNotFoundError:  # pragma: no cover
    from seed_data import build_seed_data

try:
    from tests.load.acceptance_profile import (
        ACCEPTANCE_TARGETS,
        CONCURRENCY,
        WORKFLOW_WEIGHTS,
        evaluate_acceptance,
        weighted_workflow,
    )
except ModuleNotFoundError:  # pragma: no cover
    from acceptance_profile import (  # type: ignore[no-redef]
        ACCEPTANCE_TARGETS,
        CONCURRENCY,
        WORKFLOW_WEIGHTS,
        evaluate_acceptance,
        weighted_workflow,
    )

#: Routes that never call the LLM. ``non_llm_rps`` / ``non_llm_p95_ms`` in
#: `spec.md` are defined over exactly these, so the classification lives here
#: instead of being guessed during analysis.
NON_LLM_ROUTES: frozenset[str] = frozenset(
    {
        "POST /api/auth/login",
        "GET /api/knowledge-bases",
        "GET /api/knowledge-bases (tenant isolation)",
        "GET /api/knowledge-bases/{id} (cross-tenant)",
        "GET /api/v2/runs/{id} (cross-tenant)",
        "POST /api/v2/runs (isolation probe)",
        "GET /api/documents/{id}/status",
        "POST /api/knowledge-bases/{id}/documents",
    }
)

ISOLATION_ROUTE = "GET /api/knowledge-bases/{id} (cross-tenant)"
ISOLATION_RUN_ROUTE = "GET /api/v2/runs/{id} (cross-tenant)"
ISOLATION_PROBE_ROUTE = "POST /api/v2/runs (isolation probe)"


@dataclass(frozen=True, slots=True)
class LoadProfile:
    name: str
    users: int
    spawn_rate: float
    wait_min_seconds: float
    wait_max_seconds: float
    workflow: str = "chat"


PROFILE_CONFIGS: dict[str, LoadProfile] = {
    "smoke": LoadProfile("smoke", 1, 1.0, 1.0, 2.0),
    "load": LoadProfile("load", 10, 0.2, 0.2, 1.0),
    "stress": LoadProfile("stress", 50, 5.0, 0.1, 0.5),
    "spike": LoadProfile("spike", 100, 25.0, 0.0, 0.2),
    "soak": LoadProfile("soak", 10, 1.0, 1.0, 3.0),
    "sse": LoadProfile("sse", 5, 1.0, 0.5, 1.5, "sse"),
    "file-workflow": LoadProfile("file-workflow", 5, 1.0, 0.5, 1.5, "file"),
    "saturation": LoadProfile("saturation", 200, 50.0, 0.0, 0.1),
    "tenant-isolation": LoadProfile("tenant-isolation", 10, 2.0, 0.2, 1.0, "isolation"),
    # 2,200 users = 1,000 chat sessions + 1,000 SSE streams + 200 file/isolation.
    "acceptance": LoadProfile("acceptance", 2_200, 50.0, 0.0, 0.1, "acceptance"),
}


def selected_profile(environ: dict[str, str] | None = None) -> LoadProfile:
    env = os.environ if environ is None else environ
    name = env.get("POLICYFLOW_LOAD_PROFILE", "smoke").strip().casefold()
    try:
        return PROFILE_CONFIGS[name]
    except KeyError as exc:
        choices = ", ".join(sorted(PROFILE_CONFIGS))
        raise ValueError(f"unknown load profile {name!r}; expected one of: {choices}") from exc


def profile_options(environ: dict[str, str] | None = None) -> dict[str, Any]:
    profile = selected_profile(environ)
    env = os.environ if environ is None else environ
    options: dict[str, Any] = {
        "profile": profile.name,
        "users": profile.users,
        "spawn_rate": profile.spawn_rate,
        "wait_time": [profile.wait_min_seconds, profile.wait_max_seconds],
        "workflow": profile.workflow,
        "seed": int(env.get("POLICYFLOW_LOAD_SEED", "20260913")),
        "routes": {
            "login": "/api/auth/login",
            "chat": "/api/chat",
            "sse": "/api/chat/stream",
            "knowledge_bases": "/api/knowledge-bases",
            "upload": "/api/knowledge-bases/{id}/documents",
            "document_status": "/api/documents/{id}/status",
            "runs": "/api/v2/runs",
        },
    }
    if profile.name == "acceptance":
        options["targets"] = ACCEPTANCE_TARGETS.as_dict()
        options["workflow_weights"] = dict(WORKFLOW_WEIGHTS)
        options["requires_dedicated_environment"] = True
    return options


def _resource_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "cpu_count": os.cpu_count(),
        "load_average": getattr(os, "getloadavg", lambda: None)(),
        "platform": platform.platform(),
    }
    try:
        import psutil  # type: ignore[import-not-found]

        snapshot["cpu_percent"] = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        snapshot["memory_percent"] = memory.percent
        snapshot["memory_available_bytes"] = memory.available
        # The load generator's own RSS: the soak leak guard is graded on the
        # first-vs-peak ratio of this number.
        snapshot["rss_bytes"] = psutil.Process().memory_info().rss
    except ImportError:
        snapshot["resource_monitor"] = "stdlib-only"
    return snapshot


# Locust starts each user without a stable ordinal, so tenants would all land on
# index 0 unless we hand out slots. The counter makes the spread deterministic
# per run and keeps the isolation profile meaningful (two tenants in flight).
_USER_SLOTS = itertools.count()
_USER_SLOT_LOCK = threading.Lock()


def next_user_slot() -> int:
    """Return a process-wide, monotonically increasing user ordinal."""

    with _USER_SLOT_LOCK:
        return next(_USER_SLOTS)


def tenant_for_slot(tenants: list[dict[str, Any]], slot: int) -> dict[str, Any]:
    """Pick the tenant for a user slot, honouring the explicit env override."""

    explicit = os.environ.get("POLICYFLOW_LOAD_TENANT_INDEX")
    index = int(explicit) if explicit is not None else slot
    return tenants[index % len(tenants)]


def foreign_tenant(tenants: list[dict[str, Any]], tenant: dict[str, Any]) -> dict[str, Any] | None:
    """Return a different tenant to attempt cross-tenant reads with."""

    for candidate in tenants:
        if candidate["id"] != tenant["id"]:
            return candidate
    return None


def load_username(tenant: dict[str, Any]) -> str:
    """Username for the seeded tenant, unless an explicit one is configured."""

    override = os.environ.get("POLICYFLOW_LOAD_USERNAME")
    if override:
        return override
    role = os.environ.get("POLICYFLOW_LOAD_ROLE", "employee")
    return f"{tenant['code']}-{role}"


def _percentile(values: list[float], quantile: float) -> float | None:
    """Nearest-rank percentile, so a small sample never invents a tail value."""

    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(quantile * (len(ordered) - 1)))))
    return ordered[index]


def summarize_run(root: Path, *, duration_seconds: float) -> dict[str, Any]:
    """Aggregate the raw request log into the metrics `evaluate_acceptance` grades.

    Everything here is derived from what the run actually recorded; a metric that
    has no observations is simply absent, which the evaluator treats as
    *inconclusive* rather than passing it.
    """

    rows: list[dict[str, str]] = []
    raw_path = root / "raw.csv"
    if raw_path.exists():
        with raw_path.open("r", newline="", encoding="utf-8") as handle:
            rows.extend(csv.DictReader(handle))

    non_llm_latencies: list[float] = []
    first_stage: list[float] = []
    failures = 0
    isolation_violations = 0
    for row in rows:
        name = row.get("name") or ""
        try:
            latency = float(row.get("response_time_ms") or 0.0)
        except ValueError:
            latency = 0.0
        if row.get("exception"):
            failures += 1
        else:
            status = int(row.get("status_code") or 0)
            if status >= 500 or status == 0:
                failures += 1
        if name in NON_LLM_ROUTES:
            non_llm_latencies.append(latency)
        try:
            context = json.loads(row.get("context") or "{}")
        except json.JSONDecodeError:
            context = {}
        if isinstance(context, dict):
            first_event = context.get("sse_first_event_ms")
            if isinstance(first_event, int | float) and first_event >= 0:
                first_stage.append(float(first_event))
            if context.get("isolation_violation"):
                isolation_violations += 1

    rss_samples: list[int] = []
    resource_path = root / "resource.jsonl"
    if resource_path.exists():
        for line in resource_path.read_text(encoding="utf-8").splitlines():
            try:
                sample = json.loads(line)
            except json.JSONDecodeError:
                continue
            rss = sample.get("rss_bytes")
            if isinstance(rss, int) and rss > 0:
                rss_samples.append(rss)

    summary: dict[str, Any] = {
        "profile": selected_profile().name,
        "captured_at": datetime.now(UTC).isoformat(),
        "duration_seconds": round(duration_seconds, 3),
        "request_count": len(rows),
        "server_failures": failures,
        **CONCURRENCY.snapshot(),
    }
    if duration_seconds > 0:
        summary["non_llm_rps"] = round(len(non_llm_latencies) / duration_seconds, 3)
    non_llm_p95 = _percentile(non_llm_latencies, 0.95)
    if non_llm_p95 is not None:
        summary["non_llm_p95_ms"] = round(non_llm_p95, 3)
    first_stage_p95 = _percentile(first_stage, 0.95)
    if first_stage_p95 is not None:
        summary["first_stage_p95_ms"] = round(first_stage_p95, 3)
    if rows:
        summary["server_failure_rate"] = round(failures / len(rows), 6)
    if isolation_violations:
        summary["isolation_violations"] = isolation_violations
    if rss_samples:
        summary["rss_growth_ratio"] = round(max(rss_samples) / rss_samples[0], 4)
    summary["soak_minutes"] = round(duration_seconds / 60.0, 3)
    summary["verdict"] = evaluate_acceptance(summary)["verdict"]
    return summary


_monitor_stop = threading.Event()
_monitor_thread: threading.Thread | None = None
_raw_handle: Any = None
_raw_writer: csv.DictWriter | None = None
_raw_lock = threading.Lock()
_run_started_at: float | None = None


def _artifact_root() -> Path:
    root = Path(
        os.environ.get(
            "POLICYFLOW_LOAD_ARTIFACT_DIR", f"artifacts/load/baseline/{selected_profile().name}"
        )
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _record_raw(event: dict[str, Any]) -> None:
    if _raw_writer is None:
        return
    with _raw_lock:
        _raw_writer.writerow(event)
        _raw_handle.flush()


def _monitor_resources(profile_name: str, output: Path, interval_seconds: float) -> None:
    with output.open("a", encoding="utf-8") as handle:
        while not _monitor_stop.wait(interval_seconds):
            handle.write(
                json.dumps({"profile": profile_name, **_resource_snapshot()}, ensure_ascii=False)
                + "\n"
            )
            handle.flush()


@events.init_command_line_parser.add_listener
def add_profile_argument(parser, **_: Any) -> None:
    parser.add_argument("--profile", choices=sorted(PROFILE_CONFIGS), default=None)


@events.init.add_listener
def select_cli_profile(environment, **_: Any) -> None:
    profile = getattr(getattr(environment, "parsed_options", None), "profile", None)
    if profile:
        os.environ["POLICYFLOW_LOAD_PROFILE"] = profile


@events.test_start.add_listener
def start_resource_monitor(environment, **_: Any) -> None:
    global _monitor_thread, _raw_handle, _raw_writer, _run_started_at
    _monitor_stop.clear()
    CONCURRENCY.reset()
    _run_started_at = time.perf_counter()
    root = _artifact_root()
    (root / "profile.json").write_text(
        json.dumps(profile_options(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _raw_handle = (root / "raw.csv").open("w", newline="", encoding="utf-8")
    _raw_writer = csv.DictWriter(
        _raw_handle,
        fieldnames=[
            "timestamp",
            "request_type",
            "name",
            "status_code",
            "response_time_ms",
            "response_length",
            "exception",
            "context",
        ],
    )
    _raw_writer.writeheader()
    interval = max(float(os.environ.get("POLICYFLOW_LOAD_RESOURCE_INTERVAL_SECONDS", "5")), 0.1)
    _monitor_thread = threading.Thread(
        target=_monitor_resources,
        args=(selected_profile().name, root / "resource.jsonl", interval),
        daemon=True,
    )
    _monitor_thread.start()


@events.request.add_listener
def record_request(
    name, request_type, response_time, response_length, response, context, exception, **_: Any
) -> None:
    _record_raw(
        {
            "timestamp": datetime.now(UTC).isoformat(),
            "request_type": request_type,
            "name": name,
            "status_code": getattr(response, "status_code", None),
            "response_time_ms": round(float(response_time), 3),
            "response_length": response_length,
            "exception": str(exception) if exception else "",
            "context": json.dumps(context or {}, ensure_ascii=False, default=str),
        }
    )


@events.test_stop.add_listener
def stop_resource_monitor(environment, **_: Any) -> None:
    global _raw_handle, _raw_writer
    _monitor_stop.set()
    if _monitor_thread is not None:
        _monitor_thread.join(timeout=2)
    if _raw_handle is not None:
        _raw_handle.close()
    _raw_handle = None
    _raw_writer = None
    # Grade from what the run actually recorded. `summary.json` feeds
    # `evaluate_acceptance`; a metric this run never observed stays absent and
    # grades as inconclusive rather than a silent pass.
    root = _artifact_root()
    duration = (
        time.perf_counter() - _run_started_at if _run_started_at is not None else 0.0
    )
    summary = summarize_run(root, duration_seconds=duration)
    (root / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


class CapacityUser(HttpUser):
    """Authenticated Chat/SSE/file/isolation workflow selected by profile."""

    profile = selected_profile()
    wait_time = between(profile.wait_min_seconds, profile.wait_max_seconds)

    def on_start(self) -> None:
        self.profile = selected_profile()
        self.seed = int(os.environ.get("POLICYFLOW_LOAD_SEED", "20260913"))
        tenants = build_seed_data(seed=self.seed)["tenants"]
        # Spread users across tenants instead of pinning every user to index 0.
        self.tenant = tenant_for_slot(tenants, next_user_slot())
        self.foreign_tenant = foreign_tenant(tenants, self.tenant)
        self.token: str | None = None
        self.foreign_token: str | None = None
        self._rng = random.Random(f"{self.seed}:{self.tenant['id']}")
        self._login()

    def _headers(self, token: str | None = None) -> dict[str, str]:
        headers = {
            "X-Load-Profile": self.profile.name,
            "X-Load-Seed": str(self.seed),
            "X-Tenant-ID": self.tenant["id"],
        }
        bearer = token if token is not None else self.token
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        return headers

    def _login(self) -> None:
        with self.client.post(
            "/api/auth/login",
            json={
                "username": load_username(self.tenant),
                "password": os.environ.get("POLICYFLOW_LOAD_PASSWORD", "123456"),
                # Multi-tenant login needs the tenant selector, otherwise every
                # username collision resolves inside the default tenant.
                "tenant_code": self.tenant["code"],
            },
            name="POST /api/auth/login",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                self.token = response.json().get("access_token")
            elif response.status_code >= 500:
                response.failure(f"login server error: {response.status_code}")

    def _login_foreign(self) -> None:
        """Log in as another tenant so isolation probes carry a foreign token."""

        if self.foreign_tenant is None or self.foreign_token is not None:
            return
        with self.client.post(
            "/api/auth/login",
            json={
                "username": load_username(self.foreign_tenant),
                "password": os.environ.get("POLICYFLOW_LOAD_PASSWORD", "123456"),
                "tenant_code": self.foreign_tenant["code"],
            },
            name="POST /api/auth/login (foreign tenant)",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                self.foreign_token = response.json().get("access_token")
            elif response.status_code >= 500:
                response.failure(f"foreign login server error: {response.status_code}")

    def _chat(self) -> None:
        kb_id = os.environ.get("POLICYFLOW_LOAD_KB_ID", "")
        with CONCURRENCY.track("chat"):
            with self.client.post(
                "/api/chat",
                json={
                    "question": os.environ.get("POLICYFLOW_LOAD_QUESTION", "差旅报销需要哪些材料？"),
                    "knowledge_base_ids": [kb_id] if kb_id else [],
                    "top_k": 5,
                },
                headers=self._headers(),
                name="POST /api/chat",
                catch_response=True,
            ) as response:
                if response.status_code >= 500:
                    response.failure(f"chat server error: {response.status_code}")

    def _sse(self) -> None:
        # Per-frame timing: the first SSE frame's arrival is what SC-004's
        # "first stage p95" means. Reading `response.text` would only report
        # when the *whole* stream finished, which is not a first-event latency.
        started = time.perf_counter()
        first_event_ms: float | None = None
        event_count = 0
        with CONCURRENCY.track("sse"):
            with self.client.post(
                "/api/chat/stream",
                json={"question": "差旅报销流程是什么？"},
                headers=self._headers(),
                name="POST /api/chat/stream",
                catch_response=True,
                stream=True,
            ) as response:
                if response.status_code >= 500:
                    response.failure(f"sse failure: {response.status_code}")
                    return
                try:
                    for raw_line in response.iter_lines():
                        if not raw_line:
                            continue
                        line = (
                            raw_line.decode("utf-8")
                            if isinstance(raw_line, bytes)
                            else raw_line
                        )
                        if line.startswith("event:"):
                            event_count += 1
                            if first_event_ms is None:
                                first_event_ms = (time.perf_counter() - started) * 1000
                        elif line.startswith("data:") and first_event_ms is None:
                            first_event_ms = (time.perf_counter() - started) * 1000
                except Exception as exc:  # noqa: BLE001 - surfaced as a locust failure
                    response.failure(f"sse stream error: {exc}")
                response.context = {
                    **(response.context or {}),
                    "sse_first_event_ms": first_event_ms,
                    "sse_duration_ms": (time.perf_counter() - started) * 1000,
                    "sse_event_count": event_count,
                }
                if event_count == 0:
                    response.failure("sse stream produced no events")

    def _file_workflow(self) -> None:
        kb_id = os.environ.get("POLICYFLOW_LOAD_KB_ID", "")
        if not kb_id:
            self._chat()
            return
        with CONCURRENCY.track("file"):
            with self.client.post(
                f"/api/knowledge-bases/{kb_id}/documents",
                files={
                    "file": (
                        "baseline-policy.txt",
                        b"Receipts are required within 30 days.",
                        "text/plain",
                    )
                },
                data={"title": "baseline-policy"},
                headers=self._headers(),
                name="POST /api/knowledge-bases/{id}/documents",
                catch_response=True,
            ) as response:
                if response.status_code not in {200, 201}:
                    response.failure(f"upload failure: {response.status_code}")
                    return
                document_id = response.json().get("document_id")
            if document_id:
                with self.client.get(
                    f"/api/documents/{document_id}/status",
                    headers=self._headers(),
                    name="GET /api/documents/{id}/status",
                    catch_response=True,
                ) as status_response:
                    if status_response.status_code >= 500:
                        status_response.failure(f"status failure: {status_response.status_code}")

    def _isolation(self) -> None:
        """Assert the tenant boundary, not just that a list request succeeds.

        Reading your own knowledge bases proves nothing about isolation. This
        probe reads another tenant's resource with a foreign token and treats any
        success as a violation, recording it in the request context so the run
        summary can count it.
        """

        own_kb_ids: list[str] = []
        with self.client.get(
            "/api/knowledge-bases",
            headers=self._headers(),
            name="GET /api/knowledge-bases",
            catch_response=True,
        ) as response:
            if response.status_code >= 500:
                response.failure(f"isolation server error: {response.status_code}")
            elif response.status_code == 200:
                payload = response.json()
                items = payload.get("items") if isinstance(payload, dict) else payload
                if isinstance(items, list):
                    own_kb_ids = [
                        str(item.get("id"))
                        for item in items
                        if isinstance(item, dict) and item.get("id")
                    ]

        self._login_foreign()
        if self.foreign_token is None:
            return

        if own_kb_ids:
            target = own_kb_ids[0]
            with self.client.get(
                f"/api/knowledge-bases/{target}",
                headers=self._headers(self.foreign_token),
                name=ISOLATION_ROUTE,
                catch_response=True,
            ) as response:
                leaked = response.status_code < 400
                response.context = {
                    **(response.context or {}),
                    "isolation_violation": leaked,
                    "expected": "cross-tenant denial",
                }
                if leaked:
                    response.failure(
                        f"cross-tenant read of knowledge base {target} succeeded"
                    )
                elif response.status_code >= 500:
                    response.failure(f"isolation probe server error: {response.status_code}")
            return

        # No knowledge base to probe: create a run in this tenant and try to read
        # it from the other one. The run route is tenant-scoped the same way.
        key = f"isolation-{self.tenant['code']}-{self._rng.randrange(1 << 40):012d}"
        run_id: str | None = None
        with self.client.post(
            "/api/v2/runs",
            json={"kind": "chat", "payload": {"question": "isolation probe"}},
            headers={**self._headers(), "Idempotency-Key": key},
            name=ISOLATION_PROBE_ROUTE,
            catch_response=True,
        ) as response:
            if response.status_code in {200, 201}:
                run_id = response.json().get("run_id") or response.json().get("id")
            else:
                response.failure(f"isolation probe run failed: {response.status_code}")
        if not run_id:
            return
        with self.client.get(
            f"/api/v2/runs/{run_id}",
            headers=self._headers(self.foreign_token),
            name=ISOLATION_RUN_ROUTE,
            catch_response=True,
        ) as response:
            leaked = response.status_code < 400
            response.context = {
                **(response.context or {}),
                "isolation_violation": leaked,
                "expected": "cross-tenant denial",
            }
            if leaked:
                response.failure(f"cross-tenant read of run {run_id} succeeded")
            elif response.status_code >= 500:
                response.failure(f"isolation probe server error: {response.status_code}")

    @task
    def request_workflow(self) -> None:
        workflow = self._workflow()
        {
            "sse": self._sse,
            "file": self._file_workflow,
            "isolation": self._isolation,
            "chat": self._chat,
        }[workflow]()

    def _workflow(self) -> str:
        """Pick the workflow for this iteration.

        Acceptance is a *mix* (1,000 chat + 1,000 SSE + file/isolation), so it
        draws from `WORKFLOW_WEIGHTS`. A single-workflow profile keeps its
        configured workflow.
        """

        if self.profile.workflow == "acceptance":
            return weighted_workflow(WORKFLOW_WEIGHTS, self._rng)
        if self.profile.workflow not in {"sse", "file", "isolation"}:
            return "chat"
        return self.profile.workflow


def main() -> None:
    print(json.dumps(profile_options(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
