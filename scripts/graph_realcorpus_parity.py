"""Real-corpus conclusion parity for the route cut-over (T054, real-stack arm).

Runs ONLY when a real OpenAI-compatible embedding/LLM provider is configured
in `.env` (LLM_BASE_URL / LLM_CHAT_MODEL / LLM_EMBEDDING_MODEL non-placeholder
and the LLM_API_KEY_ENV var populated). Otherwise it exits with
`PROVIDER_NOT_CONFIGURED` and writes nothing — it never fabricates a green.

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
`artifacts/graph/stage3/realcorpus_parity.json`.

Usage:  python scripts/graph_realcorpus_parity.py [sample_size] [distractors]
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


def _run_eval(client: TestClient, item_ids: list[str], name: str) -> list[dict]:
    payload = {
        "name": name,
        "retrieval_item_ids": item_ids,
        "eval_types": ["retrieval"],
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
    ready, reason = _provider_ready()
    if not ready:
        print(f"PROVIDER_NOT_CONFIGURED: {reason}")
        print("Set real LLM_BASE_URL / LLM_CHAT_MODEL / LLM_EMBEDDING_MODEL in .env")
        print("and export the key named by LLM_API_KEY_ENV, then re-run.")
        return 2
    if not Path(_CRUD_SOURCE).exists():
        print(f"CORPUS_MISSING: {_CRUD_SOURCE}")
        return 3

    sample_size = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    distractors = int(sys.argv[2]) if len(sys.argv) > 2 else 200

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
            },
        )
        imported.raise_for_status()

        items = client.get("/api/eval/retrieval-items", params={"enabled": True})
        items.raise_for_status()
        item_ids = [row["id"] for row in items.json()][:sample_size]
        if not item_ids:
            print("NO_RETRIEVAL_ITEMS after import")
            return 4

        assert app.state.settings.ROUTE_VIA_GRAPH_ADAPTER is False
        off = _run_eval(client, item_ids, "realcorpus-parity-off")

        app.state.settings.ROUTE_VIA_GRAPH_ADAPTER = True
        on = _run_eval(client, item_ids, "realcorpus-parity-on")

    telemetry = app.state.adapter_usage_telemetry
    parity = off == on
    _ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    _ARTIFACT.write_text(
        json.dumps(
            {
                "verdict": "PARITY_OK" if parity else "PARITY_FAIL",
                "scope": "real corpus (CRUD questanswer_1doc); retrieval-only, deterministic",
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
    print(f"{'PARITY_OK' if parity else 'PARITY_FAIL'} -> {_ARTIFACT}")
    print(f"route_eval telemetry uses: {telemetry.usage_count('route_eval')}")
    return 0 if parity else 1


if __name__ == "__main__":
    raise SystemExit(main())
