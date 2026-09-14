"""Contract tests for the staged asynchronous Alembic scaffold."""

from __future__ import annotations

import ast
import io
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData

from backend.app.core.config import get_settings
from migrations.phases import MIGRATION_PHASES

ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
ENV_PATH = ROOT / "migrations" / "env.py"
MAIN_PATH = ROOT / "backend" / "app" / "main.py"


def _config(*, output: io.StringIO | None = None) -> Config:
    config = Config(str(ALEMBIC_INI), output_buffer=output)
    config.set_main_option("script_location", str(ROOT / "migrations"))
    return config


def _assigned_name(node: ast.Assign | ast.AnnAssign) -> str | None:
    target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _load_env_for_helpers() -> ModuleType:
    source = ENV_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "MIGRATION_PHASES",
        "MIGRATION_PHASE_LABEL_PREFIX",
        "MigrationPhase",
        "_revision_phase",
    }
    body: list[ast.stmt] = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or isinstance(node, (ast.Assign, ast.AnnAssign))
        and _assigned_name(node) in names
        or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_revision_phase"
    ]
    module = ModuleType("alembic_env_helpers")
    exec(compile(ast.Module(body=body, type_ignores=[]), ENV_PATH, "exec"), module.__dict__)
    return module


def test_alembic_config_and_revision_scaffold_load() -> None:
    config = _config()
    assert Path(config.get_main_option("script_location")) == ROOT / "migrations"
    assert config.get_main_option("sqlalchemy.url") == "driver://unused"
    assert config.get_main_option("revision_environment") == "true"
    script = ScriptDirectory.from_config(config)
    # Stage 2 replaced the empty Phase 1 scaffold with real staged revisions.
    # Every revision must still declare exactly one recognised phase label, and
    # labels must embed the revision id so a single phase can hold several
    # revisions (Stage 5+ needs this).
    revisions = list(script.walk_revisions())
    assert revisions, "the staged scaffold must contain the Stage 2 revisions"
    assert script.get_heads(), "the revision graph must expose at least one head"
    assert "001" in {revision.revision for revision in revisions}
    for revision in revisions:
        # The revision's OWN declaration is asserted, not Script.branch_labels:
        # Alembic folds a branch ancestor's label into the descendant's view, so
        # 002 legitimately reports both its own label and 001's.
        declared = tuple(revision.module.branch_labels or ())
        expected = f"phase:{revision.module.migration_phase}:{revision.revision}"
        assert declared == (expected,), f"{revision.revision} declares {declared}"
        assert revision.module.migration_phase in MIGRATION_PHASES
        # ``down_revision`` must chain linearly so the staged order is the only
        # order available.
        if revision.down_revision is not None:
            assert isinstance(revision.down_revision, str)
    # The chain is expand -> enforce, and only 002 is a head.
    assert script.get_heads() == ["002"]
    parents = {revision.revision: revision.down_revision for revision in revisions}
    assert parents["002"] == "001"
    assert parents["001"] is None


def test_target_metadata_imports_real_models() -> None:
    from backend.app.db import base

    assert isinstance(base.Base.metadata, MetaData)
    assert "users" in base.Base.metadata.tables
    assert "knowledge_bases" in base.Base.metadata.tables
    assert len(base.Base.metadata.tables) >= 20


def test_offline_upgrade_uses_configured_dialect_without_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@invalid/policyflow")
    # Settings is process-wide cached; without this the upgrade below could
    # silently render for whichever dialect an earlier test happened to cache.
    get_settings.cache_clear()
    try:
        command.upgrade(_config(output=output), "head", sql=True)
    finally:
        get_settings.cache_clear()
    emitted = output.getvalue()
    # Offline SQL generation must reach the real revisions and must never echo
    # the credentials embedded in the configured URL.
    assert emitted, "offline upgrade must emit the staged DDL"
    assert "CREATE TABLE" in emitted.upper()
    assert "secret" not in emitted
    # PostgreSQL-only steps must render offline for PostgreSQL.
    assert "ROW LEVEL SECURITY" in emitted.upper()
    assert "alembic_version" in emitted

    from alembic.runtime.migration import MigrationContext

    dialect = MigrationContext.configure(
        url="postgresql+psycopg://user:secret@invalid/policyflow",
        opts={"as_sql": True},
    ).dialect
    assert dialect.name == "postgresql"
    assert "secret" not in output.getvalue()


def test_revision_generation_requires_and_persists_phase() -> None:
    versions = ROOT / "migrations" / "versions"
    expected_revision = versions / "phase_smoke_phase_smoke.py"
    config = _config()
    config.cmd_opts = type("CommandOptions", (), {"x": None})()

    with pytest.raises(ValueError, match="requires -x phase"):
        command.revision(config, message="phase smoke", rev_id="phase_smoke", sql=True)

    config.cmd_opts = type("CommandOptions", (), {"x": ["phase=expand"]})()
    generated = command.revision(
        config,
        message="phase smoke",
        rev_id="phase_smoke",
        sql=True,
    )
    assert generated is not None
    source = expected_revision.read_text(encoding="utf-8")
    assert "branch_labels: str | Sequence[str] | None = ('phase:expand:phase_smoke',)" in source
    assert 'validate_migration_phase(\n    \'expand\', revision\n)' in source
    expected_revision.unlink()


def test_a_phase_may_hold_several_revisions() -> None:
    """Two generated revisions in one phase must not collide on a branch label.

    This is the defect that made the original scaffold unusable past Stage 2: a
    bare ``phase:<name>`` label is rejected by Alembic as a duplicate branch.
    """
    versions = ROOT / "migrations" / "versions"
    config = _config()
    config.cmd_opts = type("CommandOptions", (), {"x": ["phase=expand"]})()
    created = [versions / f"phase_dup{i}_phase_dup{i}.py" for i in (1, 2)]
    try:
        for index in (1, 2):
            assert (
                command.revision(
                    config,
                    message=f"phase dup{index}",
                    rev_id=f"phase_dup{index}",
                    sql=True,
                )
                is not None
            )
        # Loading the whole graph is what raises on a duplicate branch label.
        revisions = {revision.revision for revision in ScriptDirectory.from_config(config).walk_revisions()}
        assert {"phase_dup1", "phase_dup2"} <= revisions
    finally:
        for path in created:
            path.unlink(missing_ok=True)


def test_revision_phase_requires_exactly_one_supported_label() -> None:
    helpers = _load_env_for_helpers()

    class Revision:
        _orig_branch_labels = ("phase:expand",)

    class Step:
        up_revision_id = "example"
        up_revision = Revision()

    assert helpers._revision_phase(cast(Any, Step())) == "expand"
    Revision._orig_branch_labels = ()
    with pytest.raises(ValueError, match="exactly one migration phase"):
        helpers._revision_phase(cast(Any, Step()))
    Revision._orig_branch_labels = ("phase:invented",)
    with pytest.raises(ValueError, match="exactly one migration phase"):
        helpers._revision_phase(cast(Any, Step()))


def test_phase_generation_contract_is_wired() -> None:
    source = ENV_PATH.read_text(encoding="utf-8")
    assert "required=True" in source
    assert "directives[0].branch_label = migration_phase_label(phase, str(revision_id))" in source
    # The phase label must embed the revision id, otherwise Alembic rejects the
    # second revision of any phase as a duplicate branch.
    assert 'getattr(directives[0], "rev_id", None)' in source


def test_api_main_has_no_alembic_import_or_command() -> None:
    tree = ast.parse(MAIN_PATH.read_text(encoding="utf-8"))
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "alembic" not in imported_roots
    assert not {"run_migrations_online", "run_async_migrations"} & called_names
