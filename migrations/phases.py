"""Executable phase rules shared by Alembic revisions and the environment."""

from __future__ import annotations

from typing import Final, Literal, cast

from alembic import context

MigrationPhase = Literal["expand", "backfill", "enforce", "contract"]
MIGRATION_PHASES: Final[tuple[MigrationPhase, ...]] = (
    "expand",
    "backfill",
    "enforce",
    "contract",
)
MIGRATION_PHASE_LABEL_PREFIX: Final = "phase:"


def requested_migration_phase(*, required: bool = False) -> MigrationPhase | None:
    """Read and validate ``-x phase=...`` without reflecting other arguments."""
    raw_phase = context.get_x_argument(as_dictionary=True).get("phase")
    if raw_phase is None or not raw_phase.strip():
        if required:
            choices = ", ".join(MIGRATION_PHASES)
            raise ValueError(
                "Revision generation requires -x phase=<phase>; "
                f"expected one of: {choices}"
            )
        return None
    phase = raw_phase.strip().casefold()
    if phase not in MIGRATION_PHASES:
        choices = ", ".join(MIGRATION_PHASES)
        raise ValueError(f"Invalid migration phase {raw_phase!r}; expected one of: {choices}")
    return phase


def migration_phase_label(phase: MigrationPhase, revision: str) -> str:
    """Return the branch label persisted on a staged revision.

    The revision id is part of the label because Alembic requires branch labels
    to be globally unique. A bare ``phase:expand`` label would allow only one
    revision per phase, which cannot express Stage 5+, where several additive
    revisions share the expand phase.
    """
    return f"{MIGRATION_PHASE_LABEL_PREFIX}{phase}:{revision}"


def parse_migration_phase_label(label: str) -> MigrationPhase | None:
    """Extract the migration phase from a staged branch label, if it is one."""
    if not label.startswith(MIGRATION_PHASE_LABEL_PREFIX):
        return None
    parts = label.split(":", 2)
    if len(parts) < 2:
        return None
    phase = parts[1].strip().casefold()
    return cast("MigrationPhase", phase) if phase in MIGRATION_PHASES else None


def validate_migration_phase(declared_phase: object, revision: str) -> MigrationPhase:
    """Fail when a revision's executable phase declaration is unsupported."""
    if not isinstance(declared_phase, str) or declared_phase not in MIGRATION_PHASES:
        choices = ", ".join(MIGRATION_PHASES)
        raise ValueError(
            f"Revision {revision} must declare migration_phase as one of: {choices}"
        )
    return declared_phase
