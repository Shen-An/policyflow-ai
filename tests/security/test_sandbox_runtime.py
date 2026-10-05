"""T092 [US1] sandbox runtime policy: the Job manifest expresses every control.

HONEST BOUNDARY, stated up front: this host has no Kubernetes cluster and no
gVisor runtime, so these tests do NOT launch a pod and observe kernel-level
isolation. What they *do* verify is that ``infra/k8s/sandbox-job.yaml`` -- the
real, deployable production manifest -- *expresses* every control the threat model
requires, and that the renderer (T099) substitutes the registry/config values
without weakening any of them. Observing the isolation live is gated on a cluster
and tracked as [~] in tasks.md; it is not faked here.

Each control below is one the Independent Test and ``data-model.md`` name:
non-root, read-only root fs, all capabilities dropped, seccomp, gVisor runtime
class, no service-account token, no hostPath/hostNetwork/hostPID, default-deny
egress, and CPU/memory/PID/time/ephemeral-storage limits.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from backend.app.core.config import Settings
from backend.app.sandbox.processors import default_registry
from backend.app.sandbox.runner import KubernetesManifestRenderer, SandboxInput, SandboxRequest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "infra" / "k8s" / "sandbox-job.yaml"


def _docs() -> list[dict]:
    return [doc for doc in yaml.safe_load_all(MANIFEST_PATH.read_text(encoding="utf-8")) if doc]


@pytest.fixture(scope="module")
def job() -> dict:
    for doc in _docs():
        if doc.get("kind") == "Job":
            return doc
    pytest.fail("no Job in the sandbox manifest")


@pytest.fixture(scope="module")
def network_policy() -> dict:
    for doc in _docs():
        if doc.get("kind") == "NetworkPolicy":
            return doc
    pytest.fail("no NetworkPolicy in the sandbox manifest")


def _pod_spec(job: dict) -> dict:
    return job["spec"]["template"]["spec"]


def _container(job: dict) -> dict:
    containers = _pod_spec(job)["containers"]
    assert len(containers) == 1, "a sandbox task runs exactly one processor container"
    return containers[0]


# -- isolation ---------------------------------------------------------------


def test_runs_under_gvisor(job: dict) -> None:
    assert _pod_spec(job)["runtimeClassName"] == "gvisor", (
        "the user-space kernel is the primary isolation boundary"
    )


def test_runs_non_root(job: dict) -> None:
    pod = _pod_spec(job)["securityContext"]
    container = _container(job)["securityContext"]
    assert pod["runAsNonRoot"] is True
    assert container["runAsNonRoot"] is True
    assert container["runAsUser"] != 0 and pod["runAsUser"] != 0


def test_root_filesystem_is_read_only(job: dict) -> None:
    assert _container(job)["securityContext"]["readOnlyRootFilesystem"] is True


def test_all_capabilities_dropped(job: dict) -> None:
    caps = _container(job)["securityContext"]["capabilities"]
    assert caps["drop"] == ["ALL"]
    assert "add" not in caps, "a sandbox task must add no capability"


def test_privilege_escalation_is_disabled(job: dict) -> None:
    sc = _container(job)["securityContext"]
    assert sc["allowPrivilegeEscalation"] is False
    assert sc["privileged"] is False


def test_seccomp_runtime_default(job: dict) -> None:
    assert _pod_spec(job)["securityContext"]["seccompProfile"]["type"] == "RuntimeDefault"


# -- no ambient authority ----------------------------------------------------


def test_no_service_account_token(job: dict) -> None:
    assert _pod_spec(job)["automountServiceAccountToken"] is False


def test_no_host_namespaces_or_hostpath(job: dict) -> None:
    pod = _pod_spec(job)
    assert pod.get("hostNetwork", False) is False
    assert pod.get("hostPID", False) is False
    assert pod.get("hostIPC", False) is False
    # No volume may be a hostPath: the task cannot reach the node filesystem.
    for volume in pod.get("volumes", []):
        assert "hostPath" not in volume, f"volume {volume.get('name')} mounts a host path"


def test_only_bounded_emptydirs_are_mounted(job: dict) -> None:
    for volume in _pod_spec(job).get("volumes", []):
        assert "emptyDir" in volume, "the only allowed volumes are size-bounded emptyDirs"
        assert volume["emptyDir"].get("sizeLimit"), "an emptyDir must be size-limited"
    # Inputs are mounted read-only.
    mounts = {m["mountPath"]: m for m in _container(job)["volumeMounts"]}
    assert mounts["/work/in"]["readOnly"] is True


def test_default_deny_egress(network_policy: dict) -> None:
    spec = network_policy["spec"]
    assert set(spec["policyTypes"]) == {"Ingress", "Egress"}
    # Empty rule lists mean deny-all in both directions.
    assert spec["ingress"] == []
    assert spec["egress"] == []


# -- resource limits ---------------------------------------------------------


def test_every_resource_axis_is_bounded(job: dict) -> None:
    limits = _container(job)["resources"]["limits"]
    for axis in ("cpu", "memory", "ephemeral-storage"):
        assert axis in limits, f"the {axis} limit is required"
    assert job["spec"]["activeDeadlineSeconds"], "a wall-clock deadline is required"
    assert job["spec"]["backoffLimit"] == 0, "retries belong to the durable-job layer"
    assert job["spec"]["ttlSecondsAfterFinished"], "the Job must be swept after it finishes"


def test_shell_is_not_the_entrypoint(job: dict) -> None:
    # args is a placeholder here; it must be substituted with a fixed argv, and the
    # container must not declare a shell command. (The registry forbids a shell
    # program; this guards the manifest side.)
    container = _container(job)
    assert "command" not in container or "sh" not in str(container.get("command", "")), (
        "the manifest must not wrap the processor in a shell"
    )


# -- the renderer produces a consistent, fully-substituted manifest ----------


def test_renderer_substitutes_registry_image_and_argv_without_weakening() -> None:
    settings = Settings(
        DATABASE_URL="sqlite://",
        LOG_DIR=ROOT / "logs",
        SECRET_KEY="x" * 32,
        BOOTSTRAP_ADMIN_PASSWORD="pw",
        _env_file=None,
    )
    registry = default_registry()
    renderer = KubernetesManifestRenderer(
        template_path=MANIFEST_PATH, settings=settings, registry=registry
    )
    request = SandboxRequest(
        tenant_id="t",
        run_id="run-1",
        workspace_id="ws-1",
        processor_id="reimbursement-fill",
        inputs=(
            SandboxInput(
                material_id="m",
                material_version_id="v",
                relative_path="in/form.json",
                sha256="a" * 64,
            ),
        ),
    )
    rendered = renderer.render(request)
    assert "${" not in rendered, "every placeholder must be substituted"
    docs = [doc for doc in yaml.safe_load_all(rendered) if doc]
    job = next(doc for doc in docs if doc["kind"] == "Job")
    container = job["spec"]["template"]["spec"]["containers"][0]
    definition = registry.get("reimbursement-fill")
    # The image that runs is exactly the registry's digest-pinned, signed image.
    assert container["image"] == definition.image
    assert "@sha256:" in container["image"], "the rendered image must stay digest-pinned"
    assert container["args"] == list(definition.argv)
    # Rendering must not have weakened the hardening.
    assert job["spec"]["template"]["spec"]["runtimeClassName"] == "gvisor"
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]


def test_renderer_refuses_an_unknown_processor() -> None:
    from backend.app.sandbox.processors import ProcessorError

    settings = Settings(
        DATABASE_URL="sqlite://",
        LOG_DIR=ROOT / "logs",
        SECRET_KEY="x" * 32,
        BOOTSTRAP_ADMIN_PASSWORD="pw",
        _env_file=None,
    )
    renderer = KubernetesManifestRenderer(
        template_path=MANIFEST_PATH, settings=settings, registry=default_registry()
    )
    request = SandboxRequest(
        tenant_id="t",
        run_id="r",
        workspace_id="w",
        processor_id="arbitrary-image",
        inputs=(),
    )
    with pytest.raises(ProcessorError, match="PROCESSOR_UNKNOWN"):
        renderer.render(request)
