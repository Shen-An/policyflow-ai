"""Executable phase rules shared by Alembic revisions and the environment."""

from __future__ import annotations

from typing import Final, Literal

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


def migration_phase_label(phase: MigrationPhase) -> str:
    """Return the branch label persisted on a staged revision."""
    return f"{MIGRATION_PHASE_LABEL_PREFIX}{phase}"


def validate_migration_phase(declared_phase: object, revision: str) -> MigrationPhase:
    """Fail when a revision's executable phase declaration is unsupported."""
    if not isinstance(declared_phase, str) or declared_phase not in MIGRATION_PHASES:
        choices = ", ".join(MIGRATION_PHASES)
        raise ValueError(
            f"Revision {revision} must declare migration_phase as one of: {choices}"
        )
    return declared_phase
