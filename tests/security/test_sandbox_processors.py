"""T097 [US1] processor registry + T099 sandbox runner behaviour (no isolation).

The registry tests are pure. The runner tests use the local backend, which gives
NO kernel isolation (that is gVisor's job, gated on a cluster) but exercises the
real staging and collection validation: manifest-only inputs, hash verification,
and output link/escape rejection. That validation is the part this host can prove,
and it is proven here against the real runner.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from backend.app.sandbox.processors import (
    DEFAULT_PROCESSORS,
    ProcessorDefinition,
    ProcessorError,
    ProcessorRegistry,
    default_registry,
)
from backend.app.sandbox.runner import (
    LocalSandboxBackend,
    SandboxError,
    SandboxInput,
    SandboxRequest,
    SandboxRunner,
)

# -- registry (T097) ---------------------------------------------------------


def test_default_processors_are_digest_pinned_and_signed() -> None:
    registry = default_registry()
    assert registry.ids(), "the registry must not be empty"
    for processor_id in registry.ids():
        definition = registry.get(processor_id)
        assert "@sha256:" in definition.image, "every image must be digest-pinned"
        assert definition.signature_identity, "every image must require a signature"
        assert definition.argv, "every processor must have a fixed argv"


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"image": "registry.internal/x:latest"}, "IMAGE_NOT_PINNED"),
        ({"image": "registry.internal/x:v1.0"}, "IMAGE_NOT_PINNED"),
        ({"signature_identity": ""}, "SIGNATURE_REQUIRED"),
        ({"argv": ("sh", "-c", "echo hi")}, "SHELL_FORBIDDEN"),
        ({"argv": ("/bin/bash", "script.sh")}, "SHELL_FORBIDDEN"),
        ({"argv": ("/usr/bin/tool", "--run", "rm -rf / ; echo $(whoami)")}, "ARGV_METACHARACTER"),
        ({"argv": ("/usr/bin/tool", "--url", "https://evil.example")}, "FORBIDDEN_VALUE"),
        ({"argv": ("/usr/bin/tool", "--token", "bearer abc")}, "FORBIDDEN_VALUE"),
        ({"env": {"CREDS": "password=hunter2"}}, "FORBIDDEN_VALUE"),
        ({"env": {"ENDPOINT": "http://169.254.169.254"}}, "FORBIDDEN_VALUE"),
    ],
)
def test_a_dangerous_processor_definition_is_refused(overrides: dict, code: str) -> None:
    base = {
        "processor_id": "p",
        "image": "registry.internal/x@sha256:" + "0" * 64,
        "argv": ("/usr/bin/tool", "--in", "/work/in"),
        "signature_identity": "cosign:ci@internal",
    }
    base.update(overrides)
    with pytest.raises(ProcessorError, match=code):
        ProcessorDefinition(**base)


def test_an_unknown_processor_id_is_refused() -> None:
    registry = default_registry()
    with pytest.raises(ProcessorError, match="PROCESSOR_UNKNOWN"):
        registry.get("arbitrary-image-the-caller-chose")


def test_duplicate_processor_ids_are_refused() -> None:
    with pytest.raises(ProcessorError, match="PROCESSOR_DUPLICATE"):
        ProcessorRegistry((*DEFAULT_PROCESSORS, DEFAULT_PROCESSORS[0]))


# -- runner staging + collection (T099) --------------------------------------


def _registry_with_local() -> ProcessorRegistry:
    return ProcessorRegistry(
        (
            ProcessorDefinition(
                processor_id="echo",
                image="registry.internal/echo@sha256:" + "0" * 64,
                argv=("/usr/local/bin/echo", "--in", "/work/in", "--out", "/work/out"),
                signature_identity="cosign:ci@internal",
            ),
        )
    )


async def _fetcher_for(contents: dict[tuple[str, str], bytes]):
    async def fetch(tenant_id: str, material_id: str, material_version_id: str) -> bytes:
        return contents[(material_id, material_version_id)]

    return fetch


def _request(processor_id="echo", inputs=()) -> SandboxRequest:
    return SandboxRequest(
        tenant_id="t",
        run_id="run-1",
        workspace_id="ws-1",
        processor_id=processor_id,
        inputs=tuple(inputs),
    )


async def test_runner_refuses_an_unknown_processor(tmp_path) -> None:
    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({}),
        fetcher=await _fetcher_for({}),
        registry=_registry_with_local(),
    )
    with pytest.raises(ProcessorError, match="PROCESSOR_UNKNOWN"):
        await runner.start(_request(processor_id="arbitrary"))


async def test_runner_stages_inputs_and_collects_outputs(tmp_path) -> None:
    body = b"reimbursement form body"
    contents = {("m1", "v1"): body}

    async def processor(input_dir: Path, output_dir: Path) -> str:
        # A well-behaved processor reads in/, writes out/.
        text = (input_dir / "form.txt").read_bytes()
        (output_dir / "filled.txt").write_bytes(text + b"\nAPPROVED")
        return "succeeded"

    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({"echo": processor}),
        fetcher=await _fetcher_for(contents),
        registry=_registry_with_local(),
    )
    result = await runner.run_to_completion(
        _request(
            inputs=[
                SandboxInput(
                    material_id="m1",
                    material_version_id="v1",
                    relative_path="form.txt",
                    sha256=hashlib.sha256(body).hexdigest(),
                )
            ]
        )
    )
    assert result.status == "succeeded"
    assert len(result.outputs) == 1
    assert result.outputs[0].relative_path == "filled.txt"
    assert result.outputs[0].sha256 == hashlib.sha256(body + b"\nAPPROVED").hexdigest()


async def test_runner_rejects_an_input_hash_mismatch(tmp_path) -> None:
    contents = {("m1", "v1"): b"actual bytes"}
    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({"echo": _noop_processor}),
        fetcher=await _fetcher_for(contents),
        registry=_registry_with_local(),
    )
    with pytest.raises(SandboxError, match="INPUT_HASH_MISMATCH"):
        await runner.start(
            _request(
                inputs=[
                    SandboxInput(
                        material_id="m1",
                        material_version_id="v1",
                        relative_path="form.txt",
                        sha256="f" * 64,  # wrong
                    )
                ]
            )
        )


async def test_runner_rejects_a_traversal_input_path(tmp_path) -> None:
    body = b"x"
    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({"echo": _noop_processor}),
        fetcher=await _fetcher_for({("m1", "v1"): body}),
        registry=_registry_with_local(),
    )
    with pytest.raises(Exception, match="TRAVERSAL|ESCAPE"):
        await runner.start(
            _request(
                inputs=[
                    SandboxInput(
                        material_id="m1",
                        material_version_id="v1",
                        relative_path="../escape.txt",
                        sha256=hashlib.sha256(body).hexdigest(),
                    )
                ]
            )
        )


async def test_runner_rejects_a_symlinked_output(tmp_path) -> None:
    """A processor that links an output to outside the workspace is caught."""
    secret = tmp_path / "secret.txt"
    secret.write_text("outside", encoding="utf-8")

    async def malicious(input_dir: Path, output_dir: Path) -> str:
        link = output_dir / "exfil.txt"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("creating a symlink requires privilege on this host")
        return "succeeded"

    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({"echo": malicious}),
        fetcher=await _fetcher_for({}),
        registry=_registry_with_local(),
    )
    result = await runner.run_to_completion(_request())
    assert result.status == "failed"
    assert result.error_code == "LINK_REJECTED"


async def test_runner_rejects_output_with_mismatched_declared_hash(tmp_path) -> None:
    async def lying(input_dir: Path, output_dir: Path) -> str:
        (output_dir / "a.txt").write_bytes(b"real bytes")
        (output_dir / "_outputs.json").write_text(
            '{"a.txt": "deadbeef"}', encoding="utf-8"
        )
        return "succeeded"

    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({"echo": lying}),
        fetcher=await _fetcher_for({}),
        registry=_registry_with_local(),
    )
    result = await runner.run_to_completion(_request())
    assert result.status == "failed"
    assert result.error_code == "OUTPUT_HASH_MISMATCH"


async def test_a_processor_level_failure_is_a_failed_run_not_a_crash(tmp_path) -> None:
    async def failing(input_dir: Path, output_dir: Path) -> str:
        return "failed"

    runner = SandboxRunner(
        workspace_root=tmp_path,
        backend=LocalSandboxBackend({"echo": failing}),
        fetcher=await _fetcher_for({}),
        registry=_registry_with_local(),
    )
    result = await runner.run_to_completion(_request())
    assert result.status == "failed" and result.error_code == "PROCESSOR_FAILED"


async def _noop_processor(input_dir: Path, output_dir: Path) -> str:
    return "succeeded"
