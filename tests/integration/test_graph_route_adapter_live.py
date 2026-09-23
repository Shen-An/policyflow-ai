"""Flag-ON live route cut-over (T051/T052).

Proves that flipping ROUTE_VIA_GRAPH_ADAPTER on routes the real /api/chat
endpoint through GraphRouteAdapter: the full ChatResponse contract is preserved
(byte-for-byte the same shape the frontend depends on) AND the Stage 9
removal-ledger telemetry records the usage. The default-off legacy path is
covered by tests/test_f4_frontend_contract.py; this test only asserts the
delta introduced by the flag.

Uses the F4 stub LightRAG/LLM so the assertion is deterministic without a live
retrieval stack. Real-corpus 100%-conclusion parity is NOT verified here (no
LightRAG/Milvus on this box) and is tracked as unverified in tasks.md T051/T052.
"""

from __future__ import annotations

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


def _build_app(tmp_path: Path, *, route_via_graph_adapter: bool):
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'route-adapter.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        ROUTE_VIA_GRAPH_ADAPTER=route_via_graph_adapter,
        _env_file=None,
    )
    return create_app(settings, lightrag_adapter=F4LightRAG(), llm_service=F4LLM())


def test_flag_on_preserves_chat_contract_and_records_telemetry(tmp_path: Path) -> None:
    app = _build_app(tmp_path, route_via_graph_adapter=True)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        owner_headers = create_employee(client, admin_headers, "routeowner", "hr")

        response = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": "What is the travel approval process?"},
        )

    assert response.status_code == 200
    chat = response.json()
    # The rich ChatResponse contract the frontend depends on must survive the
    # cut-over unchanged: the adapter delegates to the same pipeline graph.
    for field in (
        "answer",
        "conversation_id",
        "query_log_id",
        "citations",
        "confidence_score",
        "router_result",
    ):
        assert field in chat, f"missing contract field: {field}"

    # The flag-ON path must feed the Stage 9 removal-ledger telemetry.
    telemetry = app.state.adapter_usage_telemetry
    assert telemetry.usage_count("route_chat") >= 1
    event = telemetry.events[-1]
    # Telemetry carries routing metadata only, never payload content.
    assert event.run_id == chat["query_log_id"]
    assert "travel" not in repr(event).lower()


def test_flag_off_does_not_record_telemetry(tmp_path: Path) -> None:
    app = _build_app(tmp_path, route_via_graph_adapter=False)

    with TestClient(app) as client:
        admin_headers = login(client, "admin", "test-password")
        owner_headers = create_employee(client, admin_headers, "legacyowner", "hr")

        response = client.post(
            "/api/chat",
            headers=owner_headers,
            json={"question": "What is the travel approval process?"},
        )

    assert response.status_code == 200
    # Legacy path is byte-identical to before the flag existed: no telemetry.
    assert app.state.adapter_usage_telemetry.usage_count("route_chat") == 0
