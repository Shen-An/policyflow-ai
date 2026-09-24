"""Real-corpus conclusion parity for the route cut-over (T054, real-stack arm).

Runs against real corpus. The embedding-backed strategies (lightrag / hybrid,
the default) require a real OpenAI-compatible embedding/LLM provider in `.env`
(LLM_BASE_URL / LLM_CHAT_MODEL / LLM_EMBEDDING_MODEL non-placeholder and the
LLM_API_KEY_ENV var populated); otherwise the run exits with
`PROVIDER_NOT_CONFIGURED` and writes nothing — it never fabricates a green. The
`bm25_only` strategy is pure lexical over `knowledge_documents.content_text` and
needs NO provider, so it runs the real-corpus cut-over parity for the lexical arm
even when no embedding endpoint is available.

When a provider is present it drives the REAL production stack through the HTTP
routes (so the exact cut-over branch runs):

  1. Import a small random CRUD `questanswer_1doc` sample + distractors into the
     dedicated eval_test 测试库 (`POST /api/eval/datasets/crud-import`).
  2. Run a **retrieval-only** eval (`eval_types=["retrieval"]`) twice against the
     one shared pipeline — once with ROUTE_VIA_GRAPH_ADAPTER OFF (legacy direct
     `execute_eval_run`) and once ON (via `GraphRouteAdapter.run_eval`).
  3. Assert the retrieval-side conclusions (ordered retrieved document ids +
     Hit@K/MRR retrieval_metrics + passed + score) are identical per case.

Retrieval is deterministic, so any drift would be the cut-over's fault. Generated
answer text is intentionally NOT compared: LLM sampling varies independently of
the flag and would produce false parity failures. Evidence is written to
`artifacts/graph/stage3/realcorpus_parity.json` (embedding arm) or
`realcorpus_parity_bm25.json` (lexical arm).

Usage:  python scripts/graph_realcorpus_parity.py [sample_size] [distractors] [strategy]
        strategy defaults to hybrid_lightrag_bm25; pass bm25_only for the
        provider-free lexical arm.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, col, select

from backend.app.core.config import get_settings
from backend.app.db.models import KnowledgeBase, KnowledgeDocument
from backend.app.main import create_app

_ARTIFACT = Path("artifacts/graph/stage3/realcorpus_parity.json")
_CRUD_SOURCE = r"D:\Coding\Code\Github\CRUD_RAG\data\crud_split\split_merged.json"

# The embedding arm (lightrag / hybrid) needs BOTH an embedding provider AND a
# chat provider (in-process LightRAG calls an LLM for graph extraction while it
# indexes). The two live on different hosts with different keys, so a single
# shared LLM_* / LLM_API_KEY_ENV in .env cannot express them. Instead the arm is
# driven by these env vars — secrets stay in the real environment, never in code
# or the repo. All six must be present or the embedding arm hard-gates.
_CHAT_ENVS = ("EVAL_CHAT_BASE_URL", "EVAL_CHAT_MODEL", "EVAL_CHAT_API_KEY")
_EMBED_ENVS = ("EVAL_EMBED_BASE_URL", "EVAL_EMBED_MODEL", "EVAL_EMBED_API_KEY")


def _provider_env_ready() -> tuple[bool, str]:
    """The embedding arm is runnable only when both provider trios are exported.

    This is the honest precondition for the split chat+embedding setup: BM25 needs
    neither, so the gate only applies to the embedding-backed strategies.
    """
    missing = [name for name in (*_CHAT_ENVS, *_EMBED_ENVS) if not os.environ.get(name)]
    if missing:
        return False, "missing env vars: " + ", ".join(missing)
    return True, "ok"


def _configure_provider(
    client: TestClient,
    capability: str,
    *,
    base_url: str,
    model: str,
    api_key: str,
    api_style: str,
    embedding_dimension: int | None = None,
) -> dict:
    payload: dict[str, object] = {
        "name": f"eval-{capability}",
        "base_url": base_url.rstrip("/"),
        "auth_mode": "bearer",
        "api_style": api_style,
        "api_key": api_key,
        "model": model,
        "timeout_seconds": 120.0,
        "enabled": True,
    }
    if capability == "embedding" and embedding_dimension is not None:
        payload["embedding_dimension"] = embedding_dimension
    resp = client.put(f"/api/settings/model-providers/{capability}", json=payload)
    resp.raise_for_status()
    return resp.json()


def _ensure_providers(client: TestClient) -> None:
    """Wire chat + embedding providers into the DB and prove both are reachable.

    Never fabricates readiness: a failing connectivity test aborts the run so a
    hollow (empty-index) parity can never be recorded.
    """
    _configure_provider(
        client,
        "chat",
        base_url=os.environ["EVAL_CHAT_BASE_URL"],
        model=os.environ["EVAL_CHAT_MODEL"],
        api_key=os.environ["EVAL_CHAT_API_KEY"],
        api_style="openai_chat_completions",
    )
    # Auto-detect the embedding dimension so the LightRAG vector store matches the
    # provider (a wrong embedding_dim fails vector insert). Configure, probe, and
    # re-configure if the probed dimension differs from any EVAL_EMBED_DIM hint.
    dim_hint = os.environ.get("EVAL_EMBED_DIM")
    _configure_provider(
        client,
        "embedding",
        base_url=os.environ["EVAL_EMBED_BASE_URL"],
        model=os.environ["EVAL_EMBED_MODEL"],
        api_key=os.environ["EVAL_EMBED_API_KEY"],
        api_style="openai_embeddings",
        embedding_dimension=int(dim_hint) if dim_hint else None,
    )

    chat_test = client.post("/api/settings/model-providers/chat/test")
    chat_test.raise_for_status()
    chat_result = chat_test.json()["result"]
    if chat_result["status"] != "passed":
        raise SystemExit(f"chat provider unreachable: {chat_result}")

    embed_test = client.post("/api/settings/model-providers/embedding/test")
    embed_test.raise_for_status()
    embed_result = embed_test.json()["result"]
    if embed_result["status"] != "passed":
        raise SystemExit(f"embedding provider unreachable: {embed_result}")

    probed_dim = embed_result.get("dimension")
    if probed_dim and (not dim_hint or int(dim_hint) != int(probed_dim)):
        _configure_provider(
            client,
            "embedding",
            base_url=os.environ["EVAL_EMBED_BASE_URL"],
            model=os.environ["EVAL_EMBED_MODEL"],
            api_key=os.environ["EVAL_EMBED_API_KEY"],
            api_style="openai_embeddings",
            embedding_dimension=int(probed_dim),
        )
    print(
        f"providers ready: chat={os.environ['EVAL_CHAT_MODEL']} "
        f"embedding={os.environ['EVAL_EMBED_MODEL']} dim={probed_dim}"
    )


def _wait_for_indexing(app, timeout_seconds: float = 900.0) -> dict[str, int]:
    """Block until every eval_test document leaves pending/indexing, then tally.

    LightRAG indexing runs in the background; the embedding-arm parity is only
    meaningful once the index is actually built, so this is a hard gate — a run
    that never reaches a terminal state raises rather than proceed on an empty
    index.
    """
    engine = app.state.engine
    deadline = time.time() + timeout_seconds
    while True:
        with Session(engine) as session:
            kb = session.exec(
                select(KnowledgeBase).where(KnowledgeBase.code == "eval_test")
            ).first()
            rows = (
                session.exec(
                    select(KnowledgeDocument.index_status).where(
                        col(KnowledgeDocument.knowledge_base_id) == kb.id
                    )
                ).all()
                if kb is not None
                else []
            )
        counts: dict[str, int] = {}
        for status in rows:
            counts[status] = counts.get(status, 0) + 1
        in_flight = counts.get("pending", 0) + counts.get("indexing", 0)
        if in_flight == 0:
            return counts
        if time.time() > deadline:
            raise SystemExit(f"indexing did not finish within {timeout_seconds}s: {counts}")
        time.sleep(2.0)


def _conclusion(result: dict) -> dict:
    """Deterministic retrieval-side verdict for one eval case (no latency/ids).

    List position IS the retrieval rank, so the ordered identity list captures
    the ranking without depending on a per-source `rank` key that the trace
    model may or may not carry.
    """
    return {
        "question": result["question"],
        "sources": [
            src.get("document_id") or src.get("chunk_id") or src.get("source_id")
            for src in result.get("retrieved_sources", [])
        ],
        "retrieval_metrics": result.get("retrieval_metrics"),
        "passed": result.get("passed"),
        "score": result.get("score"),
    }


def _run_eval(
    client: TestClient, item_ids: list[str], name: str, strategy: str
) -> list[dict]:
    payload = {
        "name": name,
        "retrieval_item_ids": item_ids,
        "eval_types": ["retrieval"],
        "retrieval_config": {"strategy": strategy, "top_k_values": [1, 3, 5, 10]},
    }
    created = client.post("/api/eval/runs", json=payload)
    created.raise_for_status()
    run_id = created.json()["id"]
    # Background execute_eval_run completes before TestClient returns, but poll to
    # be safe across Starlette versions. Terminal run statuses are the ones the
    # runner writes: success / failed / skipped (see EvalRunner.run).
    body: dict = {}
    for _ in range(120):
        read = client.get(f"/api/eval/runs/{run_id}")
        read.raise_for_status()
        body = read.json()
        if body["status"] in {"success", "failed", "skipped"}:
            break
        time.sleep(0.5)
    if body.get("status") != "success":
        raise SystemExit(
            f"eval run {run_id} ended {body.get('status')}: {body.get('error_summary')}"
        )
    return [_conclusion(r) for r in body["results"]]


def main() -> int:
    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    distractors = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    strategy = sys.argv[3] if len(sys.argv) > 3 else "hybrid_lightrag_bm25"

    # BM25 is pure lexical over knowledge_documents.content_text — it needs NO
    # embedding/LLM provider, so the provider gate applies only to the embedding-
    # backed strategies (lightrag / hybrid). The artifact records the strategy so a
    # lexical-arm parity is never mistaken for the embedding-arm one.
    needs_embeddings = strategy != "bm25_only"
    if needs_embeddings:
        ready, reason = _provider_env_ready()
        if not ready:
            print(f"PROVIDER_NOT_CONFIGURED: {reason}")
            print("Export the chat trio (EVAL_CHAT_BASE_URL / EVAL_CHAT_MODEL / "
                  "EVAL_CHAT_API_KEY) and the embedding trio (EVAL_EMBED_BASE_URL / "
                  "EVAL_EMBED_MODEL / EVAL_EMBED_API_KEY), then re-run.")
            print("(Or pass strategy 'bm25_only' as arg 3 to run the lexical arm "
                  "with no provider.)")
            return 2
    if not Path(_CRUD_SOURCE).exists():
        print(f"CORPUS_MISSING: {_CRUD_SOURCE}")
        return 3

    artifact = (
        _ARTIFACT
        if needs_embeddings
        else _ARTIFACT.with_name("realcorpus_parity_bm25.json")
    )

    app = create_app()
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={
                "username": get_settings().BOOTSTRAP_ADMIN_USERNAME,
                "password": get_settings().BOOTSTRAP_ADMIN_PASSWORD,
            },
        )
        login.raise_for_status()
        client.headers.update(
            {"Authorization": f"Bearer {login.json()['access_token']}"}
        )

        # Embedding arm only: wire chat+embedding providers into the DB and prove
        # both endpoints answer before importing (BM25 needs neither).
        if needs_embeddings:
            _ensure_providers(client)

        imported = client.post(
            "/api/eval/datasets/crud-import",
            json={
                "source_path": _CRUD_SOURCE,
                "task_type": "questanswer_1doc",
                "sample_size": sample_size,
                "distractor_count": distractors,
                # Skip background LightRAG indexing on the lexical-only arm; BM25
                # reads content_text directly.
                "index_documents": needs_embeddings,
            },
        )
        imported.raise_for_status()

        # The embedding arm's retrieval is only meaningful once the LightRAG index
        # is built. Block on it and refuse to proceed on a hollow (all-failed/empty)
        # index so parity can never be a vacuous match of two empty result sets.
        index_counts: dict[str, int] = {}
        if needs_embeddings:
            index_counts = _wait_for_indexing(app)
            print(f"indexing terminal states: {index_counts}")
            if index_counts.get("indexed", 0) == 0:
                raise SystemExit(
                    f"no documents indexed successfully; refusing hollow parity: {index_counts}"
                )

        items = client.get("/api/eval/retrieval-items", params={"enabled": True})
        items.raise_for_status()
        item_ids = [row["id"] for row in items.json()][:sample_size]
        if not item_ids:
            print("NO_RETRIEVAL_ITEMS after import")
            return 4

        # Set both arms explicitly — the cut-over made ROUTE_VIA_GRAPH_ADAPTER
        # default True, so we can no longer assume the process default here.
        app.state.settings.ROUTE_VIA_GRAPH_ADAPTER = False
        off = _run_eval(client, item_ids, "realcorpus-parity-off", strategy)

        app.state.settings.ROUTE_VIA_GRAPH_ADAPTER = True
        on = _run_eval(client, item_ids, "realcorpus-parity-on", strategy)

    telemetry = app.state.adapter_usage_telemetry
    parity = off == on
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {
                "verdict": "PARITY_OK" if parity else "PARITY_FAIL",
                "scope": (
                    f"real corpus (CRUD questanswer_1doc); retrieval-only, "
                    f"deterministic; strategy={strategy}"
                ),
                "strategy": strategy,
                "embedding_provider_used": needs_embeddings,
                "sample_size": len(item_ids),
                "distractor_count": distractors,
                "index_counts": index_counts or None,
                "route_eval_uses": telemetry.usage_count("route_eval"),
                "flag_off": off,
                "flag_on": on,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"{'PARITY_OK' if parity else 'PARITY_FAIL'} ({strategy}) -> {artifact}")
    print(f"route_eval telemetry uses: {telemetry.usage_count('route_eval')}")
    return 0 if parity else 1


if __name__ == "__main__":
    raise SystemExit(main())
