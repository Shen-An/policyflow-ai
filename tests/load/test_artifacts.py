"""Contract tests for capacity evidence manifests."""

from __future__ import annotations

import json

from tests.load.artifacts import collect_artifact_manifest, write_artifact_manifest


def test_manifest_records_raw_artifacts_and_digest(tmp_path) -> None:
    raw = tmp_path / "run.csv"
    raw.write_text("request_id,latency_ms\nrun-1,12\n", encoding="utf-8")
    manifest = collect_artifact_manifest(
        tmp_path,
        tmp_path,
        topology={"profile": "smoke", "workers": 1},
        data_volume={"requests": 1},
    )
    assert manifest["raw_artifact_count"] == 1
    assert manifest["raw_artifacts"][0]["path"] == "run.csv"
    output = write_artifact_manifest(manifest, tmp_path)
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["raw_artifacts"][0]["sha256"]
    assert (tmp_path / "manifest.sha256").is_file()
