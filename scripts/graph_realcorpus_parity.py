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

from backend.app.core.config import get_settings
from backend.app.main import create_app

_ARTIFACT = Path("artifacts/graph/stage3/realcorpus_parity.json")
_CRUD_SOURCE = r"D:\Coding\Code\Github\CRUD_RAG\data\crud_split\split_merged.json"


def _provider_ready() -> tuple[bool, str]:
    s = get_settings()
    base_url = s.LLM_BASE_URL or ""
    chat_model = s.LLM_CHAT_MODEL or ""
    embed_model = s.LLM_EMBEDDING_MODEL or ""
    if not base_url or "example.com" in base_url:
        return False, "LLM_BASE_URL unset or the placeholder api.example.com"
    if not chat_model or not embed_model or chat_model.startswith("your-") or embed_model.startswith("your-"):
        return False, "LLM_CHAT_MODEL/LLM_EMBEDDING_MODEL unset or placeholders"
    if not os.environ.get(s.LLM_API_KEY_ENV, ""):
        return False, f"env var {s.LLM_API_KEY_ENV} (LLM_API_KEY_ENV) is empty"
    return True, "ok"


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
        ready, reason = _provider_ready()
        if not ready:
            print(f"PROVIDER_NOT_CONFIGURED: {reason}")
            print("Set real LLM_BASE_URL / LLM_CHAT_MODEL / LLM_EMBEDDING_MODEL in .env")
            print("and export the key named by LLM_API_KEY_ENV, then re-run.")
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
