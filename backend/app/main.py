"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from re import compile as compile_pattern
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from backend.app.agents.answer_agent import AnswerAgent
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.memory_agent import MemoryAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.agents.retrieval_agent import RetrievalAgent
from backend.app.agents.router_agent import RouterAgent
from backend.app.agents.skill_agent import SkillAgent
from backend.app.api.routes_audit import router as audit_router
from backend.app.api.routes_auth import router as auth_router
from backend.app.api.routes_chat import router as chat_router
from backend.app.api.routes_draft import router as draft_router
from backend.app.api.routes_eval import router as eval_router
from backend.app.api.routes_faq import router as faq_router
from backend.app.api.routes_feedback import router as feedback_router
from backend.app.api.routes_kb import departments_router, documents_router
from backend.app.api.routes_kb import router as knowledge_base_router
from backend.app.api.routes_mcp import router as mcp_router
from backend.app.api.routes_memory import router as memory_router
from backend.app.api.routes_settings import router as settings_router
from backend.app.api.routes_skill import router as skill_router
from backend.app.api.routes_tool import router as tool_router
from backend.app.api.routes_users import router as users_router
from backend.app.core.config import Settings, get_settings
from backend.app.core.exceptions import (
    register_exception_handlers,
    unexpected_exception_response,
)
from backend.app.core.logging import (
    bind_request_id,
    configure_logging,
    get_logger,
    reset_request_id,
)
from backend.app.db.init_db import initialize_database
from backend.app.db.session import build_engine, get_engine
from backend.app.frontend import mount_frontend
from backend.app.mcp.manager import MCPManager
from backend.app.rag.bm25_retriever import BM25Retriever
from backend.app.rag.hybrid_retriever import HybridRetriever
from backend.app.rag.inprocess_lightrag import InProcessLightRAGAdapter
from backend.app.rag.protocols import LightRAGBackend, LLMService
from backend.app.services.embedding_service import OpenAICompatibleEmbeddingService
from backend.app.services.llm_service import OpenAICompatibleLLMService
from backend.app.services.rag_service import RAGService
from backend.app.skills.registry import SkillRegistry
from backend.app.tools.builtin_tools import (
    draft_create_tool,
    draft_update_tool,
    mcp_call_tool,
    memory_read_tool,
    memory_write_tool,
)
from backend.app.tools.registry import ToolRegistry

logger = get_logger(__name__)
REQUEST_ID_PATTERN = compile_pattern(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


def create_app(
    settings: Settings | None = None,
    database_engine: Engine | None = None,
    lightrag_adapter: LightRAGBackend | None = None,
    llm_service: LLMService | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.LOG_LEVEL, app_settings.log_file)
    engine = database_engine or (
        get_engine()
        if settings is None
        else build_engine(app_settings.DATABASE_URL, app_settings.DATABASE_ECHO)
    )
    language_model = llm_service or OpenAICompatibleLLMService(engine, app_settings)
    embedding_service = OpenAICompatibleEmbeddingService(engine, app_settings)
    adapter = lightrag_adapter or InProcessLightRAGAdapter(
        engine, app_settings, language_model, embedding_service
    )
    bm25_retriever = BM25Retriever(engine)
    hybrid_retriever = HybridRetriever(adapter, bm25_retriever)
    rag_service = RAGService(adapter, bm25=bm25_retriever, hybrid=hybrid_retriever)
    skill_registry = SkillRegistry(language_model)
    mcp_manager = MCPManager(app_settings)
    tool_registry = ToolRegistry()
    tool_registry.register("draft.create", draft_create_tool)
    tool_registry.register("draft.update", draft_update_tool)
    tool_registry.register("memory.read", memory_read_tool)
    tool_registry.register("memory.write", memory_write_tool)
    tool_registry.register("mcp.call", mcp_call_tool(mcp_manager))
    pipeline = AgentPipeline(
        RouterAgent(language_model),
        RetrievalAgent(rag_service),
        AnswerAgent(language_model, app_settings),
        SkillAgent(skill_registry),
        ComplianceAgent(app_settings),
    )
    memory_agent = MemoryAgent(
        app_settings,
        llm_service=language_model,
        embedding_service=embedding_service,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        summary = initialize_database(engine, app_settings)
        logger.info(
            "Application started",
            extra={
                "environment": app_settings.ENVIRONMENT,
                "database_seed": asdict(summary),
            },
        )
        yield
        close_adapter = getattr(adapter, "close", None)
        if close_adapter is not None:
            await close_adapter()
        engine.dispose()
        logger.info("Application stopped")

    application = FastAPI(
        title=app_settings.PROJECT_NAME,
        version=app_settings.VERSION,
        description="Enterprise Policy Assistant",
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.engine = engine
    application.state.lightrag_adapter = adapter
    application.state.llm_service = language_model
    application.state.embedding_service = embedding_service
    application.state.rag_service = rag_service
    application.state.agent_pipeline = pipeline
    application.state.memory_agent = memory_agent
    application.state.skill_registry = skill_registry
    application.state.tool_registry = tool_registry
    application.state.mcp_manager = mcp_manager

    @application.middleware("http")
    async def add_request_id(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        incoming_request_id = request.headers.get("X-Request-ID", "").strip()
        request_id = (
            incoming_request_id
            if REQUEST_ID_PATTERN.fullmatch(incoming_request_id)
            else str(uuid4())
        )
        request.state.request_id = request_id
        context_token = bind_request_id(request_id)
        started_at = perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                response = unexpected_exception_response(request, exc)
            duration_ms = round((perf_counter() - started_at) * 1000, 2)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Process-Time-Ms"] = str(duration_ms)
            logger.info(
                "HTTP request completed",
                extra={
                    "request_method": request.method,
                    "request_path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response
        finally:
            reset_request_id(context_token)

    register_exception_handlers(application)
    application.include_router(audit_router)
    application.include_router(auth_router)
    application.include_router(chat_router)
    application.include_router(draft_router)
    application.include_router(eval_router)
    application.include_router(faq_router)
    application.include_router(feedback_router)
    application.include_router(knowledge_base_router)
    application.include_router(documents_router)
    application.include_router(departments_router)
    application.include_router(mcp_router)
    application.include_router(memory_router)
    application.include_router(settings_router)
    application.include_router(skill_router)
    application.include_router(tool_router)
    application.include_router(users_router)

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    if frontend_dist is not None:
        mount_frontend(application, frontend_dist)

    return application


app = create_app(frontend_dist=DEFAULT_FRONTEND_DIST)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
