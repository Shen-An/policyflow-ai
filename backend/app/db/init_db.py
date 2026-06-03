"""Create database tables and idempotently insert roadmap seed data."""

from dataclasses import asdict, dataclass

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, col, select

from backend.app.core.config import Settings, get_settings
from backend.app.core.logging import get_logger
from backend.app.core.mcp_security import protect_command, protect_config, reveal_config
from backend.app.core.redaction import redact_sensitive
from backend.app.core.security import hash_password
from backend.app.db import base  # noqa: F401
from backend.app.db.models import (
    Department,
    KnowledgeBase,
    KnowledgeBasePermission,
    MCPServer,
    ModelProvider,
    Role,
    Skill,
    Tool,
    ToolCallLog,
    User,
    UserRole,
    utc_now,
)
from backend.app.db.session import get_engine

logger = get_logger(__name__)

SQLITE_COLUMN_MIGRATIONS = {
    "model_providers": {
        "api_key_ciphertext": "api_key_ciphertext TEXT",
        "capability": "capability VARCHAR(20) NOT NULL DEFAULT 'chat'",
    },
    "audit_logs": {
        "request_id": "request_id VARCHAR(128)",
    },
    "eval_runs": {
        "error_summary": "error_summary TEXT",
        "request_id": "request_id VARCHAR(128)",
    },
    "eval_results": {
        "answer_metrics": "answer_metrics JSON",
        "type_statuses": "type_statuses JSON NOT NULL DEFAULT '{}'",
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
    "memory_items": {
        "embedding": "embedding JSON",
        "meta_json": "meta_json JSON NOT NULL DEFAULT '{}'",
    },
    "knowledge_documents": {
        "external_id": "external_id VARCHAR(128)",
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


def create_db_and_tables(engine: Engine | None = None) -> Engine:
    database_engine = engine or get_engine()
    SQLModel.metadata.create_all(database_engine)
    return database_engine


def _apply_sqlite_column_migrations(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as connection:
        for table_name, columns in SQLITE_COLUMN_MIGRATIONS.items():
            if table_name not in table_names:
                continue
            existing_columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for column_name, definition in columns.items():
                if column_name not in existing_columns:
                    connection.execute(
                        text(f'ALTER TABLE "{table_name}" ADD COLUMN {definition}')
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

    with Session(database_engine) as session:
        roles: dict[str, Role] = {}
        for code, name, description in ROLE_SEEDS:
            role = session.exec(select(Role).where(Role.code == code)).first()
            if role is None:
                role = Role(code=code, name=name, description=description)
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
                select(KnowledgeBase).where(KnowledgeBase.code == code)
            ).first()
            if knowledge_base is None:
                # eval_test reuses admin department so it is clearly non-business.
                department_code = code if code in departments else "admin"
                knowledge_base = KnowledgeBase(
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
                if knowledge_base.status == "deleted" and code == "eval_test":
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
                select(User).where(User.username == app_settings.BOOTSTRAP_ADMIN_USERNAME)
            ).first()
            if admin is None:
                admin = User(
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
    )
    logger.info("Database seed completed", extra={"seed_summary": asdict(summary)})
    return summary


def initialize_database(
    engine: Engine | None = None,
    settings: Settings | None = None,
) -> SeedSummary:
    app_settings = settings or get_settings()
    database_engine = create_db_and_tables(engine)
    _apply_sqlite_column_migrations(database_engine)
    _split_legacy_model_providers(database_engine)
    _infer_model_provider_api_styles(database_engine)
    _protect_existing_mcp_configuration(database_engine, app_settings)
    _redact_existing_tool_logs(database_engine)
    return seed_initial_data(database_engine, app_settings)


def main() -> None:
    initialize_database()


if __name__ == "__main__":
    main()
