"""T085 [US1] ``POST /api/v2/materials``: declare an upload, get a scoped grant.

The route never receives bytes. A client declares what it is about to upload --
filename, size, SHA-256, media type, purpose -- and receives a short-lived,
single-object upload grant plus the material and version IDs it will use
afterwards. That split is what lets the API enforce limits *before* anything is
transferred, and it is why the client never learns a bucket or a key.

Validation is the contract, so it is spelled out rather than inferred:

* ``filename`` 1..255 characters -- it is recorded for audit and display only and
  deliberately does not influence the derived object key (a user-supplied name in
  a key is both a traversal surface and an information leak);
* ``size_bytes`` at least 1 -- a zero-byte material has no evidence value, and the
  configured ceiling produces ``413`` rather than a generic rejection, because the
  caller can act on "too large" but not on "invalid";
* ``sha256`` must match ``^[a-f0-9]{64}$`` -- lowercase hex, checked at the edge so
  a malformed digest never reaches the verification step where it would surface as
  a confusing mismatch;
* ``media_type`` must be in the configured allowlist, producing ``415``;
* ``purpose`` is ``task_input`` or ``policy_import``, and only ``policy_import``
  may create a formal policy material. An employee uploading a task input must not
  be able to introduce something that later reads as authoritative policy.

Everything else (413 vs 415 vs 422 and the tenant it all happens in) comes from
the signed token's membership; no tenant or user is ever read from the body.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import async_sessionmaker

from backend.app.api.deps import PrincipalDep
from backend.app.core.exceptions import ApplicationError
from backend.app.retrieval.indexer import VectorIndexer
from backend.app.retrieval.milvus import MilvusVectorStore, MilvusVectorStoreConfig
from backend.app.storage.object_store import ObjectStore, ObjectStoreConfig
from backend.app.storage.saga import MaterialSaga, SagaStateError

router = APIRouter(prefix="/api/v2", tags=["v2", "materials"])

FILENAME_MIN_LENGTH = 1
FILENAME_MAX_LENGTH = 255

#: Lowercase hex SHA-256. Uppercase is rejected rather than normalised: the digest
#: is compared byte-for-byte downstream, and accepting two spellings of the same
#: value invites a mismatch that looks like corruption.
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")

#: What an uploaded material may be used for.
#:
#: ``task_input`` -- material a user brings to a workflow (a receipt, a form).
#: ``policy_import`` -- a formal enterprise policy original, which becomes
#: read-only and may be cited as authoritative evidence.
MATERIAL_PURPOSES = ("task_input", "policy_import")

#: Purpose -> (source_type, read_only). Only ``policy_import`` produces a formal
#: policy item; a ``task_input`` can never become one, which is what stops an
#: ordinary upload from later reading as authoritative policy.
_PURPOSE_BINDING: dict[str, tuple[str, bool]] = {
    "task_input": ("user_upload", False),
    "policy_import": ("policy", True),
}

#: Media types accepted for upload. An allowlist rather than a denylist: anything
#: not understood here cannot be chunked into trustworthy evidence anyway.
DEFAULT_ALLOWED_MEDIA_TYPES: tuple[str, ...] = (
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

#: Upper bound on a declared material size. Enforced here so the caller is told
#: "too large" before transferring anything.
DEFAULT_MAX_MATERIAL_BYTES = 64 * 1024 * 1024


class MaterialUploadRequest(BaseModel):
    """Body for ``POST /materials``. Tenant and user are never accepted here."""

    knowledge_base_id: str = Field(min_length=1, max_length=36)
    filename: str = Field(min_length=FILENAME_MIN_LENGTH, max_length=FILENAME_MAX_LENGTH)
    size_bytes: int = Field(ge=1)
    sha256: str = Field(min_length=64, max_length=64)
    media_type: str = Field(min_length=1, max_length=180)
    purpose: Literal["task_input", "policy_import"]
    #: Supplying this makes the request an *update*: a new immutable version is
    #: appended and the existing one keeps serving until the new one activates.
    material_id: str | None = Field(default=None, max_length=36)

    @field_validator("sha256")
    @classmethod
    def _lowercase_hex(cls, value: str) -> str:
        if not SHA256_PATTERN.match(value):
            raise ValueError("sha256 must match ^[a-f0-9]{64}$ (lowercase hex)")
        return value

    @field_validator("filename")
    @classmethod
    def _no_path_separators(cls, value: str) -> str:
        """Reject a filename that is really a path.

        The key derivation ignores the filename entirely, so this cannot cause a
        traversal -- but a filename containing separators or NUL is still either a
        confused client or a probe, and echoing it back into audit and UI surfaces
        is not worth it.
        """
        if value.strip() != value or not value.strip():
            raise ValueError("filename must not be blank or padded")
        for forbidden in ("/", "\\", "\x00"):
            if forbidden in value:
                raise ValueError("filename must not contain path separators")
        return value


def _allowed_media_types(request: Request) -> tuple[str, ...]:
    configured = getattr(
        request.app.state.settings, "MATERIAL_ALLOWED_MEDIA_TYPES", None
    )
    return tuple(configured) if configured else DEFAULT_ALLOWED_MEDIA_TYPES


def _max_material_bytes(request: Request) -> int:
    return int(
        getattr(request.app.state.settings, "MATERIAL_MAX_BYTES", None)
        or DEFAULT_MAX_MATERIAL_BYTES
    )


def _material_saga(request: Request) -> MaterialSaga:
    """Build the saga over the app's engine, honouring a test/prod override.

    Suites inject ``app.state.material_saga`` (wired to live MinIO and Milvus).
    In a deployment one is built from settings and cached on ``app.state`` so a
    client is not created per request.
    """
    override = getattr(request.app.state, "material_saga", None)
    if override is not None:
        return override

    settings = request.app.state.settings
    factory = async_sessionmaker(request.app.state.async_engine, expire_on_commit=False)
    store = ObjectStore(ObjectStoreConfig.from_settings(settings))
    vectors = MilvusVectorStore(MilvusVectorStoreConfig.from_settings(settings))
    chunker = getattr(request.app.state, "material_chunker", None)
    if chunker is None:
        raise ApplicationError(
            "MATERIAL_PIPELINE_UNCONFIGURED",
            "material indexing is not configured on this deployment",
            status_code=503,
        )
    saga = MaterialSaga(
        factory=factory,
        object_store=store,
        indexer=VectorIndexer(factory=factory, vector_store=vectors),
        chunker=chunker,
    )
    request.app.state.material_saga = saga
    return saga


@router.post("/materials", status_code=201)
async def declare_material_upload(
    body: MaterialUploadRequest,
    principal: PrincipalDep,
    request: Request,
) -> Any:
    """Declare an upload and return a short-lived grant for exactly one object.

    ``413`` when the declared size exceeds the configured ceiling, ``415`` for a
    media type outside the allowlist, ``422`` for any shape violation (handled by
    the request model), ``403`` when the caller's purpose is not permitted, and
    ``201`` with the grant otherwise.
    """
    maximum = _max_material_bytes(request)
    if body.size_bytes > maximum:
        # 413 rather than 422: the request is well-formed, the payload is too big,
        # and the caller can act on the limit we report back. Raised through
        # ApplicationError rather than returned as a bare JSONResponse so it uses
        # the same {"error": {...}} envelope (and request-id correlation) as every
        # other failure on this API -- a route with its own error shape is a
        # client-side special case forever.
        raise ApplicationError(
            "MATERIAL_TOO_LARGE",
            "the declared material exceeds the maximum size",
            status_code=413,
            details={"max_size_bytes": maximum, "declared": body.size_bytes},
        )

    allowed = _allowed_media_types(request)
    if body.media_type not in allowed:
        raise ApplicationError(
            "MATERIAL_MEDIA_TYPE_UNSUPPORTED",
            "the declared media type is not accepted",
            status_code=415,
            details={"allowed_media_types": list(allowed)},
        )

    source_type, read_only = _PURPOSE_BINDING[body.purpose]
    if body.purpose == "policy_import" and not _may_import_policy(principal):
        # Importing a formal policy creates something that will later be cited as
        # authoritative, so it is a separate authority from uploading a task input.
        raise ApplicationError(
            "AUTH_FORBIDDEN",
            "importing a formal policy requires a policy-management grant",
            status_code=403,
        )

    saga = _material_saga(request)
    try:
        draft = await saga.begin_upload(
            tenant_id=principal.tenant_id,
            knowledge_base_id=body.knowledge_base_id,
            name=body.filename,
            source_type=source_type,
            media_type=body.media_type,
            size_bytes=body.size_bytes,
            sha256=body.sha256,
            created_by=principal.user_id,
            filename=body.filename,
            material_id=body.material_id,
            read_only=read_only,
        )
    except SagaStateError as exc:
        # Includes "another tenant's material": the saga returns the same error for
        # absent and foreign ids, so this cannot confirm that a material exists.
        raise ApplicationError(
            "RESOURCE_NOT_FOUND", str(exc), status_code=404
        ) from exc

    return {
        "material_id": draft.material_id,
        "material_version_id": draft.material_version_id,
        "version_number": draft.version_number,
        "purpose": body.purpose,
        "upload": {
            "method": draft.grant.method,
            "url": draft.grant.url,
            "expires_at": draft.grant.expires_at.isoformat(),
            "max_bytes": draft.grant.max_bytes,
            "media_type": draft.grant.media_type,
            # The bucket *alias* is returned, never the provider bucket or the
            # derived key: the client addresses the grant URL and nothing else.
            "bucket_alias": draft.grant.bucket_alias,
        },
    }


def _may_import_policy(principal: Any) -> bool:
    """Whether this principal may introduce a formal policy original.

    Checked against the principal's own scopes/roles rather than a request field.
    Stage 6 replaces this with the full approval-backed publish flow; what Stage 5
    owns is that the two purposes are not interchangeable.
    """
    scopes = {str(scope) for scope in getattr(principal, "scopes", ()) or ()}
    roles = {str(role) for role in getattr(principal, "roles", ()) or ()}
    return bool(
        {"policy:import", "policy:manage", "knowledge:manage"} & scopes
        or {"admin", "policy_admin", "knowledge_admin"} & roles
    )

