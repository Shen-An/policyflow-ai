"""Injected services the graph nodes call (T045, T047, T049, T050).

Nodes never reach into concrete services directly. They depend on this narrow,
async protocol so the *same* graph can run three ways with no branching in the
node bodies:

- **Deterministic** (:class:`DeterministicGraphDependencies`) for eval and the
  parity/contract tests — fixed, side-effect-free outputs.
- **Production** (wired in :func:`backend.app.graph.service`) against real query
  rewrite, four-layer memory, retrieval and the Claude provider.

Keeping memory non-authoritative is a contract, not a convenience: the memory
snapshot returned here can shape phrasing but is never a retrieval candidate and
can never satisfy the evidence gate.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from backend.app.graph.evidence_gate import EvidenceCandidate

__all__ = [
    "DeterministicGraphDependencies",
    "GraphDependencies",
    "ProductionGraphDependencies",
]


@runtime_checkable
class GraphDependencies(Protocol):
    """The async services a graph run needs. All calls are side-effect free
    except the provider generate call, which is bounded by the run deadline."""

    async def rewrite_query(
        self, *, query: str, history: list[dict[str, Any]]
    ) -> str: ...

    async def load_memory(
        self, *, tenant_id: str, user_id: str, conversation_id: str | None
    ) -> dict[str, Any]: ...

    async def retrieve(
        self, *, tenant_id: str, query: str, knowledge_version: str
    ) -> list[EvidenceCandidate]: ...

    async def generate(
        self, *, query: str, accepted_evidence: list[EvidenceCandidate]
    ) -> str: ...


class DeterministicGraphDependencies:
    """Fixed, reproducible dependencies for eval and contract parity.

    Produces exactly one relevant, current-tenant knowledge-base candidate so
    the evidence gate deterministically resolves ``supported`` — the anchor the
    entrypoint-parity fingerprint asserts.
    """

    def __init__(self, *, tenant_id: str = "tenant-a") -> None:
        self._tenant_id = tenant_id

    async def rewrite_query(self, *, query: str, history: list[dict[str, Any]]) -> str:
        return query.strip()

    async def load_memory(
        self, *, tenant_id: str, user_id: str, conversation_id: str | None
    ) -> dict[str, Any]:
        return {"window": [], "summary": None, "selected_items": []}

    async def retrieve(
        self, *, tenant_id: str, query: str, knowledge_version: str
    ) -> list[EvidenceCandidate]:
        return [
            EvidenceCandidate(
                evidence_id="evidence-1",
                tenant_id=tenant_id,
                knowledge_base_id="kb-1",
                document_version=knowledge_version,
                content="Employees may claim approved travel expenses.",
                relevance_score=0.95,
                retrievable=True,
                source_type="knowledge_base",
            )
        ]

    async def generate(
        self, *, query: str, accepted_evidence: list[EvidenceCandidate]
    ) -> str:
        if not accepted_evidence:
            return "Insufficient evidence to answer."
        return "Based on policy: " + accepted_evidence[0].content


class ProductionGraphDependencies:
    """Composes the real services behind the graph via constructor injection.

    Retrieval and memory access need a live tenant-scoped session, so they are
    injected as async callables rather than imported directly. This keeps the
    dependency object testable and free of import-time coupling to the running
    stack (PostgreSQL / Milvus / LightRAG). The route layer builds one per
    request with the session-bound callables and the shared Claude provider.
    """

    def __init__(
        self,
        *,
        retrieve_fn: Any,
        generate_fn: Any,
        memory_fn: Any | None = None,
        rewrite_fn: Any | None = None,
    ) -> None:
        self._retrieve_fn = retrieve_fn
        self._generate_fn = generate_fn
        self._memory_fn = memory_fn
        self._rewrite_fn = rewrite_fn

    async def rewrite_query(self, *, query: str, history: list[dict[str, Any]]) -> str:
        if self._rewrite_fn is None:
            from backend.app.services.query_rewrite import graph_rewrite_query

            return await graph_rewrite_query(query=query, history=history)
        return await self._rewrite_fn(query=query, history=history)

    async def load_memory(
        self, *, tenant_id: str, user_id: str, conversation_id: str | None
    ) -> dict[str, Any]:
        if self._memory_fn is None:
            return {"window": [], "summary": None, "selected_items": [], "authoritative": False}
        return await self._memory_fn(
            tenant_id=tenant_id, user_id=user_id, conversation_id=conversation_id
        )

    async def retrieve(
        self, *, tenant_id: str, query: str, knowledge_version: str
    ) -> list[EvidenceCandidate]:
        return await self._retrieve_fn(
            tenant_id=tenant_id, query=query, knowledge_version=knowledge_version
        )

    async def generate(
        self, *, query: str, accepted_evidence: list[EvidenceCandidate]
    ) -> str:
        return await self._generate_fn(query=query, accepted_evidence=accepted_evidence)
