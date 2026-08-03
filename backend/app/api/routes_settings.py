"""Independent Chat, Embedding, and Reranker model settings routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request

from backend.app.api.deps import SessionDep
from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_request_id
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
from backend.app.rag.cross_encoder_rerank_service import DEFAULT_NVIDIA_RERANKER_MODELS, NvidiaCrossEncoderRerankService
from backend.app.schemas.retrieval import Evidence
from backend.app.schemas.model_settings import (
    ModelCapabilityResult,
    ModelCatalogResponse,
    ModelEndpointSettingsRead,
    ModelEndpointSettingsUpdate,
    ModelProviderSettingsResponse,
    ModelProviderTestResponse,
)
from backend.app.services.embedding_service import OpenAICompatibleEmbeddingService
from backend.app.services.llm_service import OpenAICompatibleLLMService
from backend.app.services.model_settings_service import (
    Capability,
    get_model_provider_settings,
    update_model_provider_settings,
)

router = APIRouter(prefix="/api/settings/model-providers", tags=["settings"])
SysAdminUser = Annotated[User, Depends(require_roles("sys_admin"))]


@router.get("", response_model=ModelProviderSettingsResponse)
def get_settings(session: SessionDep, _: SysAdminUser) -> ModelProviderSettingsResponse:
    return get_model_provider_settings(session)


@router.put("/{capability}", response_model=ModelEndpointSettingsRead)
def put_settings(
    capability: Literal["chat", "embedding", "reranker"],
    data: ModelEndpointSettingsUpdate,
    request: Request,
    session: SessionDep,
    user: SysAdminUser,
) -> ModelEndpointSettingsRead:
    return update_model_provider_settings(
        session,
        request.app.state.settings,
        user,
        capability,
        data,
        request.client.host if request.client else None,
    )


@router.get("/{capability}/models", response_model=ModelCatalogResponse)
async def get_models(
    capability: Literal["chat", "embedding", "reranker"],
    request: Request,
    _: SysAdminUser,
) -> ModelCatalogResponse:
    if capability == "chat":
        service: OpenAICompatibleLLMService = request.app.state.llm_service
        models = await service.list_models()
    elif capability == "embedding":
        embedding_service: OpenAICompatibleEmbeddingService = request.app.state.embedding_service
        models = await embedding_service.list_models()
    else:
        models = list(DEFAULT_NVIDIA_RERANKER_MODELS)
    return ModelCatalogResponse(capability=capability, models=models)


async def _test_capability(
    capability: Capability,
    request: Request,
) -> ModelProviderTestResponse:
    try:
        if capability == "chat":
            service: OpenAICompatibleLLMService = request.app.state.llm_service
            answer = await service.complete("Return exactly the word OK.", "Connectivity test")
            result = ModelCapabilityResult(
                status="passed",
                message=f"Chat model responded: {answer[:80]}",
            )
        elif capability == "embedding":
            embedding_service: OpenAICompatibleEmbeddingService = request.app.state.embedding_service
            vectors = await embedding_service.embed(["PolicyFlow connectivity test"])
            result = ModelCapabilityResult(
                status="passed",
                message="Embedding model returned a vector",
                dimension=len(vectors[0]),
            )
        else:
            reranker: NvidiaCrossEncoderRerankService = request.app.state.rerankers["cross_encoder"]
            await reranker.rerank(
                "travel lodging standard",
                [
                    Evidence(
                        knowledge_base_id="settings-test",
                        knowledge_base_name="settings-test",
                        document_id="settings-test",
                        document_title="Connectivity test",
                        snippet="Travel lodging standard is 500 units per night.",
                        retriever_type="settings_test",
                        rank=1,
                    )
                ],
                limit=1,
            )
            result = ModelCapabilityResult(
                status="passed",
                message="NVIDIA Cross-Encoder returned a relevance score",
            )
    except ApplicationError as exc:
        result = ModelCapabilityResult(
            status="failed",
            message=exc.message,
            error_code=exc.code,
        )
    return ModelProviderTestResponse(
        capability=capability,
        result=result,
        request_id=get_request_id(),
    )


@router.post("/{capability}/test", response_model=ModelProviderTestResponse)
async def post_test(
    capability: Literal["chat", "embedding", "reranker"],
    request: Request,
    _: SysAdminUser,
) -> ModelProviderTestResponse:
    return await _test_capability(capability, request)
