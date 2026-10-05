"""T097 [US1] the processor registry: signed, allowlisted images with fixed argv.

A sandbox job never runs an arbitrary image or an arbitrary command. It runs one
of a small, named set of *processors*, each pinned to:

* an image referenced by **digest** (``repo@sha256:...``), never a mutable tag --
  a tag can be repointed, a digest cannot, so "the thing we reviewed" is the thing
  that runs;
* a required **cosign signature identity**, so the image's provenance is checked
  before it is admitted;
* a **fixed argv**, with no shell, no interpolation and no caller-supplied
  arguments -- the command is a constant, so there is no injection surface.

What a processor definition can *never* carry, enforced at registration:

* a URL, host, or any network destination (the sandbox has deny-all egress);
* a credential, token or secret of any kind;
* a shell string or anything that would be passed to ``sh -c``;
* a mutable image tag.

The registry is the single source of truth the runner (T099) and the Kubernetes
Job (T098) both read, so the image and argv that are reviewed, signed, admitted
and executed are provably the same.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

#: An image reference pinned by digest. A tag (``:latest``, ``:v1``) is rejected:
#: only ``repo@sha256:<64 hex>`` is accepted so the running bytes are immutable.
_DIGEST_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/-]*@sha256:[a-f0-9]{64}$")

#: Tokens that must never appear in an argv element: a shell would interpret them,
#: so their presence means someone is trying to smuggle a command.
_SHELL_METACHARACTERS = frozenset("|&;<>$`\\\"'()*?!{}[]~\n\r")

#: Argv program names that are a shell (or a shell-invoking interpreter with
#: ``-c``). A processor runs a program directly, never a shell.
_SHELL_PROGRAMS = frozenset({"sh", "bash", "zsh", "ash", "dash", "/bin/sh", "/bin/bash"})

#: Substrings that betray a network destination or a credential in a value that is
#: supposed to be inert. Checked case-insensitively.
_FORBIDDEN_SUBSTRINGS = (
    "://",
    "http",
    "https",
    "ftp",
    "localhost",
    "127.0.0.1",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
)


class ProcessorError(ValueError):
    """A processor definition violated the registry's constraints."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class ProcessorDefinition:
    """One allowlisted sandbox processor.

    Immutable (``frozen``) so a registered definition cannot be mutated after its
    constraints were checked. ``signature_identity`` is the cosign identity the
    admission controller must verify before the image runs.
    """

    processor_id: str
    image: str
    argv: tuple[str, ...]
    signature_identity: str
    description: str = ""
    #: Non-secret, inert environment the processor needs (e.g. ``TZ=UTC``). Values
    #: are checked for URLs/credentials at registration like everything else.
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.processor_id or not re.match(r"^[a-z0-9][a-z0-9_-]*$", self.processor_id):
            raise ProcessorError(
                "PROCESSOR_ID_INVALID", f"processor id {self.processor_id!r} is not a slug"
            )
        if not _DIGEST_REFERENCE.match(self.image):
            raise ProcessorError(
                "IMAGE_NOT_PINNED",
                "the image must be pinned by digest (repo@sha256:...), not a tag",
            )
        if not self.signature_identity:
            raise ProcessorError(
                "SIGNATURE_REQUIRED", "a cosign signature identity is required"
            )
        if not self.argv:
            raise ProcessorError("ARGV_REQUIRED", "a processor must declare a fixed argv")
        _validate_argv(self.argv)
        for key, value in self.env.items():
            _reject_sensitive(f"env[{key}]", value)


def _validate_argv(argv: tuple[str, ...]) -> None:
    program = argv[0]
    if program.rsplit("/", 1)[-1] in _SHELL_PROGRAMS or program in _SHELL_PROGRAMS:
        raise ProcessorError(
            "SHELL_FORBIDDEN", "a processor runs a program directly, never a shell"
        )
    if "-c" in argv:
        raise ProcessorError(
            "SHELL_FORBIDDEN", "'-c' implies a shell/interpreter command string"
        )
    for element in argv:
        if any(char in _SHELL_METACHARACTERS for char in element):
            raise ProcessorError(
                "ARGV_METACHARACTER",
                f"argv element {element!r} contains a shell metacharacter",
            )
        _reject_sensitive("argv", element)


def _reject_sensitive(where: str, value: str) -> None:
    lowered = value.casefold()
    for needle in _FORBIDDEN_SUBSTRINGS:
        if needle in lowered:
            raise ProcessorError(
                "FORBIDDEN_VALUE",
                f"{where} contains a forbidden network/credential token ({needle!r})",
            )


class ProcessorRegistry:
    """An immutable allowlist of processors, addressed by ``processor_id``."""

    def __init__(self, definitions: tuple[ProcessorDefinition, ...]) -> None:
        catalogue: dict[str, ProcessorDefinition] = {}
        for definition in definitions:
            if definition.processor_id in catalogue:
                raise ProcessorError(
                    "PROCESSOR_DUPLICATE",
                    f"processor id {definition.processor_id!r} is registered twice",
                )
            catalogue[definition.processor_id] = definition
        self._catalogue: Mapping[str, ProcessorDefinition] = MappingProxyType(catalogue)

    def get(self, processor_id: str) -> ProcessorDefinition:
        """Return the definition, or raise -- a caller cannot run an unknown id."""
        definition = self._catalogue.get(processor_id)
        if definition is None:
            raise ProcessorError(
                "PROCESSOR_UNKNOWN",
                f"no allowlisted processor {processor_id!r}; arbitrary images are refused",
            )
        return definition

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._catalogue))

    def __contains__(self, processor_id: object) -> bool:
        return processor_id in self._catalogue


#: The built-in processors. Images are digest-pinned placeholders for the dev
#: stack -- a deployment overrides them with its own signed images. The point is
#: that *the shape* is fixed: digest-pinned, signed, fixed argv, no network.
DEFAULT_PROCESSORS: tuple[ProcessorDefinition, ...] = (
    ProcessorDefinition(
        processor_id="reimbursement-fill",
        image=(
            "registry.internal/policyflow/reimbursement-fill"
            "@sha256:" + "0" * 64
        ),
        argv=("/usr/local/bin/fill", "--manifest", "/work/in/manifest.json",
              "--out", "/work/out"),
        signature_identity="cosign:policyflow-ci@policyflow.internal",
        description="Fill a reimbursement form from a validated input manifest.",
        env={"TZ": "UTC"},
    ),
    ProcessorDefinition(
        processor_id="document-diff",
        image="registry.internal/policyflow/document-diff@sha256:" + "0" * 64,
        argv=("/usr/local/bin/diff", "--in", "/work/in", "--out", "/work/out"),
        signature_identity="cosign:policyflow-ci@policyflow.internal",
        description="Produce a normalized diff artifact between two versions.",
    ),
)


def default_registry() -> ProcessorRegistry:
    """Build the registry from the built-in definitions."""
    return ProcessorRegistry(DEFAULT_PROCESSORS)
