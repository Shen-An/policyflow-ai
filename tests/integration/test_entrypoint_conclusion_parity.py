"""Production-entrypoint conclusion parity for the route cut-over (T054).

Discharges the verifiable core of the Phase 3 Checkpoint's "100% conclusion
parity across production entrypoints" at the level this box can prove without a
live retrieval stack: the SAME question, driven through the real /api/chat and
/api/chat/stream routes, yields byte-identical *conclusions* whether the cut-over
flag is OFF (legacy direct call) or ON (via GraphRouteAdapter). Because both
states execute the one shared pipeline graph, this proves the adapter
indirection introduces zero conclusion drift, and that non-stream Chat and its
SSE stream agree.

Honest boundary (unchanged): this is deterministic-stub parity (F4LightRAG /
F4LLM), NOT real-corpus answer parity — the latter needs LightRAG/Milvus, absent
here, and remains outstanding (see specs/001-enterprise-agent-refactor/tasks.md
T054). Evidence is written to artifacts/graph/stage3/entrypoint_conclusion_parity.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app
from tests.test_f4_frontend_contract import (
    F4LightRAG,
    F4LLM,
    create_employee,
    login,
)

# Conclusion = the decision-bearing fields the frontend renders. Volatile
# identifiers (conversation/message/query_log ids) are excluded: they differ per
# run by design and say nothing about whether the graph reached the same verdict.
_VOLATILE = {
    "conversation_id",
    "message_id",
    "query_log_id",
    "awaiting_message_id",
    "diagnostics",
}
_ARTIFACT = Path("artifacts/graph/stage3/entrypoint_conclusion_parity.json")


def _conclusion(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k not in _VOLATILE}


def _build_app(tmp_path: Path):
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'parity.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        ROUTE_VIA_GRAPH_ADAPTER=False,
        _env_file=None,
    )
    return create_app(settings, lightrag_adapter=F4LightRAG(), llm_service=F4LLM())


def _parse_sse_final(body: str) -> dict:
    for block in body.split("\n\n"):
        event = None
        data = None
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):].strip()
        if event == "final" and data is not None:
            return json.loads(data)
    raise AssertionError("SSE stream carried no final event")


def _chat(client: TestClient, headers: dict[str, str], question: str) -> dict:
    response = client.post("/api/chat", headers=headers, json={"question": question})
    assert response.status_code == 200, response.text
    return response.json()


def _chat_stream(client: TestClient, headers: dict[str, str], question: str) -> dict:
    response = client.post(
        "/api/chat/stream", headers=headers, json={"question": question}
    )
    assert response.status_code == 200, response.text
    return _parse_sse_final(response.text)


# Evidence-present + evidence-absent (hard-refuse) both exercised so parity
# covers the accept and the fail-closed branch of the shared evidence gate.
_QUESTIONS = ("What is the travel approval process?", "unknown policy question")


def _collect(client: TestClient, headers: dict[str, str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for index, question in enumerate(_QUESTIONS):
        out[f"chat_{index}"] = _conclusion(_chat(client, headers, question))
        out[f"stream_{index}"] = _conclusion(_chat_stream(client, headers, question))
    return out


def test_chat_and_stream_conclusions_are_identical_across_flag_states(
    tmp_path: Path,
) -> None:
    # One app / one database so seeded identifiers (knowledge-base UUIDs etc.)
    # are stable: the ONLY variable between the two runs is the cut-over flag,
    # toggled at runtime (routes read request.app.state.settings per request).
    # Separate users keep the two runs' memory effects symmetric.
    app = _build_app(tmp_path)
    with TestClient(app) as client:
        admin = login(client, "admin", "test-password")

        off_user = create_employee(client, admin, "parityoff", "hr")
        assert app.state.settings.ROUTE_VIA_GRAPH_ADAPTER is False
        off = _collect(client, off_user)

        on_user = create_employee(client, admin, "parityon", "hr")
        app.state.settings.ROUTE_VIA_GRAPH_ADAPTER = True
        on = _collect(client, on_user)

    telemetry = app.state.adapter_usage_telemetry

    # 1. The flag must not change any conclusion (adapter is pure indirection).
    assert on == off

    # 2. Non-stream Chat and its SSE stream must agree per question and state:
    #    the single shared graph reaches one verdict regardless of entrypoint.
    for source in (off, on):
        for index in range(len(_QUESTIONS)):
            assert source[f"chat_{index}"] == source[f"stream_{index}"]

    # 3. Only the flag-ON run feeds the removal-ledger telemetry.
    assert telemetry.usage_count("route_chat") == len(_QUESTIONS)
    assert telemetry.usage_count("route_chat_stream") == len(_QUESTIONS)

    _ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    _ARTIFACT.write_text(
        json.dumps(
            {
                "verdict": "PARITY_OK",
                "scope": "deterministic-stub (F4LightRAG/F4LLM); NOT real-corpus",
                "entrypoints": ["/api/chat", "/api/chat/stream"],
                "questions": list(_QUESTIONS),
                "route_chat_uses": telemetry.usage_count("route_chat"),
                "route_chat_stream_uses": telemetry.usage_count("route_chat_stream"),
                "conclusions": on,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
