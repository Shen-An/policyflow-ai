"""The seeded roles must carry the capabilities they advertise.

Principal scopes are built from ``role.actions`` (``backend/app/api/deps.py``),
so a role row with an empty action list grants nothing: every ``Action.ADMIN``
gate in ``backend/app/services/admin_service.py`` refuses even the bootstrap
``sys_admin`` user, which makes the whole ``/api/v2/admin/*`` surface return 403
on a freshly seeded install. These tests pin the seed contract.
"""

from pathlib import Path

from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.init_db import initialize_database
from backend.app.db.models import Role
from backend.app.db.session import build_engine


def build_test_settings(tmp_path: Path) -> Settings:
    return Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'policyflow-roles.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )


def _roles(engine) -> dict[str, list[str]]:
    with Session(engine) as session:
        return {role.code: list(role.actions or []) for role in session.exec(select(Role))}


def test_seeded_roles_declare_the_actions_they_advertise(tmp_path: Path) -> None:
    settings = build_test_settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)

    summary = initialize_database(engine, settings)

    assert summary.roles_created == 3
    assert summary.roles_repaired == 0

    roles = _roles(engine)
    assert roles["employee"] == ["read"]
    assert roles["kb_admin"] == ["read", "edit", "publish"]
    # sys_admin is the only role allowed through every Action.ADMIN gate.
    assert "admin" in roles["sys_admin"]
    # Action.CROSS_TENANT_ADMIN is deliberately *not* seeded: cross-tenant reads
    # stay an explicit, separately granted capability, so /api/v2/admin/capacity
    # must keep answering 403 for a seeded sys_admin (README honors that default).
    assert "cross_tenant_admin" not in roles["sys_admin"]


def test_seed_backfills_roles_that_have_no_actions(tmp_path: Path) -> None:
    """A database seeded before this fix keeps its role rows; reseeding repairs them."""

    settings = build_test_settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)

    with Session(engine) as session:
        for role in session.exec(select(Role)):
            role.actions = []
            session.add(role)
        session.commit()

    assert _roles(engine) == {"employee": [], "kb_admin": [], "sys_admin": []}

    summary = initialize_database(engine, settings)

    assert summary.roles_created == 0
    assert summary.roles_repaired == 3
    assert _roles(engine)["sys_admin"].count("admin") == 1
    assert _roles(engine)["employee"] == ["read"]


def test_seed_leaves_a_deliberately_narrowed_role_alone(tmp_path: Path) -> None:
    """Only the empty action list is treated as the broken state."""

    settings = build_test_settings(tmp_path)
    engine = build_engine(settings.DATABASE_URL)
    initialize_database(engine, settings)

    with Session(engine) as session:
        role = session.exec(select(Role).where(Role.code == "kb_admin")).one()
        role.actions = ["read"]
        session.add(role)
        session.commit()

    summary = initialize_database(engine, settings)

    assert summary.roles_repaired == 0
    assert _roles(engine)["kb_admin"] == ["read"]
