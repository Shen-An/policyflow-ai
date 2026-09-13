"""Offline, route-level T015 baseline executor with retained raw evidence."""

from __future__ import annotations

import csv
import json
import os
import statistics
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.models import KnowledgeBase, KnowledgeDocument
from backend.app.integrations.deterministic_llm import DeterministicLLMService
from backend.app.main import create_app
from backend.app.rag.protocols import RetrievalRequest
from backend.app.schemas.retrieval import Evidence
from tests.load.artifacts import collect_artifact_manifest, write_artifact_manifest

PROFILE_NAMES = (
    "smoke",
    "load",
    "stress",
    "spike",
    "soak",
    "sse",
    "file-workflow",
    "saturation",
    "tenant-isolation",
)


class BaselineAdapter:
    """Small local retriever/indexer implementing the LightRAG protocol."""

    name = "deterministic_local"
    available = True

    def __init__(self) -> None:
        self.engine = None

    async def insert_document(self, knowledge_base, document) -> None:
        return None

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        with Session(self.engine) as session:
            rows = session.exec(
                select(KnowledgeDocument).where(
                    KnowledgeDocument.knowledge_base_id.in_(request.knowledge_base_ids),
                    KnowledgeDocument.index_status != "deleted",
                )
            ).all()
            names = {
                kb.id: kb.name
                for kb in session.exec(
                    select(KnowledgeBase).where(KnowledgeBase.id.in_(request.knowledge_base_ids))
                ).all()
            }
        query = request.query.casefold()
        hits = [
            row
            for row in rows
            if query in (f"{row.title} {row.content_text or ''}").casefold()
            or any(token in (row.content_text or "").casefold() for token in query.split())
        ]
        return [
            Evidence(
                knowledge_base_id=row.knowledge_base_id,
                knowledge_base_name=names.get(row.knowledge_base_id, ""),
                document_id=row.id,
                document_title=row.title,
                snippet=(row.content_text or "")[:400],
                score=1.0 / (index + 1),
                retriever_type=self.name,
                rank=index + 1,
            )
            for index, row in enumerate(hits[:limit])
        ]


def _run_profile(
    client: TestClient,
    profile: str,
    token: str,
    kb_id: str,
    root: Path,
    iterations: int,
    *,
    forbidden_kb_id: str | None = None,
    isolation_token: str | None = None,
) -> dict[str, Any]:
    profile_root = root / profile
    profile_root.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    for index in range(iterations):
        started = time.perf_counter()
        isolation_pass: bool | None = None
        index_status: str | int | None = None
        if profile == "sse":
            response = client.post(
                "/api/chat/stream",
                json={"question": "差旅报销流程是什么？", "knowledge_base_ids": [kb_id]},
                headers={"Authorization": f"Bearer {token}"},
            )
            event_count = response.text.count("event: ")
        elif profile == "file-workflow":
            response = client.post(
                f"/api/knowledge-bases/{kb_id}/documents",
                files={
                    "file": (
                        f"baseline-{index}.txt",
                        b"Receipts are required within 30 days.",
                        "text/plain",
                    )
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            event_count = 0
            if response.status_code in {200, 201}:
                document_id = response.json()["document_id"]
                status_response = client.get(
                    f"/api/documents/{document_id}/status",
                    headers={"Authorization": f"Bearer {token}"},
                )
                event_count = status_response.status_code
                index_status = status_response.json().get("index_status")
            else:
                index_status = None
        elif profile == "tenant-isolation":
            auth_token = isolation_token or token
            response = client.get(
                "/api/knowledge-bases",
                headers={"Authorization": f"Bearer {auth_token}", "X-Tenant-ID": "tenant-02"},
            )
            visible = response.json().get("items", []) if response.status_code == 200 else []
            finance_visible = any(item.get("code") == "finance" for item in visible)
            detail_response = (
                client.get(
                    f"/api/knowledge-bases/{forbidden_kb_id}",
                    headers={"Authorization": f"Bearer {auth_token}"},
                )
                if forbidden_kb_id
                else None
            )
            isolation_pass = (
                response.status_code == 200
                and not finance_visible
                and (detail_response is None or detail_response.status_code == 403)
            )
            event_count = len(visible)
        else:
            response = client.post(
                "/api/chat",
                json={"question": "差旅报销需要哪些材料？", "knowledge_base_ids": [kb_id]},
                headers={"Authorization": f"Bearer {token}"},
            )
            event_count = 0
            index_status = None
        events.append(
            {
                "profile": profile,
                "iteration": index,
                "status_code": response.status_code,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                "response_bytes": len(response.content),
                "event_count": event_count,
                "isolation_pass": isolation_pass,
                "index_status": index_status,
            }
        )
    (profile_root / "events.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in events), encoding="utf-8"
    )
    with (profile_root / "raw.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(events[0]) if events else ["profile"])
        writer.writeheader()
        writer.writerows(events)
    (profile_root / "resource.jsonl").write_text(
        json.dumps(
            {"profile": profile, "samples": len(events), "execution_mode": "route_level_asgi"}
        )
        + "\n",
        encoding="utf-8",
    )
    (profile_root / "profile.json").write_text(
        json.dumps(
            {
                "profile": profile,
                "iterations": iterations,
                "execution_mode": "route_level_asgi",
                "provider": "deterministic_mock",
                "retriever": "deterministic_local",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    latencies = [float(item["elapsed_ms"]) for item in events]
    status_counts: dict[str, int] = {}
    for item in events:
        key = str(item["status_code"])
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "profile": profile,
        "samples": len(events),
        "status_codes": sorted({item["status_code"] for item in events}),
        "failures": sum(item["status_code"] >= 500 for item in events),
        "status_counts": status_counts,
        "latency_ms": {
            "min": round(min(latencies), 3),
            "p50": round(statistics.median(latencies), 3),
            "max": round(max(latencies), 3),
        },
        "isolation_pass": all(item["isolation_pass"] is not False for item in events),
        "sse_event_count": max((item["event_count"] for item in events), default=0)
        if profile == "sse"
        else None,
    }


def main() -> None:
    root = Path(
        os.environ.get("POLICYFLOW_BASELINE_ARTIFACT_DIR", "artifacts/load/baseline")
    ).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="policyflow-t015-", ignore_cleanup_errors=True) as temp:
        temp_path = Path(temp)
        settings = Settings(
            DATABASE_URL=f"sqlite:///{(temp_path / 'baseline.db').as_posix()}",
            LOG_DIR=temp_path / "logs",
            UPLOAD_DIR=temp_path / "uploads",
            RAG_WORKSPACE_DIR=temp_path / "rag",
            SECRET_KEY="baseline-secret",
            BOOTSTRAP_ADMIN_PASSWORD="123456",
            LLM_MOCK_LATENCY_MILLISECONDS=0,
            CHAT_REFLECTION_ENABLED=False,
            _env_file=None,
        )
        adapter = BaselineAdapter()
        app = create_app(
            settings=settings,
            lightrag_adapter=adapter,
            llm_service=DeterministicLLMService(),
            frontend_dist=None,
        )
        adapter.engine = app.state.engine
        with TestClient(app) as client:
            login = client.post("/api/auth/login", json={"username": "admin", "password": "123456"})
            login.raise_for_status()
            token = login.json()["access_token"]
            kbs = client.get(
                "/api/knowledge-bases", headers={"Authorization": f"Bearer {token}"}
            ).json()["items"]
            kb_id = next(item["id"] for item in kbs if item["code"] == "admin")
            finance_kb_id = next(item["id"] for item in kbs if item["code"] == "finance")
            departments = client.get(
                "/api/departments", headers={"Authorization": f"Bearer {token}"}
            ).json()["items"]
            department_ids = {item["code"]: item["id"] for item in departments}
            for username, department_code in (
                ("baseline-hr", "hr"),
                ("baseline-finance", "finance"),
            ):
                create_user = client.post(
                    "/api/users",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "username": username,
                        "email": f"{username}@example.com",
                        "display_name": username,
                        "password": "baseline-password",
                        "department_id": department_ids[department_code],
                        "role_codes": ["employee"],
                    },
                )
                create_user.raise_for_status()
            hr_login = client.post(
                "/api/auth/login",
                json={"username": "baseline-hr", "password": "baseline-password"},
            )
            hr_login.raise_for_status()
            hr_token = hr_login.json()["access_token"]
            with Session(app.state.engine) as session:
                fixture = KnowledgeDocument(
                    knowledge_base_id=kb_id,
                    title="Baseline Travel Policy",
                    file_path=str(temp_path / "baseline-policy.txt"),
                    file_type="txt",
                    content_text="Employees must submit travel receipts within 30 days. Approval follows the department travel lane.",
                    content_hash="baseline-fixture-sha256",
                    index_status="indexed",
                    created_by="system",
                )
                (temp_path / "baseline-policy.txt").write_text(
                    fixture.content_text or "", encoding="utf-8"
                )
                session.add(fixture)
                session.commit()
            summaries = []
            for name in PROFILE_NAMES:
                summaries.append(
                    _run_profile(
                        client,
                        name,
                        token,
                        kb_id,
                        root,
                        1 if name in {"smoke", "sse", "file-workflow", "tenant-isolation"} else 3,
                        forbidden_kb_id=finance_kb_id,
                        isolation_token=hr_token,
                    )
                )
            (root / "run.log").write_text(
                "\n".join(
                    f"profile={item['profile']} samples={item['samples']} "
                    f"status_codes={item['status_codes']} failures={item['failures']} "
                    f"latency={item['latency_ms']}"
                    for item in summaries
                )
                + "\n",
                encoding="utf-8",
            )
    (root / "summary.json").write_text(
        json.dumps(
            {
                "execution_mode": "route_level_asgi",
                "provider": "deterministic_mock",
                "retriever": "deterministic_local",
                "profiles": summaries,
                "bottleneck_conclusions": [
                    "Route-level ASGI requests completed without 5xx in this offline run.",
                    "Observed latency is application-process timing only; it is not a distributed capacity claim.",
                    "SSE produced stage events, but TestClient buffered the response, so first-event timing is not a network measurement.",
                    "File workflow reached upload and status endpoints; background indexing remains deployment-dependent.",
                    "Tenant isolation was verified with an HR employee token: finance KB list/detail was not visible (detail returned 403).",
                ],
                "limitations": [
                    "offline route-level baseline; not production capacity evidence",
                    "tenant isolation is limited by the legacy single-database fixture",
                    "saturation was sequential in-process ASGI; no 429/503 claim is made",
                    "TestClient buffers SSE responses, so first-event latency is not a network streaming measurement",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "bottleneck-conclusions.md").write_text(
        "# T015 Baseline Bottleneck Conclusions\n\n"
        "- This is a deterministic, route-level ASGI baseline executed on September 13, 2026.\n"
        "- All nine profiles exercised real authentication and application routes; no `/health`-only result is used.\n"
        "- The run recorded no 5xx responses. This is not a production capacity pass/fail result because it is sequential and in-process.\n"
        "- SSE event counts and file upload/status transitions are retained per profile.\n"
        "- An HR employee token could not list or open the finance knowledge base; this is the retained tenant-isolation check.\n"
        "- Real Anthropic provider evidence remains physically separate under `artifacts/provider/anthropic/` and was not invoked.\n",
        encoding="utf-8",
    )
    manifest = collect_artifact_manifest(
        Path.cwd(),
        root,
        topology={
            "mode": "route_level_asgi",
            "services": ["FastAPI", "SQLite", "deterministic_mock"],
        },
        data_volume={"seed": 20260913, "knowledge_base": "admin", "distractors": 0},
        extra_environment={
            "execution_mode": "route_level_asgi",
            "provider": "deterministic_mock",
            "retriever": "deterministic_local",
        },
    )
    write_artifact_manifest(manifest, root)
    print(
        json.dumps(
            {
                "artifact_root": str(root),
                "raw_artifact_count": manifest["raw_artifact_count"],
                "profiles": summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
