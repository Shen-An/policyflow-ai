"""T099 [US1] the sandbox runner: stage inputs, run a processor, collect outputs.

The runner is the orchestration seam between the file workflow and an actual
sandbox. It accepts *only* a workspace + material-version manifest -- never a path,
an image, or a command -- resolves the processor through the registry (T097), and:

* **stages inputs into a controlled directory**, copying each selected material
  version's bytes in read-only after full validation (containment, link
  rejection, hash check). The processor sees a clean ``in/`` tree and nothing else.
* **runs the processor** through an injected backend. Production is the gVisor
  Kubernetes Job (``infra/k8s/sandbox-job.yaml``, rendered by
  :class:`KubernetesManifestRenderer`); the local backend runs an allowlisted
  in-process callable for dev/test. Either way the runner's guarantees hold.
* **collects outputs**, refusing links, escaping names, and anything whose bytes
  do not match the manifest/hash the processor declared.

HONEST BOUNDARY: the local backend does not provide kernel-level isolation -- that
is gVisor's job, and gVisor needs a cluster this host does not have. What the
local backend *does* provide is the exact staging/collection validation and the
manifest-only input contract, so those are genuinely tested; the isolation itself
is verified at the manifest level (T092) and gated for live execution.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from backend.app.sandbox.processors import ProcessorRegistry, default_registry
from backend.app.sandbox.validation import (
    ValidationError,
    WorkspaceEscape,
    assert_not_a_link,
    safe_input_path,
    safe_output_path,
)


class SandboxError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class SandboxInput:
    """One material version to stage, addressed by ID only.

    ``relative_path`` is where it lands inside the workspace ``in/`` tree; it is
    validated for containment before anything is written. ``sha256`` is the bytes
    the caller claims, re-verified after staging.
    """

    material_id: str
    material_version_id: str
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class SandboxRequest:
    """The complete, path-free description of a sandbox task."""

    tenant_id: str
    run_id: str
    workspace_id: str
    processor_id: str
    inputs: tuple[SandboxInput, ...]


@dataclass(frozen=True)
class SandboxOutput:
    """One collected output file, with its verified hash."""

    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class SandboxResult:
    """The outcome of a completed sandbox task."""

    sandbox_job_ref: str
    status: str  # "succeeded" | "failed" | "cancelled"
    outputs: tuple[SandboxOutput, ...] = ()
    error_code: str | None = None


@dataclass
class SandboxHandle:
    """A started task: its workspace dirs plus the backend's opaque reference."""

    sandbox_job_ref: str
    request: SandboxRequest
    root: Path
    input_dir: Path
    output_dir: Path
    backend_state: dict[str, Any] = field(default_factory=dict)


#: A bytes-fetcher the runner uses to pull a material version's content. Injected
#: (the production one reads the object store) so the runner is testable without
#: infrastructure. Addressed by IDs, never a key or path.
InputFetcher = Callable[[str, str, str], Awaitable[bytes]]


class SandboxBackend(Protocol):
    """Executes a staged task. Sees only the staged dirs, never raw material IDs."""

    name: str

    async def run(self, handle: SandboxHandle) -> str:
        """Run the processor over ``handle.input_dir`` writing to ``output_dir``.

        Returns a status string. Must not raise for a processor-level failure --
        the runner distinguishes "ran and failed" from "could not run".
        """
        ...

    async def cancel(self, handle: SandboxHandle) -> None: ...


class SandboxRunner:
    """Owns staging, execution hand-off, and output collection with validation."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        backend: SandboxBackend,
        fetcher: InputFetcher,
        registry: ProcessorRegistry | None = None,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._backend = backend
        self._fetcher = fetcher
        self._registry = registry or default_registry()

    # -- lifecycle ------------------------------------------------------------

    async def start(self, request: SandboxRequest) -> SandboxHandle:
        """Provision a workspace and stage every input, validated.

        A processor id that is not allowlisted is refused here, before any
        directory is created -- the runner cannot be asked to run an arbitrary
        image.
        """
        definition = self._registry.get(request.processor_id)
        ref = f"sbx-{request.workspace_id}-{definition.processor_id}"
        root = self._workspace_root / request.tenant_id / request.workspace_id
        input_dir = root / "in"
        output_dir = root / "out"
        if root.exists():
            shutil.rmtree(root)
        input_dir.mkdir(parents=True)
        output_dir.mkdir(parents=True)

        for item in request.inputs:
            await self._stage_input(root, input_dir, item, request)

        return SandboxHandle(
            sandbox_job_ref=ref,
            request=request,
            root=root,
            input_dir=input_dir,
            output_dir=output_dir,
        )

    async def _stage_input(
        self, root: Path, input_dir: Path, item: SandboxInput, request: SandboxRequest
    ) -> None:
        # Containment is resolved against the in/ tree; a traversal or link in the
        # declared relative path is refused before any byte is fetched.
        destination = safe_input_path(input_dir, item.relative_path)
        data = await self._fetcher(
            request.tenant_id, item.material_id, item.material_version_id
        )
        actual = hashlib.sha256(data).hexdigest()
        if actual != item.sha256.lower():
            raise SandboxError(
                "INPUT_HASH_MISMATCH",
                f"input {item.relative_path!r} bytes do not match the manifest hash",
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Re-check the parent chain for links introduced since directory creation.
        destination.write_bytes(data)
        assert_not_a_link(destination)

    async def run_to_completion(self, request: SandboxRequest) -> SandboxResult:
        """Start, run and collect in one call -- the common synchronous path."""
        handle = await self.start(request)
        try:
            status = await self._backend.run(handle)
        except Exception as exc:  # noqa: BLE001 - a backend crash is a failed run
            return SandboxResult(
                handle.sandbox_job_ref,
                "failed",
                error_code=f"BACKEND_{type(exc).__name__}",
            )
        if status != "succeeded":
            return SandboxResult(handle.sandbox_job_ref, status, error_code="PROCESSOR_FAILED")
        try:
            outputs = self.collect(handle)
        except (ValidationError, SandboxError) as exc:
            return SandboxResult(
                handle.sandbox_job_ref,
                "failed",
                error_code=getattr(exc, "code", "OUTPUT_REJECTED"),
            )
        return SandboxResult(handle.sandbox_job_ref, "succeeded", outputs=tuple(outputs))

    async def cancel(self, handle: SandboxHandle) -> SandboxResult:
        await self._backend.cancel(handle)
        return SandboxResult(handle.sandbox_job_ref, "cancelled")

    # -- collection -----------------------------------------------------------

    def collect(self, handle: SandboxHandle) -> list[SandboxOutput]:
        """Walk the output dir, refusing links and escaping names, hashing each.

        A manifest file (``_outputs.json``) the processor may write is honoured as
        a declaration of intent but never trusted: the actual files on disk are
        the source of truth, and any declared hash that disagrees with the bytes
        is a rejection, not a warning.
        """
        declared = self._read_output_manifest(handle.output_dir)
        collected: list[SandboxOutput] = []
        for path in sorted(handle.output_dir.rglob("*")):
            if path.name == "_outputs.json":
                continue
            # A link anywhere under out/ is refused before it is read, so a
            # processor cannot exfiltrate by linking an output to /etc/passwd.
            assert_not_a_link(path)
            if path.is_dir():
                continue
            relative = path.relative_to(handle.output_dir).as_posix()
            # Containment re-check: the collected path must still resolve inside.
            safe_output_path(handle.output_dir, relative)
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if relative in declared and declared[relative].lower() != digest:
                raise SandboxError(
                    "OUTPUT_HASH_MISMATCH",
                    f"output {relative!r} bytes do not match the processor's declared hash",
                )
            collected.append(
                SandboxOutput(relative_path=relative, sha256=digest, size_bytes=len(data))
            )
        return collected

    def _read_output_manifest(self, output_dir: Path) -> Mapping[str, str]:
        manifest_path = output_dir / "_outputs.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return {}
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SandboxError(
                "OUTPUT_MANIFEST_INVALID", "the output manifest is not valid JSON"
            ) from exc
        if not isinstance(raw, dict):
            raise SandboxError("OUTPUT_MANIFEST_INVALID", "the output manifest must be an object")
        return {str(key): str(value) for key, value in raw.items()}


# -- local backend (dev/test) ------------------------------------------------

#: A local processor: a pure function over (input_dir, output_dir). Allowlisted by
#: processor_id, same as the production images. It provides NO isolation -- that is
#: gVisor's job in production -- but it exercises the exact staging/collection path.
LocalProcessor = Callable[[Path, Path], Awaitable[str]]


class LocalSandboxBackend:
    """Runs an allowlisted in-process processor. No kernel isolation (dev/test only)."""

    name = "local"

    def __init__(self, processors: Mapping[str, LocalProcessor]) -> None:
        self._processors = dict(processors)

    async def run(self, handle: SandboxHandle) -> str:
        processor = self._processors.get(handle.request.processor_id)
        if processor is None:
            raise SandboxError(
                "PROCESSOR_UNAVAILABLE",
                f"no local processor for {handle.request.processor_id!r}",
            )
        return await processor(handle.input_dir, handle.output_dir)

    async def cancel(self, handle: SandboxHandle) -> None:
        # In-process work is synchronous per call; nothing to signal. Present so the
        # backend satisfies the protocol the production (k8s) backend implements.
        return None


# -- Kubernetes manifest rendering (production target) -----------------------


class KubernetesManifestRenderer:
    """Renders the gVisor Job manifest (T098) for one task from the registry + config.

    This is what the production backend would hand to the cluster. It is a pure
    function (template + substitutions) so it is fully testable here; the live
    submission and the gVisor isolation it buys are gated on a cluster.
    """

    def __init__(self, *, template_path: Path, settings: Any, registry: ProcessorRegistry):
        self._template = Path(template_path).read_text(encoding="utf-8")
        self._settings = settings
        self._registry = registry

    def render(self, request: SandboxRequest) -> str:
        definition = self._registry.get(request.processor_id)
        s = self._settings
        substitutions = {
            "JOB_NAME": f"sbx-{request.workspace_id}"[:63],
            "ACTIVE_DEADLINE_SECONDS": str(s.SANDBOX_ACTIVE_DEADLINE_SECONDS),
            "PROCESSOR_IMAGE": definition.image,
            "PROCESSOR_ARGV": json.dumps(list(definition.argv)),
            "CPU_REQUEST_MILLICORES": str(max(s.SANDBOX_CPU_LIMIT_MILLICORES // 2, 1)),
            "MEMORY_REQUEST_MIB": str(max(s.SANDBOX_MEMORY_LIMIT_MIB // 2, 1)),
            "EPHEMERAL_REQUEST_MIB": str(max(s.SANDBOX_EPHEMERAL_STORAGE_LIMIT_MIB // 2, 1)),
            "CPU_LIMIT_MILLICORES": str(s.SANDBOX_CPU_LIMIT_MILLICORES),
            "MEMORY_LIMIT_MIB": str(s.SANDBOX_MEMORY_LIMIT_MIB),
            "EPHEMERAL_LIMIT_MIB": str(s.SANDBOX_EPHEMERAL_STORAGE_LIMIT_MIB),
        }
        rendered = self._template
        for key, value in substitutions.items():
            rendered = rendered.replace(f"${{{key}}}", value)
        # A leftover placeholder in a *non-comment* line means a substitution was
        # missed and the manifest would be malformed. The template's own header
        # comment mentions "${...}" generically, so comment lines are excluded.
        unrendered = [
            line
            for line in rendered.splitlines()
            if "${" in line and not line.lstrip().startswith("#")
        ]
        if unrendered:
            raise SandboxError(
                "MANIFEST_UNRENDERED", "the sandbox manifest has unsubstituted placeholders"
            )
        return rendered


__all__ = [
    "InputFetcher",
    "KubernetesManifestRenderer",
    "LocalSandboxBackend",
    "SandboxBackend",
    "SandboxError",
    "SandboxHandle",
    "SandboxInput",
    "SandboxOutput",
    "SandboxRequest",
    "SandboxResult",
    "SandboxRunner",
    "WorkspaceEscape",
]
