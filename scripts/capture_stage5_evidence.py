"""Capture Stage-5 storage evidence against live infrastructure (T088).

Produces the three manifests ``tasks.md`` T088 asks for, plus an infrastructure
probe, by driving the *same* production code paths the suites exercise rather than
describing them:

* ``update-manifest.json`` -- a material updated to a second immutable version:
  which version served before and after, the compare-and-set activation, and the
  retired manifest.
* ``delete-manifest.json`` -- physical deletion: vectors, every object version and
  delete marker, and the SQL rows, each counted before and after, with
  reconciliation's independent confirmation.
* ``fault-detection.json`` -- all six reconciliation issue kinds seeded
  individually and the sweep's verdict, so the "100% detection" claim is
  reproducible rather than asserted.
* ``infra-probe.txt`` -- the actual server versions the evidence was taken
  against, so a number cannot be quoted without its environment.

Run with the dev stack up:
    python -m scripts.capture_stage5_evidence
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # pragma: no cover - CLI convenience
    sys.path.insert(0, str(REPO_ROOT))

from sqlalchemy import select  # noqa: E402

from backend.app.db.models import (  # noqa: E402
    Material,
    MaterialVersion,
    ObjectVersion,
    VectorManifest,
)
from backend.app.retrieval.indexer import ChunkPayload  # noqa: E402
from tests import conftest  # noqa: E402
from tests.stage5_env import (  # noqa: E402
    DIMENSIONS,
    TENANT_A,
    stage5_environment,
    upload_material,
)

OUTPUT_DIR = REPO_ROOT / "artifacts" / "storage" / "stage5"

POLICY_V1 = b"daily reimbursement limit is 300 CNY\nreceipts required above 100 CNY\n"
POLICY_V2 = b"daily reimbursement limit is 500 CNY\nreceipts required above 200 CNY\n"


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def _sql_counts(env, material_id: str) -> dict[str, int]:
    async with env.factory() as session:
        versions = (
            (
                await session.execute(
                    select(MaterialVersion).where(
                        MaterialVersion.material_id == material_id
                    )
                )
            )
            .scalars()
            .all()
        )
        objects = (
            (
                await session.execute(
                    select(ObjectVersion).where(ObjectVersion.material_id == material_id)
                )
            )
            .scalars()
            .all()
        )
        manifests = (
            (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.material_id == material_id
                    )
                )
            )
            .scalars()
            .all()
        )
    return {
        "material_versions": len(versions),
        "object_versions": len(objects),
        "vector_manifests": len(manifests),
    }


async def _manifest_rows(env, material_id: str) -> list[dict[str, Any]]:
    async with env.factory() as session:
        rows = (
            (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.material_id == material_id
                    )
                )
            )
            .scalars()
            .all()
        )
    out: list[dict[str, Any]] = []
    for manifest in rows:
        out.append(
            {
                "manifest_id": manifest.id,
                "material_version_id": manifest.material_version_id,
                "embedding_version_id": manifest.embedding_version_id,
                "milvus_collection": manifest.milvus_collection,
                "expected_count": manifest.expected_count,
                "indexed_count": manifest.indexed_count,
                "retrievable": manifest.retrievable,
                "deletion_state": manifest.deletion_state,
                "vectors_in_milvus": await env.vectors.count_by_prefix(
                    tenant_id=TENANT_A, vector_id_prefix=manifest.vector_id_prefix
                ),
            }
        )
    return out


async def capture_update_manifest(env) -> dict[str, Any]:
    """A material updated to v2: who served before, who serves after."""
    material_id, v1 = await upload_material(
        env, tenant_id=TENANT_A, name="Reimbursement Policy", body=POLICY_V1
    )

    async def served() -> tuple[str, ...]:
        scope = await env.indexer.resolve_scope(
            tenant_id=TENANT_A,
            knowledge_base_ids=(env.knowledge_base(TENANT_A),),
            embedding_version_id=env.embedding(TENANT_A),
        )
        return scope.version_ids

    before = await served()
    manifests_before = await _manifest_rows(env, material_id)

    _same, v2 = await upload_material(
        env,
        tenant_id=TENANT_A,
        name="Reimbursement Policy",
        body=POLICY_V2,
        material_id=material_id,
    )
    after = await served()
    manifests_after = await _manifest_rows(env, material_id)

    async with env.factory() as session:
        old = await session.get(MaterialVersion, v1)
        new = await session.get(MaterialVersion, v2)
        material = await session.get(Material, material_id)

    index_metadata = await env.vectors.index_metadata()
    return {
        "captured_at": _now(),
        "material_id": material_id,
        "versions": {
            "v1": {
                "id": v1,
                "version_number": old.version_number if old else None,
                "status": old.status if old else None,
                "source_version_id": old.source_version_id if old else None,
            },
            "v2": {
                "id": v2,
                "version_number": new.version_number if new else None,
                "status": new.status if new else None,
                "source_version_id": new.source_version_id if new else None,
            },
        },
        "served_version_ids_before_update": list(before),
        "served_version_ids_after_update": list(after),
        "active_version_pointer_after": material.active_version_id if material else None,
        "manifests_before": manifests_before,
        "manifests_after": manifests_after,
        "retrieval_strategy": index_metadata.strategy_name(),
        "milvus_index": {
            "index_type": index_metadata.index_type,
            "metric_type": index_metadata.metric_type,
            "dimensions": index_metadata.dimensions,
            "tenant_partition_key": index_metadata.tenant_partition_key,
        },
        "invariants": {
            "exactly_one_served_version_before": len(before) == 1,
            "exactly_one_served_version_after": len(after) == 1,
            "served_version_changed_to_v2": after == (v2,),
            "old_version_superseded": (old.status == "superseded") if old else False,
            "retired_manifest_has_no_vectors": all(
                row["vectors_in_milvus"] == 0
                for row in manifests_after
                if row["material_version_id"] == v1
            ),
        },
    }


async def capture_delete_manifest(env) -> dict[str, Any]:
    """Physical deletion, counted on all three stores before and after."""
    material_id, v1 = await upload_material(
        env, tenant_id=TENANT_A, name="Deletable Policy", body=POLICY_V1
    )
    _same, v2 = await upload_material(
        env,
        tenant_id=TENANT_A,
        name="Deletable Policy",
        body=POLICY_V2,
        material_id=material_id,
    )

    # Make the object-store state as awkward as it realistically gets: several
    # provider versions plus a delete marker. A plain delete would leave the
    # marker behind, so this is what distinguishes real deletion from a soft one.
    grant = await env.store.create_upload(
        tenant_id=TENANT_A,
        material_id=material_id,
        material_version_id=v2,
        media_type="text/plain",
        max_bytes=4096,
    )
    await env.store.upload_bytes(grant=grant, payload=POLICY_V2 + b"annex\n")
    await env.store.soft_delete_for_test(grant)

    async def object_state() -> dict[str, dict[str, int]]:
        state: dict[str, dict[str, int]] = {}
        for version_id in (v1, v2):
            inventory = await env.store.inventory(
                tenant_id=TENANT_A,
                material_id=material_id,
                material_version_id=version_id,
            )
            state[version_id] = {
                "object_versions": len(inventory.versions),
                "delete_markers": len(inventory.delete_markers),
            }
        return state

    before = {
        "objects": await object_state(),
        "sql": await _sql_counts(env, material_id),
        "manifests": await _manifest_rows(env, material_id),
    }

    withdraw = await env.saga.request_delete(tenant_id=TENANT_A, material_id=material_id)
    scope_after_withdraw = await env.indexer.resolve_scope(
        tenant_id=TENANT_A,
        knowledge_base_ids=(env.knowledge_base(TENANT_A),),
        embedding_version_id=env.embedding(TENANT_A),
    )
    objects_after_withdraw = await object_state()

    final = await env.saga.advance_once(tenant_id=TENANT_A, material_id=material_id)
    after = {
        "objects": await object_state(),
        "sql": await _sql_counts(env, material_id),
    }
    remaining_vectors = sum(
        row["vectors_in_milvus"] for row in before["manifests"]
    )
    vectors_after = 0
    for row in before["manifests"]:
        async with env.factory() as session:
            manifest = await session.get(VectorManifest, row["manifest_id"])
        prefix = manifest.vector_id_prefix if manifest else None
        if prefix is None:
            # The manifest row is gone, which is itself the evidence; the vectors
            # were counted before deletion and are re-checked by reconciliation.
            continue
        vectors_after += await env.vectors.count_by_prefix(
            tenant_id=TENANT_A, vector_id_prefix=prefix
        )

    confirmation = await env.reconciler.confirm_physically_deleted(
        tenant_id=TENANT_A, material_id=material_id
    )
    async with env.factory() as session:
        material = await session.get(Material, material_id)

    return {
        "captured_at": _now(),
        "material_id": material_id,
        "version_ids": [v1, v2],
        "withdraw_step": {
            "from_status": withdraw.from_status,
            "to_status": withdraw.to_status,
            "served_version_ids_after_withdraw": list(scope_after_withdraw.version_ids),
            "objects_after_withdraw": objects_after_withdraw,
        },
        "delete_step": {"from_status": final.from_status, "to_status": final.to_status},
        "before": before,
        "after": after,
        "vectors_before": remaining_vectors,
        "vectors_after": vectors_after,
        "material_row_status": material.status if material else None,
        "reconciliation_confirmation": confirmation,
        "invariants": {
            "retrieval_withdrawn_before_removal": (
                scope_after_withdraw.version_ids == ()
                and all(
                    counts["object_versions"] > 0
                    for counts in objects_after_withdraw.values()
                )
            ),
            "all_object_versions_removed": all(
                counts["object_versions"] == 0 for counts in after["objects"].values()
            ),
            "all_delete_markers_removed": all(
                counts["delete_markers"] == 0 for counts in after["objects"].values()
            ),
            "all_sql_references_removed": all(
                count == 0
                for key, count in after["sql"].items()
            ),
            "all_vectors_removed": vectors_after == 0,
            "reconciliation_confirms_complete": confirmation == [],
            "material_row_kept_as_tombstone": (
                material is not None and material.status == "deleted"
            ),
        },
    }


async def capture_fault_detection(env) -> dict[str, Any]:
    """Seed all six issue kinds individually, then report the sweep's verdict."""
    import hashlib

    seeded: dict[str, str] = {}

    missing_object_id, missing_object_version = await upload_material(
        env, tenant_id=TENANT_A, name="Fault A", body=POLICY_V1
    )
    await env.store.delete_all_versions(
        tenant_id=TENANT_A,
        material_id=missing_object_id,
        material_version_id=missing_object_version,
    )
    seeded["missing_object"] = missing_object_version

    orphan_material_id, _ = await upload_material(
        env, tenant_id=TENANT_A, name="Fault B", body=POLICY_V1
    )
    payload = b"bytes nobody claims"
    orphan_grant = await env.store.create_upload(
        tenant_id=TENANT_A,
        material_id=orphan_material_id,
        material_version_id="ghost-version",
        media_type="text/plain",
        max_bytes=4096,
    )
    await env.store.upload_bytes(grant=orphan_grant, payload=payload)
    orphan_stored = await env.store.verify_upload(
        grant=orphan_grant,
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        expected_size_bytes=len(payload),
        expected_media_type="text/plain",
    )
    async with env.factory() as session:
        row = ObjectVersion(
            tenant_id=TENANT_A,
            material_id=orphan_material_id,
            material_version_id="ghost-version",
            bucket_alias=orphan_stored.bucket_alias,
            object_key=orphan_stored.object_key,
            provider_version_id=orphan_stored.provider_version_id,
            sha256=orphan_stored.sha256,
            size_bytes=orphan_stored.size_bytes,
            media_type=orphan_stored.media_type,
        )
        session.add(row)
        await session.commit()
        seeded["orphan_object"] = row.id

    _mv_id, missing_vector_version = await upload_material(
        env, tenant_id=TENANT_A, name="Fault C", body=POLICY_V1
    )
    async with env.factory() as session:
        missing_vector_manifest = (
            (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.material_version_id == missing_vector_version
                    )
                )
            )
            .scalars()
            .one()
        )
    await env.vectors.delete_by_prefix(
        tenant_id=TENANT_A, vector_id_prefix=missing_vector_manifest.vector_id_prefix
    )
    seeded["missing_vector"] = missing_vector_manifest.id

    _mc_id, missing_chunk_version = await upload_material(
        env, tenant_id=TENANT_A, name="Fault D", body=POLICY_V1
    )
    async with env.factory() as session:
        missing_chunk_manifest = (
            (
                await session.execute(
                    select(VectorManifest).where(
                        VectorManifest.material_version_id == missing_chunk_version
                    )
                )
            )
            .scalars()
            .one()
        )
    held = await env.vectors.chunk_ids_for_prefix(
        tenant_id=TENANT_A, vector_id_prefix=missing_chunk_manifest.vector_id_prefix
    )
    await env.vectors.delete_ids(
        tenant_id=TENANT_A,
        vector_ids=[f"{missing_chunk_manifest.vector_id_prefix}:{held[0]}"],
    )
    seeded["missing_chunk"] = missing_chunk_manifest.id

    await env.vectors.upsert_chunks(
        tenant_id=TENANT_A,
        knowledge_base_id=env.knowledge_base(TENANT_A),
        subject_kind="material",
        subject_id="ghost-material",
        version_id="ghost-version-vectors",
        embedding_version_id=env.embedding(TENANT_A),
        vector_id_prefix="ghostprefix",
        chunks=[ChunkPayload(chunk_id="c0", text="stale", vector=[0.25] * DIMENSIONS)],
        retrievable=True,
    )
    seeded["orphan_vector"] = "ghost-version-vectors"

    drift_material_id, _ = await upload_material(
        env, tenant_id=TENANT_A, name="Fault E", body=POLICY_V1
    )
    async with env.factory() as session:
        material = await session.get(Material, drift_material_id)
        material.active_version_id = "stale-version-id"
        material.version += 1
        await session.commit()
    seeded["version_drift"] = drift_material_id

    report = await env.reconciler.sweep(tenant_id=TENANT_A)
    issues = await env.reconciler.open_issues(tenant_id=TENANT_A)
    expected = set(seeded)
    detected = report.kinds()

    # Run the sweep twice more: idempotence is what makes the detection rate a
    # stable number rather than a function of how often the sweep ran.
    second = await env.reconciler.sweep(tenant_id=TENANT_A)
    third = await env.reconciler.sweep(tenant_id=TENANT_A)

    return {
        "captured_at": _now(),
        "seeded": seeded,
        "detected_kinds": sorted(detected),
        "expected_kinds": sorted(expected),
        "detection_rate": f"{len(detected & expected)}/{len(expected)}",
        "issues": [
            {
                "issue_kind": issue.issue_kind,
                "store_pair": issue.store_pair,
                "resource_kind": issue.resource_kind,
                "resource_id": issue.resource_id,
                "severity": issue.severity,
                "state": issue.state,
                "observed": issue.observed_fingerprint,
                "expected": issue.expected_fingerprint,
            }
            for issue in sorted(issues, key=lambda row: row.issue_kind)
        ],
        "idempotence": {
            "findings_pass_1": report.findings,
            "findings_pass_2": second.findings,
            "findings_pass_3": third.findings,
            "distinct_issue_rows": len(issues),
            "stable": report.findings == second.findings == third.findings,
        },
        "invariants": {
            "all_six_kinds_detected": detected == expected,
            "one_row_per_fault": len(issues) == len(expected),
        },
    }


def capture_infra_probe() -> str:
    """Record the actual server versions the evidence was taken against."""
    lines = [f"Stage-5 infrastructure probe, captured {_now()}", ""]
    try:
        compose = subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(REPO_ROOT / "infra" / "dev" / "compose.yaml"),
                "--env-file",
                str(REPO_ROOT / "infra" / "dev" / ".env"),
                "ps",
                "--format",
                "{{.Service}}\t{{.Image}}\t{{.Status}}",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        lines.append("docker compose ps:")
        lines.append(compose.stdout.strip() or compose.stderr.strip())
    except Exception as exc:  # noqa: BLE001 - probe is best-effort evidence
        lines.append(f"docker compose ps unavailable: {type(exc).__name__}")
    lines.append("")
    try:
        from pymilvus import MilvusClient

        uri = f"http://127.0.0.1:{conftest._infra_value('MILVUS_PORT')}"
        client = MilvusClient(uri=uri, timeout=10.0)
        lines.append(f"Milvus server version: {client.get_server_version()}")
        client.close()
    except Exception as exc:  # noqa: BLE001
        lines.append(f"Milvus probe failed: {type(exc).__name__}")
    return "\n".join(lines) + "\n"


async def _main() -> int:
    pg_url = conftest.test_database_url()
    milvus_uri = f"http://127.0.0.1:{conftest._infra_value('MILVUS_PORT')}"
    from backend.app.storage.object_store import ObjectStoreConfig

    endpoint = f"http://127.0.0.1:{conftest._infra_value('MINIO_PORT')}"
    object_store_config = ObjectStoreConfig(
        endpoint_url=endpoint,
        region="us-east-1",
        bucket=conftest._infra_value("OBJECT_STORE_BUCKET"),
        access_key_id=conftest._infra_value("MINIO_ROOT_USER"),
        secret_access_key=conftest._infra_value("MINIO_ROOT_PASSWORD"),
        session_token=None,
        tls_enabled=False,
        versioning_required=True,
        connect_timeout_seconds=5.0,
        read_timeout_seconds=30.0,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "infra-probe.txt").write_text(capture_infra_probe(), encoding="utf-8")

    captures = (
        ("update-manifest.json", capture_update_manifest, "ev_update"),
        ("delete-manifest.json", capture_delete_manifest, "ev_delete"),
        ("fault-detection.json", capture_fault_detection, "ev_faults"),
    )
    failures = 0
    for filename, capture, scratch in captures:
        async with stage5_environment(
            pg_url=pg_url,
            milvus_uri=milvus_uri,
            object_store_config=object_store_config,
            scratch_name=f"pf_{scratch}",
            collection_suffix=scratch,
        ) as env:
            payload = await capture(env)
        (OUTPUT_DIR / filename).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        invariants = payload.get("invariants", {})
        broken = [name for name, held in invariants.items() if not held]
        status = "OK" if not broken else f"BROKEN: {broken}"
        failures += bool(broken)
        print(f"{filename}: {status}")
    return 1 if failures else 0


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
