"""${message}

Migration phase: ${branch_labels[0].split(":")[1]}
Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

Generate revisions with an explicit phase, for example:
    alembic -x phase=expand revision -m "add enterprise tables"
Run a constrained upgrade with:
    alembic -x phase=expand upgrade head
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from migrations.phases import validate_migration_phase
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}
migration_phase = validate_migration_phase(
    ${repr(branch_labels[0].split(":")[1])}, revision
)


def upgrade() -> None:
    """Upgrade schema within the declared migration phase."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """Downgrade schema when the staged recovery policy permits it."""
    ${downgrades if downgrades else "pass"}
