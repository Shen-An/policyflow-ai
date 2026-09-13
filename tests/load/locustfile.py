"""Executable route-level Locust profiles for the migration baseline."""

from __future__ import annotations

import csv
import json
import os
import platform
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
    return {
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
        },
    }


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
    except ImportError:
        snapshot["resource_monitor"] = "stdlib-only"
    return snapshot


_monitor_stop = threading.Event()
_monitor_thread: threading.Thread | None = None
_raw_handle: Any = None
_raw_writer: csv.DictWriter | None = None
_raw_lock = threading.Lock()


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
    global _monitor_thread, _raw_handle, _raw_writer
    _monitor_stop.clear()
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


class CapacityUser(HttpUser):
    """Authenticated Chat/SSE/file/isolation workflow selected by profile."""

    profile = selected_profile()
    wait_time = between(profile.wait_min_seconds, profile.wait_max_seconds)

    def on_start(self) -> None:
        self.profile = selected_profile()
        self.seed = int(os.environ.get("POLICYFLOW_LOAD_SEED", "20260913"))
        tenants = build_seed_data(seed=self.seed)["tenants"]
        self.tenant = tenants[
            int(os.environ.get("POLICYFLOW_LOAD_TENANT_INDEX", "0")) % len(tenants)
        ]
        self.token: str | None = None
        self._login()

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Load-Profile": self.profile.name,
            "X-Load-Seed": str(self.seed),
            "X-Tenant-ID": self.tenant["id"],
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _login(self) -> None:
        with self.client.post(
            "/api/auth/login",
            json={
                "username": os.environ.get("POLICYFLOW_LOAD_USERNAME", "admin"),
                "password": os.environ.get("POLICYFLOW_LOAD_PASSWORD", "123456"),
            },
            name="POST /api/auth/login",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                self.token = response.json().get("access_token")
            elif response.status_code >= 500:
                response.failure(f"login server error: {response.status_code}")

    def _chat(self) -> None:
        kb_id = os.environ.get("POLICYFLOW_LOAD_KB_ID", "")
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
        started = time.perf_counter()
        with self.client.post(
            "/api/chat/stream",
            json={"question": "差旅报销流程是什么？"},
            headers=self._headers(),
            name="POST /api/chat/stream",
            catch_response=True,
        ) as response:
            body = response.text
            response.context = {
                **(response.context or {}),
                "sse_first_event_ms": (time.perf_counter() - started) * 1000
                if "event: " in body
                else None,
                "sse_duration_ms": (time.perf_counter() - started) * 1000,
                "sse_event_count": body.count("event: "),
            }
            if response.status_code >= 500 or not body:
                response.failure(f"sse failure: {response.status_code}")

    def _file_workflow(self) -> None:
        kb_id = os.environ.get("POLICYFLOW_LOAD_KB_ID", "")
        if not kb_id:
            self._chat()
            return
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
        with self.client.get(
            "/api/knowledge-bases",
            headers=self._headers(),
            name="GET /api/knowledge-bases (tenant isolation)",
            catch_response=True,
        ) as response:
            if response.status_code >= 500:
                response.failure(f"isolation server error: {response.status_code}")

    @task
    def request_workflow(self) -> None:
        {"sse": self._sse, "file": self._file_workflow, "isolation": self._isolation}.get(
            self.profile.workflow, self._chat
        )()


def main() -> None:
    print(json.dumps(profile_options(), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
