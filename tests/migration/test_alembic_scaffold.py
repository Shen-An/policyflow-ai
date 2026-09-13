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
    assert script.get_heads() == []


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
    command.upgrade(_config(output=output), "head", sql=True)
    assert output.getvalue() == ""

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
    assert "branch_labels: str | Sequence[str] | None = ('phase:expand',)" in source
    assert 'validate_migration_phase(\n    \'expand\', revision\n)' in source
    expected_revision.unlink()


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
    assert "directives[0].branch_label = migration_phase_label(phase)" in source


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
