"""Create database tables and idempotently insert roadmap seed data.

Schema authority rules:

- **Production**: Alembic owns the schema. This module must NOT create tables or
  run migrations at startup; it only verifies that the expected revision is
  already applied and fails fast otherwise. A service that silently creates its
  own schema cannot be rolled forward or rolled back predictably.
- **Development/test**: the SQLite convenience bootstrap (``create_all`` plus the
  small column backfills below) is retained so a developer can start the app
  without running migrations. It is explicitly refused in production.
"""

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass

from sqlalchemy import JSON, Column, func, inspect, text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, col, select

from backend.app.core.config import Environment, Settings, get_settings
from backend.app.core.logging import get_logger
from backend.app.core.mcp_security import protect_command, protect_config, reveal_config
from backend.app.core.redaction import redact_sensitive
from backend.app.core.security import hash_password
from backend.app.db import base  # noqa: F401
from backend.app.db.models import (
    DEFAULT_TENANT_CODE,
    DEFAULT_TENANT_NAME,
    LEGACY_TENANT_CODE,
    LEGACY_TENANT_ID,
    LEGACY_TENANT_NAME,
    Department,
    KnowledgeBase,
    KnowledgeBasePermission,
    MCPServer,
    ModelProvider,
    Role,
    Skill,
    Tenant,
    Tool,
    ToolCallLog,
    User,
    UserRole,
    UserRoleGrant,
    utc_now,
)
from backend.app.db.session import (
    ALEMBIC_VERSION_TABLE,
    expected_schema_revision,
    get_engine,
)

logger = get_logger(__name__)


class SchemaAuthorityError(RuntimeError):
    """Raised when the database schema is not the expected migrated revision.

    Startup must fail loudly rather than repairing schema implicitly: an
    implicitly-created schema has no migration history and therefore no defined
    rollback path.
    """

SQLITE_COLUMN_MIGRATIONS = {
    # Columns added after the original SQLite schema. A development database file
    # is upgraded in place, so every column the ORM has gained since that file was
    # created must appear here; otherwise an existing development database cannot
    # start at all. `tenant_id` stays nullable here because ownership is only
    # made mandatory by the enforce migration, which SQLite never runs.
    "agent_runs": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "ai_query_logs": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "audit_events": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "conversations": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "drafts": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "eval_cases": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "eval_results": {
        "tenant_id": "tenant_id VARCHAR(36)",
        "answer_metrics": "answer_metrics JSON",
        "type_statuses": "type_statuses JSON NOT NULL DEFAULT '{}'",
    },
    "eval_runs": {
        "tenant_id": "tenant_id VARCHAR(36)",
        "error_summary": "error_summary TEXT",
        "request_id": "request_id VARCHAR(128)",
    },
    "graph_checkpoint_bindings": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "idempotency_records": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "knowledge_bases": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "knowledge_documents": {
        "tenant_id": "tenant_id VARCHAR(36)",
        "external_id": "external_id VARCHAR(128)",
    },
    "memory_items": {
        "tenant_id": "tenant_id VARCHAR(36)",
        "embedding": "embedding JSON",
        "meta_json": "meta_json JSON NOT NULL DEFAULT '{}'",
    },
    "messages": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "retrieval_eval_items": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "roles": {
        "tenant_id": "tenant_id VARCHAR(36)",
        "version": "version INTEGER NOT NULL DEFAULT 1",
    },
    "run_events": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "user_role_grants": {
        "tenant_id": "tenant_id VARCHAR(36)",
    },
    "users": {
        "tenant_id": "tenant_id VARCHAR(36)",
        "version": "version INTEGER NOT NULL DEFAULT 1",
    },
    "model_providers": {
        "api_key_ciphertext": "api_key_ciphertext TEXT",
        "capability": "capability VARCHAR(20) NOT NULL DEFAULT 'chat'",
    },
    "audit_logs": {
        "request_id": "request_id VARCHAR(128)",
    },
    "tool_call_logs": {
        "request_id": "request_id VARCHAR(128)",
    },
    "mcp_servers": {
        "server_type": "server_type VARCHAR(20) NOT NULL DEFAULT 'mock'",
        "integration_mode": "integration_mode VARCHAR(20) NOT NULL DEFAULT 'mock'",
        "endpoint": "endpoint VARCHAR(500)",
        "tools": "tools JSON NOT NULL DEFAULT '[]'",
        "last_error_code": "last_error_code VARCHAR(100)",
        "last_error_message": "last_error_message TEXT",
    },
}

ROLE_SEEDS = (
    ("employee", "普通员工", "可查询已授权的企业制度知识库"),
    ("kb_admin", "知识库管理员", "可维护被授权的知识库与文档"),
    ("sys_admin", "系统管理员", "拥有系统级管理权限"),
)

DEPARTMENT_SEEDS = (
    ("hr", "HR"),
    ("finance", "Finance"),
    ("it", "IT"),
    ("admin", "Admin"),
    ("legal", "Legal"),
)

KNOWLEDGE_BASE_SEEDS = (
    ("hr", "人力资源制度库", "HR 制度与流程"),
    ("finance", "财务制度库", "财务制度与报销流程"),
    ("it", "IT 制度库", "信息技术与安全制度"),
    ("admin", "行政制度库", "行政管理制度与流程"),
    ("legal", "法务制度库", "法务与合规制度"),
    # Isolated sandbox for CRUD/Hit@K evaluation imports — do not mix with business KBs.
    ("eval_test", "测试库", "评估/回归专用沙箱知识库，仅放 CRUD 评测语料"),
    (
        "enterprise_eval_test",
        "企业政策测试库",
        "企业内部政策检索评测专用库，包含人工设计的制度文档和边界问题",
    ),
)


SKILL_SEEDS = (
    ("knowledge_qa", "企业制度问答", "low"),
    ("process_checklist", "根据制度生成流程清单", "low"),
    ("application_draft", "生成申请材料草稿", "medium"),
    ("policy_compare", "对比多份制度内容", "medium"),
    ("faq_generate", "生成 FAQ 草稿", "medium"),
    ("risk_check", "检查制度相关风险", "high"),
    ("summary", "总结制度或对话内容", "low"),
)

TOOL_SEEDS = (
    "knowledge.search",
    "knowledge.insert",
    "knowledge.reindex",
    "draft.create",
    "draft.update",
    "faq.create_draft",
    "faq.approve",
    "memory.read",
    "memory.write",
    "mcp.call",
)


@dataclass(frozen=True)
class SeedSummary:
    roles_created: int = 0
    departments_created: int = 0
    knowledge_bases_created: int = 0
    permissions_created: int = 0
    users_created: int = 0
    model_providers_created: int = 0
    skills_created: int = 0
    tools_created: int = 0
    tenants_created: int = 0


def create_db_and_tables(engine: Engine | None = None) -> Engine:
    """Development/test-only schema bootstrap.

    Production schema is owned by Alembic. Allowing the application to create
    tables in production would produce a schema with no migration history and
    therefore no defined rollback path, so the call is refused there.
    """
    if get_settings().ENVIRONMENT is Environment.PRODUCTION:
        raise SchemaAuthorityError(
            "create_all is not permitted in production; apply `alembic upgrade head` "
            "before starting the service"
        )
    database_engine = engine or get_engine()
    SQLModel.metadata.create_all(database_engine)
    return database_engine


def verify_schema_version(engine: Engine | None = None) -> str:
    """Return the applied Alembic revision, raising when schema is not usable.

    This is the production readiness check for schema: it reads
    ``alembic_version`` and compares it with the repository head. It never
    creates or alters anything.
    """
    database_engine = engine or get_engine()
    expected = expected_schema_revision()
    with database_engine.connect() as connection:
        if not inspect(connection).has_table(ALEMBIC_VERSION_TABLE):
            raise SchemaAuthorityError(
                "schema is not migrated: alembic_version is absent; "
                "run `alembic upgrade head` before starting the service"
            )
        applied = connection.execute(
            text(f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE} LIMIT 1")  # noqa: S608
        ).scalar()
    if not applied:
        raise SchemaAuthorityError(
            "schema is not migrated: alembic_version is empty; "
            "run `alembic upgrade head` before starting the service"
        )
    if expected is not None and str(applied) != expected:
        raise SchemaAuthorityError(
            f"schema revision {applied} does not match repository head {expected}; "
            "run `alembic upgrade head` before starting the service"
        )
    logger.info("database schema revision verified", extra={"revision": str(applied)})
    return str(applied)


def _sqlite_literal(value: object) -> str:
    """Render a Python default as a SQLite DDL literal."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, dict)):
        return "'" + json.dumps(value).replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _sqlite_add_column_clause(column: Column) -> str | None:
    """Render an ``ADD COLUMN`` clause for SQLite, or ``None`` when impossible.

    SQLite only accepts ``ALTER TABLE ... ADD COLUMN`` for a nullable column or
    one with a constant default, so the default has to be derived from the model
    for the migration to be expressible at all.
    """
    try:
        type_sql = column.type.compile(sqlite_dialect.dialect())
    except Exception:  # pragma: no cover - a type SQLite cannot name
        return None
    quoted = f'"{column.name}" {type_sql}'
    if column.nullable:
        return quoted
    server_default = getattr(column.server_default, "arg", None)
    if server_default is not None:
        rendered = getattr(server_default, "text", None) or str(server_default)
        return f"{quoted} NOT NULL DEFAULT {rendered}"
    default = column.default
    if default is not None and not default.is_callable:
        return f"{quoted} NOT NULL DEFAULT {_sqlite_literal(default.arg)}"
    if isinstance(column.type, JSON):
        # An empty container is the only sensible constant for a JSON column; the
        # model's own default decides which shape where it declares one.
        factory = getattr(default, "arg", None)
        literal = "{}" if isinstance(factory, dict) or factory is dict else "[]"
        return f"{quoted} NOT NULL DEFAULT '{literal}'"
    if default is not None and default.is_callable:
        # A callable default cannot be evaluated by SQLite, and SQLite rejects a
        # non-constant default in ADD COLUMN outright, so existing rows receive a
        # fixed sentinel rather than a fabricated "now".
        if "DATE" in type_sql.upper() or "TIME" in type_sql.upper():
            logger.warning(
                "existing rows receive the epoch for a timestamp column added in place",
                extra={"column": column.name},
            )
            return f"{quoted} NOT NULL DEFAULT '1970-01-01 00:00:00'"
    return None


def _apply_sqlite_column_migrations(engine: Engine) -> None:
    """Bring an existing development SQLite file up to the current ORM.

    The columns are derived from the model metadata rather than a hand-maintained
    list, because that list is exactly how this upgrade path broke: the ORM
    gained ``roles.actions``, ``roles.updated_at``, ``users.external_subject``,
    ``tenant_id`` on fifteen tables and ``version``, none of which were recorded,
    and an existing development database could no longer start at all.
    ``SQLITE_COLUMN_MIGRATIONS`` is still consulted first so that a column whose
    DDL cannot be derived can be specified by hand.

    A column that SQLite cannot add to a non-empty table is reported instead of
    being skipped silently, so the gap stays visible.
    """
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, table in sorted(SQLModel.metadata.tables.items()):
            if table_name not in existing_tables:
                continue
            existing_columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            overrides = SQLITE_COLUMN_MIGRATIONS.get(table_name, {})
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                clause = overrides.get(column.name) or _sqlite_add_column_clause(column)
                if clause is None:
                    logger.warning(
                        "development database is missing a column that cannot be added in place",
                        extra={"table": table_name, "column": column.name},
                    )
                    continue
                connection.execute(
                    text(f'ALTER TABLE "{table_name}" ADD COLUMN {clause}')
                )
                logger.info(
                    "added column to development database",
                    extra={"table": table_name, "column": column.name},
                )


def _split_legacy_model_providers(engine: Engine) -> None:
    with Session(engine) as session:
        embedding_exists = session.exec(
            select(ModelProvider).where(ModelProvider.capability == "embedding")
        ).first()
        if embedding_exists is not None:
            return
        legacy = session.exec(
            select(ModelProvider).where(
                ModelProvider.capability == "chat",
                col(ModelProvider.default_embedding_model).is_not(None),
            )
        ).first()
        if legacy is None or not legacy.default_embedding_model:
            return
        name = f"{legacy.name}-embedding"
        if session.exec(select(ModelProvider).where(ModelProvider.name == name)).first():
            name = f"{name}-{legacy.id[:8]}"
        session.add(
            ModelProvider(
                name=name,
                provider_type=legacy.provider_type,
                capability="embedding",
                base_url=legacy.base_url,
                api_key_env=legacy.api_key_env,
                api_key_ciphertext=legacy.api_key_ciphertext,
                default_chat_model=legacy.default_embedding_model,
                default_embedding_model=legacy.default_embedding_model,
                enabled=legacy.enabled,
                config_json=dict(legacy.config_json),
            )
        )
        session.commit()


def _infer_model_provider_api_styles(engine: Engine) -> None:
    with Session(engine) as session:
        providers = session.exec(select(ModelProvider)).all()
        changed = False
        for provider in providers:
            current_style = provider.config_json.get("api_style")
            if current_style and current_style != "anthropic_messages":
                continue
            api_style = (
                "openai_embeddings"
                if provider.capability == "embedding"
                else "openai_chat_completions"
            )
            provider.config_json = {**provider.config_json, "api_style": api_style}
            session.add(provider)
            changed = True
        if changed:
            session.commit()


def _protect_existing_mcp_configuration(engine: Engine, settings: Settings) -> None:
    with Session(engine) as session:
        servers = session.exec(select(MCPServer)).all()
        changed = False
        for server in servers:
            raw_config = reveal_config(server.config, settings.SECRET_KEY)
            mode = str(raw_config.get("mode") or server.integration_mode)
            if mode != "mock" and server.integration_mode == "mock":
                server.server_type = "external"
                server.integration_mode = "stdio"
            protected_command = protect_command(server.command, settings.SECRET_KEY)
            protected_config = protect_config(server.config, settings.SECRET_KEY)
            if server.command != protected_command or server.config != protected_config:
                server.command = protected_command
                server.config = protected_config
                session.add(server)
                changed = True
        if changed:
            session.commit()


def _redact_existing_tool_logs(engine: Engine) -> None:
    with Session(engine) as session:
        logs = session.exec(select(ToolCallLog)).all()
        changed = False
        for log in logs:
            redacted_input = redact_sensitive(log.input_summary)
            redacted_output = redact_sensitive(log.output_summary)
            error_message = (
                "Legacy tool execution failed"
                if log.error_message
                and not log.error_message.startswith(
                    (
                        "MCP_",
                        "TOOL_",
                        "VALIDATION_ERROR:",
                        "MEMORY_",
                    )
                )
                else log.error_message
            )
            if (
                log.input_summary != redacted_input
                or log.output_summary != redacted_output
                or log.error_message != error_message
            ):
                log.input_summary = redacted_input
                log.output_summary = redacted_output
                log.error_message = error_message
                session.add(log)
                changed = True
        if changed:
            session.commit()


def _ensure_permission(
    session: Session,
    knowledge_base_id: str,
    subject_type: str,
    subject_id: str,
    permission: str,
) -> bool:
    existing = session.exec(
        select(KnowledgeBasePermission).where(
            KnowledgeBasePermission.knowledge_base_id == knowledge_base_id,
            KnowledgeBasePermission.subject_type == subject_type,
            KnowledgeBasePermission.subject_id == subject_id,
            KnowledgeBasePermission.permission == permission,
        )
    ).first()
    if existing is not None:
        return False
    session.add(
        KnowledgeBasePermission(
            knowledge_base_id=knowledge_base_id,
            subject_type=subject_type,
            subject_id=subject_id,
            permission=permission,
        )
    )
    return True


def _adopt_unowned_rows(session: Session, tenant_id: str) -> int:
    """Give every row without a tenant to ``tenant_id`` and return the row count.

    This mirrors the PostgreSQL backfill so development and production agree on
    ownership. It matters because a development database that predates tenancy
    has ``tenant_id = NULL`` everywhere: without adoption the seed would not find
    the existing ``employee`` role, would try to insert a second one, and the
    pre-tenant global unique index would abort startup.
    """
    adopted = 0
    for table in SQLModel.metadata.sorted_tables:
        if "tenant_id" not in table.columns:
            continue
        result = session.execute(
            table.update()
            .where(table.c.tenant_id.is_(None))
            .values(tenant_id=tenant_id)
        )
        adopted += int(result.rowcount or 0)
    return adopted


def _resolve_bootstrap_tenant(session: Session) -> tuple[Tenant, bool]:
    """Return the tenant that owns seeded reference data, and whether it was created.

    Reference data (roles, knowledge bases, the bootstrap administrator) is
    tenant-owned, because the enforced schema makes ``tenant_id`` NOT NULL and
    per-tenant uniqueness means a role code only means something inside one
    tenant. On an upgraded deployment the ``legacy`` tenant already owns every
    pre-tenant row, so seeding joins it instead of inventing a second root; on a
    fresh database a tenant is created from the bootstrapping configuration.
    """
    tenant = session.exec(select(Tenant).where(Tenant.code == LEGACY_TENANT_CODE)).first()
    if tenant is not None:
        return tenant, False
    tenant = session.exec(select(Tenant).where(Tenant.code == DEFAULT_TENANT_CODE)).first()
    if tenant is not None:
        return tenant, False

    # No tenant exists yet. If rows already exist they predate tenancy (an older
    # development database file), and they must be adopted rather than abandoned.
    if _unowned_row_count(session):
        tenant = Tenant(
            id=LEGACY_TENANT_ID,
            code=LEGACY_TENANT_CODE,
            name=LEGACY_TENANT_NAME,
            status="active",
        )
        session.add(tenant)
        session.flush()
        adopted = _adopt_unowned_rows(session, tenant.id)
        logger.info(
            "adopted pre-tenancy rows into the legacy tenant",
            extra={"tenant": LEGACY_TENANT_CODE, "rows": adopted},
        )
        return tenant, True

    tenant = Tenant(
        code=DEFAULT_TENANT_CODE,
        name=DEFAULT_TENANT_NAME,
        status="active",
    )
    session.add(tenant)
    session.flush()
    return tenant, True


def _unowned_row_count(session: Session) -> int:
    """Return how many rows across all tenant-scoped tables have no owner."""
    total = 0
    for table in SQLModel.metadata.sorted_tables:
        if "tenant_id" not in table.columns:
            continue
        count = session.execute(
            select(func.count()).select_from(table).where(table.c.tenant_id.is_(None))
        ).scalar()
        total += int(count or 0)
    return total


def seed_initial_data(
    engine: Engine | None = None,
    settings: Settings | None = None,
) -> SeedSummary:
    database_engine = engine or get_engine()
    app_settings = settings or get_settings()
    roles_created = 0
    departments_created = 0
    knowledge_bases_created = 0
    permissions_created = 0
    users_created = 0
    model_providers_created = 0
    skills_created = 0
    tools_created = 0
    tenants_created = 0

    with Session(database_engine) as session:
        tenant, tenant_created = _resolve_bootstrap_tenant(session)
        tenants_created += int(tenant_created)
        tenant_id = tenant.id

        roles: dict[str, Role] = {}
        for code, name, description in ROLE_SEEDS:
            role = session.exec(
                select(Role).where(Role.tenant_id == tenant_id, Role.code == code)
            ).first()
            if role is None:
                role = Role(
                    tenant_id=tenant_id, code=code, name=name, description=description
                )
                session.add(role)
                session.flush()
                roles_created += 1
            roles[code] = role

        departments: dict[str, Department] = {}
        for code, name in DEPARTMENT_SEEDS:
            department = session.exec(
                select(Department).where(Department.code == code)
            ).first()
            if department is None:
                department = Department(code=code, name=name)
                session.add(department)
                session.flush()
                departments_created += 1
            departments[code] = department

        knowledge_bases: dict[str, KnowledgeBase] = {}
        for code, name, description in KNOWLEDGE_BASE_SEEDS:
            knowledge_base = session.exec(
                select(KnowledgeBase).where(
                    KnowledgeBase.tenant_id == tenant_id, KnowledgeBase.code == code
                )
            ).first()
            if knowledge_base is None:
                # eval_test reuses admin department so it is clearly non-business.
                department_code = code if code in departments else "admin"
                knowledge_base = KnowledgeBase(
                    tenant_id=tenant_id,
                    code=code,
                    name=name,
                    description=description,
                    department_id=departments[department_code].id,
                    rag_workspace=str(app_settings.RAG_WORKSPACE_DIR / code),
                    status="active",
                )
                session.add(knowledge_base)
                session.flush()
                knowledge_bases_created += 1
            else:
                # Revive soft-deleted seeded sandboxes (especially eval_test).
                if knowledge_base.status == "deleted" and code in {
                    "eval_test",
                    "enterprise_eval_test",
                }:
                    knowledge_base.status = "active"
                    knowledge_base.name = name
                    knowledge_base.description = description
                    knowledge_base.updated_at = utc_now()
                    session.add(knowledge_base)
            knowledge_bases[code] = knowledge_base

        for code, knowledge_base in knowledge_bases.items():
            department_code = code if code in departments else "admin"
            permissions_created += int(
                _ensure_permission(
                    session,
                    knowledge_base.id,
                    "department",
                    departments[department_code].id,
                    "read",
                )
            )
            permissions_created += int(
                _ensure_permission(
                    session,
                    knowledge_base.id,
                    "role",
                    roles["kb_admin"].id,
                    "admin",
                )
            )
            # Evaluation sandbox should also be manageable by sys_admin / bootstrap admin later.

        if app_settings.BOOTSTRAP_ADMIN_PASSWORD:
            admin = session.exec(
                select(User).where(
                    User.tenant_id == tenant_id,
                    User.username == app_settings.BOOTSTRAP_ADMIN_USERNAME,
                )
            ).first()
            if admin is None:
                admin = User(
                    tenant_id=tenant_id,
                    username=app_settings.BOOTSTRAP_ADMIN_USERNAME,
                    email=app_settings.BOOTSTRAP_ADMIN_EMAIL,
                    password_hash=hash_password(app_settings.BOOTSTRAP_ADMIN_PASSWORD),
                    display_name=app_settings.BOOTSTRAP_ADMIN_DISPLAY_NAME,
                    department_id=departments["admin"].id,
                )
                session.add(admin)
                session.flush()
                users_created += 1

            admin_role = session.get(UserRole, (admin.id, roles["sys_admin"].id))
            if admin_role is None:
                session.add(UserRole(user_id=admin.id, role_id=roles["sys_admin"].id))

            # The principal and fresh authorization read grants, not the legacy
            # link table, so the bootstrap administrator needs a grant as well.
            # Without it the very first account could log in but never act.
            existing_grant = session.exec(
                select(UserRoleGrant).where(
                    UserRoleGrant.tenant_id == admin.tenant_id,
                    UserRoleGrant.user_id == admin.id,
                    UserRoleGrant.role_id == roles["sys_admin"].id,
                )
            ).first()
            if existing_grant is None:
                session.add(
                    UserRoleGrant(
                        tenant_id=admin.tenant_id,
                        user_id=admin.id,
                        role_id=roles["sys_admin"].id,
                    )
                )

        for name, description, risk_level in SKILL_SEEDS:
            if session.exec(select(Skill).where(Skill.name == name)).first() is None:
                session.add(Skill(name=name, description=description, risk_level=risk_level))
                skills_created += 1

        for name in TOOL_SEEDS:
            if session.exec(select(Tool).where(Tool.name == name)).first() is None:
                session.add(
                    Tool(
                        name=name,
                        description=f"Built-in tool: {name}",
                        input_schema={"type": "object"},
                        output_schema={"type": "object"},
                    )
                )
                tools_created += 1

        if app_settings.LLM_BASE_URL and app_settings.LLM_CHAT_MODEL:
            provider = session.exec(
                select(ModelProvider).where(ModelProvider.capability == "chat")
            ).first()
            if provider is None:
                session.add(
                    ModelProvider(
                        name=app_settings.LLM_PROVIDER_NAME,
                        capability="chat",
                        base_url=app_settings.LLM_BASE_URL,
                        api_key_env=app_settings.LLM_API_KEY_ENV,
                        default_chat_model=app_settings.LLM_CHAT_MODEL,
                    )
                )
                model_providers_created += 1
            if app_settings.LLM_EMBEDDING_MODEL:
                embedding_provider = session.exec(
                    select(ModelProvider).where(ModelProvider.capability == "embedding")
                ).first()
                if embedding_provider is None:
                    session.add(
                        ModelProvider(
                            name=f"{app_settings.LLM_PROVIDER_NAME}-embedding",
                            capability="embedding",
                            base_url=app_settings.LLM_BASE_URL,
                            api_key_env=app_settings.LLM_API_KEY_ENV,
                            default_chat_model=app_settings.LLM_EMBEDDING_MODEL,
                            default_embedding_model=app_settings.LLM_EMBEDDING_MODEL,
                            config_json={"embedding_dim": app_settings.LLM_EMBEDDING_DIM},
                        )
                    )
                    model_providers_created += 1

        session.commit()

    summary = SeedSummary(
        roles_created=roles_created,
        departments_created=departments_created,
        knowledge_bases_created=knowledge_bases_created,
        permissions_created=permissions_created,
        users_created=users_created,
        model_providers_created=model_providers_created,
        skills_created=skills_created,
        tools_created=tools_created,
        tenants_created=tenants_created,
    )
    logger.info("Database seed completed", extra={"seed_summary": asdict(summary)})
    return summary


def _run_backfill_once(engine: Engine, name: str, runner: Callable[[], None]) -> None:
    """历史数据回填迁移只需执行一次；用标记表避免每次启动全表扫描。"""
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS app_backfill_migrations "
                "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        )
        applied = connection.execute(
            text("SELECT 1 FROM app_backfill_migrations WHERE name = :name"),
            {"name": name},
        ).first()
    if applied is not None:
        return
    runner()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO app_backfill_migrations (name, applied_at) "
                "VALUES (:name, :applied_at)"
            ),
            {"name": name, "applied_at": utc_now().isoformat()},
        )


def initialize_database(
    engine: Engine | None = None,
    settings: Settings | None = None,
) -> SeedSummary:
    """Bring the database to a usable state for the configured environment.

    Production verifies the migrated schema and seeds reference data; it never
    creates or migrates schema. Development/test keep the SQLite convenience
    bootstrap so the app starts without a migration step.
    """
    app_settings = settings or get_settings()
    if app_settings.ENVIRONMENT is Environment.PRODUCTION:
        verify_schema_version(engine)
        return seed_initial_data(engine or get_engine(), app_settings)

    database_engine = create_db_and_tables(engine)
    _apply_sqlite_column_migrations(database_engine)
    _run_backfill_once(
        database_engine,
        "split_legacy_model_providers",
        lambda: _split_legacy_model_providers(database_engine),
    )
    _run_backfill_once(
        database_engine,
        "infer_model_provider_api_styles",
        lambda: _infer_model_provider_api_styles(database_engine),
    )
    _run_backfill_once(
        database_engine,
        "protect_existing_mcp_configuration",
        lambda: _protect_existing_mcp_configuration(database_engine, app_settings),
    )
    _run_backfill_once(
        database_engine,
        "redact_existing_tool_logs",
        lambda: _redact_existing_tool_logs(database_engine),
    )
    return seed_initial_data(database_engine, app_settings)


def main() -> None:
    initialize_database()


if __name__ == "__main__":
    main()
