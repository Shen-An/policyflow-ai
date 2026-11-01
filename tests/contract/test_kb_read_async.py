"""Contract: knowledge-base reads go through the async repository, not the sync session.

`T035` moves a service's authoritative reads onto the tenant-aware async
repository.  The first slice converts the four read-only knowledge-base
endpoints (`GET /api/knowledge-bases`, `/api/departments`,
`/api/knowledge-bases/create-options`, `/api/knowledge-bases/{id}`).

The point of the slice is not a cosmetic `async def`: the handler must no longer
receive a synchronous `Session`, it must consume the async `UnitOfWork`, and the
database work must really happen inside a running event loop (a worker thread
would have none).  The last test also pins that falsification direction, so the
suite cannot pass vacuously if every route looked migrated.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.api import routes_kb
from backend.app.db.repositories import KnowledgeBaseRepository
from tests.test_phase1_knowledge import build_knowledge_app, headers, login

MIGRATED_HANDLERS = (
    "get_knowledge_bases",
    "get_departments",
    "get_create_options",
    "get_knowledge_base",
)

# A sibling endpoint on the same router that is deliberately still synchronous.
# It shares the auth/permission plumbing, so it is a fair control.
SYNC_CONTROL_HANDLER = "get_documents"


@pytest.mark.parametrize("handler_name", MIGRATED_HANDLERS)
def test_read_handler_is_async_and_drops_the_sync_session(handler_name: str) -> None:
    handler = getattr(routes_kb, handler_name)

    assert asyncio.iscoroutinefunction(handler), (
        f"{handler_name} must be a coroutine function so the repository await "
        "stays on the event loop"
    )

    parameters = inspect.signature(handler).parameters
    assert "session" not in parameters, (
        f"{handler_name} must not take the synchronous SessionDep any more"
    )
    assert "uow" in parameters, f"{handler_name} must consume UnitOfWorkDep"
    assert "get_unit_of_work" in str(parameters["uow"].annotation)


def test_the_sync_control_handler_is_still_synchronous() -> None:
    # Falsification by contrast: the documents listing still owns a sync
    # Session, so the two properties above must not hold for it.  Without this,
    # a refactor that accidentally made the checks vacuous would go unnoticed.
    control = getattr(routes_kb, SYNC_CONTROL_HANDLER)

    assert not asyncio.iscoroutinefunction(control)
    assert "session" in inspect.signature(control).parameters
    assert "uow" not in inspect.signature(control).parameters


def test_repository_read_happens_on_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_knowledge_app(tmp_path)
    seen_in_loop: list[bool] = []
    original_list_active = KnowledgeBaseRepository.list_active

    async def spy(self: KnowledgeBaseRepository, tenant_id: str | None):
        try:
            asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - would mean a worker thread
            seen_in_loop.append(False)
        else:
            seen_in_loop.append(True)
        return await original_list_active(self, tenant_id)

    monkeypatch.setattr(KnowledgeBaseRepository, "list_active", spy)

    with TestClient(app) as client:
        token = login(client, "admin", "test-password")
        response = client.get("/api/knowledge-bases", headers=headers(token))

    assert response.status_code == 200
    assert seen_in_loop, "the async repository was never used by the read endpoint"
    assert all(seen_in_loop), (
        "KnowledgeBaseRepository.list_active ran without a running event loop, "
        "so the read is still happening on a worker thread"
    )
    assert "items" in response.json()
