"""Sandbox package: everything that keeps untrusted file work from escaping.

The file workflow copies selected material versions into a controlled workspace,
runs a signed, allowlisted processor over them in a locked-down sandbox, and
collects outputs. Three honest boundaries hold throughout:

* path containment, link/device rejection and archive-bomb defence are pure logic
  the host can verify (``validation.py``);
* the processor registry only ever names signed, allowlisted images with fixed
  argv (``processors.py``);
* the production sandbox is a per-task gVisor Job on Kubernetes (``runner.py`` +
  ``infra/k8s/sandbox-job.yaml``); where that cannot run (no cluster/gVisor), the
  runner uses a local executor that still applies every validation, and the parts
  that need a real gVisor pod are reported honestly rather than faked.
"""
