"""Independent Chat and Embedding model settings routes."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request

from backend.app.api.deps import SessionDep
from backend.app.core.exceptions import ApplicationError
from backend.app.core.logging import get_request_id
from backend.app.core.permissions import require_roles
from backend.app.db.models import User
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
    capability: Literal["chat", "embedding"],
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
    capability: Literal["chat", "embedding"],
    request: Request,
    _: SysAdminUser,
) -> ModelCatalogResponse:
    if capability == "chat":
        service: OpenAICompatibleLLMService = request.app.state.llm_service
        models = await service.list_models()
    else:
        embedding_service: OpenAICompatibleEmbeddingService = request.app.state.embedding_service
        models = await embedding_service.list_models()
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
        else:
            embedding_service: OpenAICompatibleEmbeddingService = request.app.state.embedding_service
            vectors = await embedding_service.embed(["PolicyFlow connectivity test"])
            result = ModelCapabilityResult(
                status="passed",
                message="Embedding model returned a vector",
                dimension=len(vectors[0]),
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
    capability: Literal["chat", "embedding"],
    request: Request,
    _: SysAdminUser,
) -> ModelProviderTestResponse:
    return await _test_capability(capability, request)
