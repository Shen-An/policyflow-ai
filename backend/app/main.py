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
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from backend.app.agents.answer_agent import AnswerAgent
from backend.app.agents.compliance_agent import ComplianceAgent
from backend.app.agents.critique_agent import CritiqueAgent
from backend.app.agents.improve_agent import ImproveAgent
from backend.app.agents.memory_agent import MemoryAgent
from backend.app.agents.pipeline import AgentPipeline
from backend.app.graph.compat import AdapterUsageTelemetry
from backend.app.graph.service import GraphService
from backend.app.agents.reflection_loop import ReflectionLoop
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
from backend.app.api.routes_v2 import router as v2_router
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
from backend.app.db.repositories import UnitOfWork
from backend.app.db.session import (
    build_async_engine,
    build_engine,
    check_database_ready,
    get_engine,
)
from backend.app.frontend import mount_frontend
from backend.app.mcp.manager import MCPManager
from backend.app.observability.telemetry import configure_telemetry
from backend.app.rag.bm25_retriever import BM25Retriever
from backend.app.rag.cross_encoder_rerank_service import NvidiaCrossEncoderRerankService
from backend.app.rag.hybrid_retriever import HybridRetriever
from backend.app.rag.inprocess_lightrag import InProcessLightRAGAdapter
from backend.app.rag.protocols import LightRAGBackend, LLMService
from backend.app.rag.rerank_service import RerankService
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


def _build_rerankers(settings: Settings, engine: Engine) -> dict[str, RerankService | NvidiaCrossEncoderRerankService]:
    models = tuple(
        item.strip()
        for item in settings.NVIDIA_RERANKER_MODELS.split(",")
        if item.strip()
    )
    return {
        "local_lexical_fusion": RerankService(),
        "cross_encoder": NvidiaCrossEncoderRerankService(
            models=models,
            base_url=settings.NVIDIA_RERANKER_BASE_URL,
            endpoint_template=settings.NVIDIA_RERANKER_ENDPOINT_TEMPLATE,
            api_key_env=settings.NVIDIA_RERANKER_API_KEY_ENV,
            timeout_seconds=settings.NVIDIA_RERANKER_TIMEOUT_SECONDS,
            truncate=settings.NVIDIA_RERANKER_TRUNCATE,
            engine=engine,
            settings=settings,
        ),
    }


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
    hybrid_retriever = HybridRetriever(
        adapter,
        bm25_retriever,
        lightrag_timeout_seconds=app_settings.LIGHTRAG_HYBRID_TIMEOUT_SECONDS,
    )
    rerankers = _build_rerankers(app_settings, engine)
    rag_service = RAGService(
        adapter,
        bm25=bm25_retriever,
        hybrid=hybrid_retriever,
        rerankers=rerankers,
    )
    skill_registry = SkillRegistry(language_model)
    mcp_manager = MCPManager(app_settings)

    # The async plane is built alongside the synchronous one. It owns its own
    # engine because async connections cannot be shared with synchronous callers,
    # and it is what every unit of work runs on. It is defined before the tools and
    # the memory agent because those now do their tenant-owned reads and writes
    # through a unit of work rather than the synchronous session.
    async_engine = build_async_engine(app_settings.DATABASE_URL, app_settings)
    async_session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

    def build_unit_of_work() -> UnitOfWork:
        """Return a unit of work bound to this application's async engine.

        The factory is published on the application so that a request neither
        reaches for a process-wide engine nor invents its own transaction scope.
        """
        return UnitOfWork(factory=async_session_factory)

    tool_registry = ToolRegistry()
    tool_registry.register("draft.create", draft_create_tool)
    tool_registry.register("draft.update", draft_update_tool)
    tool_registry.register("memory.read", memory_read_tool(build_unit_of_work))
    tool_registry.register("memory.write", memory_write_tool(build_unit_of_work))
    tool_registry.register("mcp.call", mcp_call_tool(mcp_manager))
    reflection_loop = ReflectionLoop(
        CritiqueAgent(language_model, app_settings),
        ImproveAgent(language_model, app_settings),
        app_settings,
    )
    pipeline = AgentPipeline(
        RouterAgent(language_model),
        RetrievalAgent(rag_service),
        AnswerAgent(language_model, app_settings),
        SkillAgent(skill_registry),
        ComplianceAgent(app_settings),
        app_settings,
        reflection_loop=reflection_loop,
    )
    memory_agent = MemoryAgent(
        app_settings,
        llm_service=language_model,
        embedding_service=embedding_service,
        uow_factory=build_unit_of_work,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_telemetry(service_name=app_settings.PROJECT_NAME)
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
        for reranker in {id(item): item for item in rerankers.values()}.values():
            close_reranker = getattr(reranker, "close", None)
            if close_reranker is not None:
                await close_reranker()
        await async_engine.dispose()
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
    application.state.async_engine = async_engine
    application.state.uow_factory = build_unit_of_work
    application.state.lightrag_adapter = adapter
    application.state.llm_service = language_model
    application.state.embedding_service = embedding_service
    application.state.rag_service = rag_service
    application.state.rerankers = rerankers
    application.state.reranker = rerankers["local_lexical_fusion"]
    application.state.agent_pipeline = pipeline
    application.state.memory_agent = memory_agent
    # Stage 3 shared graph: the unified authorized-run/parity surface, plus the
    # legacy-adapter usage sink that gates Stage 9 removal. Published additively;
    # routes cut over to it behind the removal ledger (see graph/compat.py).
    application.state.graph_service = GraphService()
    application.state.adapter_usage_telemetry = AdapterUsageTelemetry()
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
    application.include_router(v2_router)

    @application.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Liveness: this process is running.

        Deliberately does not touch the database. A liveness probe that failed on
        a database blip would restart otherwise healthy instances and turn one
        dependency's outage into a fleet-wide one.
        """
        return {"status": "ok"}

    @application.get("/ready", tags=["system"])
    async def readiness_check() -> JSONResponse:
        """Readiness: this instance may serve authoritative reads.

        Answers 503 unless the schema has reached the expected revision, so an
        instance that is running but pointed at an unmigrated database is kept
        out of rotation instead of answering with wrong results. This is the only
        place where "the process is up" and "the process can be trusted" are
        distinguished.
        """
        report = await check_database_ready(async_engine)
        return JSONResponse(
            status_code=200 if report.ok else 503,
            content={
                "status": "ready" if report.ok else "not-ready",
                "database": report.reason or "ok",
                "dialect": report.dialect,
                "schema_revision": report.schema_revision,
                "expected_revision": report.expected_revision,
            },
        )

    if frontend_dist is not None:
        mount_frontend(application, frontend_dist)

    return application


app = create_app(frontend_dist=DEFAULT_FRONTEND_DIST)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
