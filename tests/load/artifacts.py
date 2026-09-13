"""不可省略的容量测试证据清单与 SHA-256 记录。"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    """One raw evidence file and its content digest."""

    path: str
    bytes: int
    sha256: str


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Hash a file without loading the whole raw artifact into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    commit = result.stdout.strip()
    return commit or None


def _dependency_versions() -> dict[str, str]:
    """Capture installed distributions without requiring pip or network access."""

    versions: dict[str, str] = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            versions[name] = distribution.version
    return dict(sorted(versions.items(), key=lambda item: item[0].casefold()))


def _raw_artifacts(root: Path, *, manifest_names: set[str]) -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    if not root.exists():
        return records
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name in manifest_names:
            continue
        records.append(
            ArtifactRecord(
                path=path.relative_to(root).as_posix(),
                bytes=path.stat().st_size,
                sha256=sha256_file(path),
            )
        )
    return records


def collect_artifact_manifest(
    project_root: str | Path,
    artifact_root: str | Path,
    *,
    topology: dict[str, Any] | None = None,
    data_volume: dict[str, Any] | None = None,
    extra_environment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete manifest while preserving every raw artifact reference."""

    project = Path(project_root).resolve()
    artifacts = Path(artifact_root).resolve()
    records = _raw_artifacts(
        artifacts,
        manifest_names={"manifest.json", "manifest.sha256"},
    )
    environment: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "git_commit": _git_commit(project),
        "dependencies": _dependency_versions(),
    }
    if extra_environment:
        environment.update(extra_environment)
    return {
        "schema_version": "1",
        "project_root": str(project),
        "artifact_root": str(artifacts),
        "environment": environment,
        "topology": topology or {},
        "data_volume": data_volume or {},
        "raw_artifacts": [asdict(record) for record in records],
        "raw_artifact_count": len(records),
        "raw_artifact_bytes": sum(record.bytes for record in records),
    }


def write_artifact_manifest(manifest: dict[str, Any], artifact_root: str | Path) -> Path:
    """Write the JSON manifest and a digest for the manifest itself."""

    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    output = root / "manifest.json"
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    (root / "manifest.sha256").write_text(f"{digest}  manifest.json\n", encoding="ascii")
    return output


# Short aliases keep load scripts readable while retaining explicit public names.
build_manifest = collect_artifact_manifest
write_manifest = write_artifact_manifest
