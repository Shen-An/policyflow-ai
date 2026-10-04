"""T081 [US1] Milvus access: one shared collection, filtered before every search.

Design rules this module exists to enforce:

* **One shared collection, partitioned by tenant.** A collection per tenant does
  not scale (Milvus holds metadata and loaded segments per collection) and makes
  cross-tenant queries an accident waiting to happen. The tenant field is
  declared as Milvus's ``is_partition_key``, so Milvus itself routes and prunes
  by tenant.
* **The pre-ANN filter is built here, never passed in.** :meth:`search` takes a
  :class:`RetrievalScope`, not an expression, so no caller can forget the tenant
  term. All five mandatory fields are rendered from the scope, and a scope with
  an empty narrowing term refuses to render at all.
* **``retrievable`` is a projection, and the filter requires both sides.**
  PostgreSQL's ``VectorManifest`` is the authority; the per-row flag in Milvus is
  a cached copy. A search requires the row flag *and* a version id drawn from the
  authoritative manifest set, so a stale projection can only shrink the result
  set. Drift hides rows (a recoverable reconciliation finding) and can never
  expose the wrong version (a correctness hole).
* **Unavailability is typed and fails closed.** Every fault becomes
  :class:`RetrievalUnavailable`, which carries the ``RETRIEVAL_UNAVAILABLE``
  contract code (503, retryable). It is never an empty result: "no rows" and "the
  vector store is down" must not be the same observation, or an outage silently
  becomes a confident wrong answer.
* **Reported strategy metadata is read from Milvus.** :meth:`index_metadata`
  describes the real index and metric, and the strategy name embeds both, so a
  report cannot claim HNSW while the collection runs a flat index.

``pymilvus`` is synchronous, so every call is dispatched through
``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from backend.app.observability.errors import ContractError, ErrorCode

#: The five terms that must narrow every query *before* the ANN comparison runs
#: (``data-model.md``: "A query must filter tenant + allowed KB + active immutable
#: version + ``retrievable=true`` before ANN search"). Declared as data so the
#: contract test can assert it, and so a reviewer can see the whole requirement
#: in one place.
MANDATORY_PRE_ANN_FILTER_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "knowledge_base_id",
    "version_id",
    "embedding_version_id",
    "retrievable",
)

#: Subject kinds a row may describe. A material version is Stage-5 material
#: storage; a document is the legacy knowledge-document path kept during
#: migration.
SUBJECT_KINDS: frozenset[str] = frozenset({"material", "document"})

#: Number of logical partitions Milvus spreads the partition-key values over.
#: Not the tenant count: Milvus hashes the key into this many partitions, so it
#: bounds metadata growth while still pruning most tenants' data from a scan.
DEFAULT_PARTITION_COUNT = 16

#: Milvus read consistency. ``Strong`` rather than the default ``Bounded``
#: because Stage 5's whole promise is that a query sees the *current* active
#: version: bounded staleness would let a search answer from the version that was
#: authoritative a few seconds ago, which is precisely the stale retrieval the
#: Checkpoint forbids. It also makes ``verify`` meaningful -- counting chunks under
#: bounded staleness could pass a manifest whose rows had not landed yet. The cost
#: is a sync with the query coordinator per read, which is the right trade for an
#: evidence-bearing lookup.
_CONSISTENCY_LEVEL = "Strong"

_ID_FIELD = "id"
_VECTOR_FIELD = "vector"


class RetrievalUnavailable(ContractError):  # noqa: N818 - named for the contract code
    """The vector store could not serve a query or a write.

    Carries the ``RETRIEVAL_UNAVAILABLE`` contract code so the registered error
    handlers return 503 with ``Retry-After`` automatically, and so callers can
    fail closed on the *type* rather than parsing a message.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(
            ErrorCode.RETRIEVAL_UNAVAILABLE,
            details={"reason": detail},
        )


class MilvusVectorStoreConfig(BaseModel):
    """Connection and collection settings for one Milvus deployment."""

    model_config = ConfigDict(frozen=True)

    uri: str
    token: str | None = None
    tls_enabled: bool = True
    database: str = "default"
    collection: str = "policyflow_chunks"
    tenant_partition_key: str = "tenant_id"
    request_timeout_seconds: float = 30.0
    partition_count: int = DEFAULT_PARTITION_COUNT
    index_type: str = "HNSW"
    metric_type: str = "COSINE"

    @classmethod
    def from_settings(cls, settings: Any) -> MilvusVectorStoreConfig:
        """Build a config from application ``Settings``."""
        token = getattr(settings, "MILVUS_TOKEN", None)
        if token is not None:
            unwrap = getattr(token, "get_secret_value", None)
            token = unwrap() if callable(unwrap) else str(token)
        return cls(
            uri=settings.MILVUS_URI,
            token=token,
            tls_enabled=settings.MILVUS_TLS_ENABLED,
            database=settings.MILVUS_DATABASE,
            collection=settings.MILVUS_COLLECTION,
            tenant_partition_key=settings.MILVUS_TENANT_PARTITION_KEY,
            request_timeout_seconds=settings.MILVUS_REQUEST_TIMEOUT_SECONDS,
        )


@dataclass(frozen=True)
class RetrievalScope:
    """The mandatory narrowing of one search.

    Every field is required to be non-empty. An "optional" term would be the one
    a caller forgets, so :meth:`filter_expression` raises rather than quietly
    widening the search to another tenant's vectors.
    """

    tenant_id: str
    knowledge_base_ids: tuple[str, ...]
    embedding_version_id: str
    version_ids: tuple[str, ...]

    def filter_expression(self) -> str:
        """Render the pre-ANN filter, refusing to render an unnarrowed one."""
        if not self.tenant_id:
            raise ValueError("a retrieval scope requires a tenant_id")
        if not self.knowledge_base_ids:
            raise ValueError(
                "a retrieval scope requires at least one allowed knowledge base; "
                "an empty list would mean 'any knowledge base'"
            )
        if not self.embedding_version_id:
            raise ValueError("a retrieval scope requires an embedding_version_id")
        if not self.version_ids:
            raise ValueError(
                "a retrieval scope requires at least one active immutable version; "
                "an empty list would mean 'any version', including superseded ones"
            )
        terms = (
            f'tenant_id == "{_quote(self.tenant_id)}"',
            f"knowledge_base_id in {_literal_list(self.knowledge_base_ids)}",
            f"version_id in {_literal_list(self.version_ids)}",
            f'embedding_version_id == "{_quote(self.embedding_version_id)}"',
            "retrievable == true",
        )
        return " and ".join(terms)


@dataclass(frozen=True)
class VectorHit:
    """One neighbour returned by a filtered search."""

    vector_id: str
    tenant_id: str
    knowledge_base_id: str
    subject_kind: str
    subject_id: str
    version_id: str
    chunk_id: str
    embedding_version_id: str
    text: str
    score: float


@dataclass(frozen=True)
class IndexMetadata:
    """What Milvus actually reports about the collection serving queries."""

    collection: str
    database: str
    index_type: str
    metric_type: str
    dimensions: int
    tenant_partition_key: str
    row_count: int

    def strategy_name(self) -> str:
        """An honest strategy label that embeds the real index and metric.

        Used in evaluation reports and evidence records. Composing it from
        ``describe_index`` output means a report cannot overstate the index: if the
        collection falls back to a flat index, the name says so.
        """
        return f"milvus/{self.index_type}/{self.metric_type}"


class MilvusVectorStore:
    """Async facade over one shared, tenant-partitioned Milvus collection."""

    def __init__(self, config: MilvusVectorStoreConfig) -> None:
        self._config = config
        self._client: Any | None = None

    @property
    def config(self) -> MilvusVectorStoreConfig:
        return self._config

    # -- client ---------------------------------------------------------------

    def _client_sync(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient

            if self._config.tls_enabled and not self._config.uri.startswith("https://"):
                raise RetrievalUnavailable(
                    "TLS is required but the configured vector-store URI is not https"
                )
            self._client = MilvusClient(
                uri=self._config.uri,
                token=self._config.token or "",
                db_name=self._config.database,
                timeout=self._config.request_timeout_seconds,
            )
        return self._client

    async def _call(self, operation: str, *args: Any, **kwargs: Any) -> Any:
        """Run one pymilvus operation off the event loop, failing closed on faults."""

        def invoke() -> Any:
            return getattr(self._client_sync(), operation)(*args, **kwargs)

        try:
            return await asyncio.to_thread(invoke)
        except RetrievalUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - every fault fails closed
            raise RetrievalUnavailable(
                f"the vector store could not complete {operation} "
                f"({type(exc).__name__})"
            ) from exc

    async def close(self) -> None:
        """Release the client. Safe to call more than once."""
        client, self._client = self._client, None
        if client is not None:
            try:
                await asyncio.to_thread(client.close)
            except Exception:  # noqa: BLE001 - closing must never mask a result
                pass

    # -- schema ---------------------------------------------------------------

    async def ensure_collection(self, *, dimensions: int) -> None:
        """Create the shared collection and its index when absent.

        Idempotent so every process can call it at startup. The tenant field is
        declared ``is_partition_key`` so Milvus prunes by tenant during a scan;
        the ``retrievable`` flag is a projection of the PostgreSQL manifest.
        """
        from pymilvus import DataType

        if await self._call("has_collection", collection_name=self._config.collection):
            return

        def build() -> None:
            client = self._client_sync()
            schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field(
                _ID_FIELD, DataType.VARCHAR, is_primary=True, max_length=256
            )
            schema.add_field(
                self._config.tenant_partition_key,
                DataType.VARCHAR,
                max_length=36,
                is_partition_key=True,
            )
            schema.add_field("knowledge_base_id", DataType.VARCHAR, max_length=36)
            schema.add_field("subject_kind", DataType.VARCHAR, max_length=16)
            schema.add_field("subject_id", DataType.VARCHAR, max_length=36)
            schema.add_field("version_id", DataType.VARCHAR, max_length=36)
            schema.add_field("chunk_id", DataType.VARCHAR, max_length=128)
            schema.add_field("embedding_version_id", DataType.VARCHAR, max_length=36)
            schema.add_field("retrievable", DataType.BOOL)
            schema.add_field("text", DataType.VARCHAR, max_length=65535)
            schema.add_field(_VECTOR_FIELD, DataType.FLOAT_VECTOR, dim=dimensions)

            index_params = client.prepare_index_params()
            index_params.add_index(
                field_name=_VECTOR_FIELD,
                index_type=self._config.index_type,
                metric_type=self._config.metric_type,
            )
            client.create_collection(
                collection_name=self._config.collection,
                schema=schema,
                index_params=index_params,
                num_partitions=self._config.partition_count,
                consistency_level=_CONSISTENCY_LEVEL,
            )
            client.load_collection(collection_name=self._config.collection)

        try:
            await asyncio.to_thread(build)
        except RetrievalUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise RetrievalUnavailable(
                f"the vector store could not create its collection ({type(exc).__name__})"
            ) from exc

    async def drop_collection(self) -> None:
        """Drop the collection. Used by suites; never by request handling."""
        await self._call("drop_collection", collection_name=self._config.collection)

    async def describe(self) -> dict[str, Any]:
        """Return Milvus's own description of the collection."""
        return await self._call(
            "describe_collection", collection_name=self._config.collection
        )

    async def index_metadata(self) -> IndexMetadata:
        """Report the real index, metric, dimension and row count."""
        description = await self.describe()
        fields = {field["name"]: field for field in description["fields"]}
        vector_field = fields[_VECTOR_FIELD]
        dimensions = int(
            vector_field.get("params", {}).get("dim") or vector_field.get("dim") or 0
        )
        indexes = await self._call("list_indexes", collection_name=self._config.collection)
        index_type = ""
        metric_type = ""
        if indexes:
            detail = await self._call(
                "describe_index",
                collection_name=self._config.collection,
                index_name=indexes[0],
            )
            index_type = str(detail.get("index_type") or detail.get("indexType") or "")
            metric_type = str(detail.get("metric_type") or detail.get("metricType") or "")
        stats = await self._call(
            "get_collection_stats", collection_name=self._config.collection
        )
        return IndexMetadata(
            collection=self._config.collection,
            database=self._config.database,
            index_type=index_type,
            metric_type=metric_type,
            dimensions=dimensions,
            tenant_partition_key=self._config.tenant_partition_key,
            row_count=int(stats.get("row_count", 0)),
        )

    # -- writes ---------------------------------------------------------------

    async def upsert_chunks(
        self,
        *,
        tenant_id: str,
        knowledge_base_id: str,
        subject_kind: str,
        subject_id: str,
        version_id: str,
        embedding_version_id: str,
        vector_id_prefix: str,
        chunks: Any,
        retrievable: bool,
    ) -> int:
        """Upsert one version's chunks under deterministic primary keys.

        ``upsert`` (not ``insert``) because the ids are deterministic: re-indexing
        the same immutable version must overwrite its rows rather than create a
        second copy that would double-count in the manifest and double-serve in
        search results.
        """
        if subject_kind not in SUBJECT_KINDS:
            raise ValueError(f"unknown subject_kind {subject_kind!r}")
        rows = [
            {
                _ID_FIELD: vector_id(vector_id_prefix, chunk.chunk_id),
                self._config.tenant_partition_key: tenant_id,
                "knowledge_base_id": knowledge_base_id,
                "subject_kind": subject_kind,
                "subject_id": subject_id,
                "version_id": version_id,
                "chunk_id": chunk.chunk_id,
                "embedding_version_id": embedding_version_id,
                "retrievable": retrievable,
                "text": chunk.text,
                _VECTOR_FIELD: list(chunk.vector),
            }
            for chunk in chunks
        ]
        if not rows:
            return 0
        await self._call(
            "upsert", collection_name=self._config.collection, data=rows
        )
        return len(rows)

    async def set_retrievable(
        self, *, tenant_id: str, vector_id_prefix: str, retrievable: bool
    ) -> int:
        """Flip the ``retrievable`` projection for one manifest's rows.

        PostgreSQL remains the authority; this only refreshes the cached copy. The
        rows are read back and re-upserted because Milvus has no partial update:
        the vector has to be carried along. That is acceptable because the row
        count is one material version's chunks, and because the filter requires
        both sides -- so even if this step fails, the search result stays correct.
        """
        rows = await self._query_prefix(
            tenant_id=tenant_id, vector_id_prefix=vector_id_prefix, include_vector=True
        )
        if not rows:
            return 0
        for row in rows:
            row["retrievable"] = retrievable
        await self._call("upsert", collection_name=self._config.collection, data=rows)
        return len(rows)

    async def delete_by_prefix(self, *, tenant_id: str, vector_id_prefix: str) -> int:
        """Delete every row of one manifest. Idempotent.

        Deletion is by explicit primary key rather than by filter so the blast
        radius is exactly the rows that were listed, even if a concurrent write
        added more.
        """
        rows = await self._query_prefix(
            tenant_id=tenant_id, vector_id_prefix=vector_id_prefix, include_vector=False
        )
        if not rows:
            return 0
        return await self.delete_ids(
            tenant_id=tenant_id, vector_ids=[str(row[_ID_FIELD]) for row in rows]
        )

    async def delete_ids(self, *, tenant_id: str, vector_ids: Sequence[str]) -> int:
        """Delete specific rows by primary key.

        Used by reconciliation repairs, which must be able to remove an exact set
        of rows rather than everything sharing a prefix. ``tenant_id`` is accepted
        and asserted against the ids' own prefix so a caller cannot delete across
        tenants by handing over a foreign id list.
        """
        ids = [str(value) for value in vector_ids]
        if not ids:
            return 0
        owned = await self._query_ids(tenant_id=tenant_id, vector_ids=ids)
        if len(owned) != len(set(ids)):
            raise ValueError(
                "refusing to delete vector ids this tenant does not own "
                f"({len(owned)} of {len(set(ids))} resolved)"
            )
        await self._call("delete", collection_name=self._config.collection, ids=ids)
        return len(ids)

    async def _query_ids(
        self, *, tenant_id: str, vector_ids: Sequence[str]
    ) -> list[str]:
        """Return which of ``vector_ids`` exist and belong to ``tenant_id``."""
        literals = _literal_list(tuple(str(value) for value in vector_ids))
        rows = await self._call(
            "query",
            collection_name=self._config.collection,
            filter=(
                f'{self._config.tenant_partition_key} == "{_quote(tenant_id)}" and '
                f"{_ID_FIELD} in {literals}"
            ),
            output_fields=[_ID_FIELD],
            limit=16_384,
            consistency_level=_CONSISTENCY_LEVEL,
        )
        return [str(row[_ID_FIELD]) for row in rows or []]

    # -- reads ----------------------------------------------------------------

    async def search(
        self, *, scope: RetrievalScope, query_vector: list[float], limit: int
    ) -> list[VectorHit]:
        """Filtered ANN search. The filter is rendered from ``scope``.

        There is deliberately no parameter for a raw expression: the only way to
        narrow a search is through a scope, and a scope cannot be unnarrowed.
        """
        expression = scope.filter_expression()
        response = await self._call(
            "search",
            collection_name=self._config.collection,
            data=[list(query_vector)],
            filter=expression,
            limit=limit,
            consistency_level=_CONSISTENCY_LEVEL,
            output_fields=[
                self._config.tenant_partition_key,
                "knowledge_base_id",
                "subject_kind",
                "subject_id",
                "version_id",
                "chunk_id",
                "embedding_version_id",
                "text",
            ],
        )
        hits: list[VectorHit] = []
        for group in response or []:
            for hit in group:
                entity = hit.get("entity", hit)
                hits.append(
                    VectorHit(
                        vector_id=str(hit.get(_ID_FIELD, "")),
                        tenant_id=str(entity.get(self._config.tenant_partition_key, "")),
                        knowledge_base_id=str(entity.get("knowledge_base_id", "")),
                        subject_kind=str(entity.get("subject_kind", "")),
                        subject_id=str(entity.get("subject_id", "")),
                        version_id=str(entity.get("version_id", "")),
                        chunk_id=str(entity.get("chunk_id", "")),
                        embedding_version_id=str(
                            entity.get("embedding_version_id", "")
                        ),
                        text=str(entity.get("text", "")),
                        score=float(hit.get("distance", 0.0)),
                    )
                )
        return hits

    async def count_by_prefix(self, *, tenant_id: str, vector_id_prefix: str) -> int:
        """Number of rows Milvus holds for one manifest."""
        rows = await self._query_prefix(
            tenant_id=tenant_id, vector_id_prefix=vector_id_prefix, include_vector=False
        )
        return len(rows)

    async def chunk_ids_for_prefix(
        self, *, tenant_id: str, vector_id_prefix: str
    ) -> tuple[str, ...]:
        """Chunk ids Milvus actually holds, for ``missing_chunk`` reconciliation."""
        rows = await self._query_prefix(
            tenant_id=tenant_id, vector_id_prefix=vector_id_prefix, include_vector=False
        )
        return tuple(sorted(str(row["chunk_id"]) for row in rows))

    async def list_version_ids(self, *, tenant_id: str) -> tuple[str, ...]:
        """Distinct version ids present for one tenant, for orphan detection."""
        rows = await self._call(
            "query",
            collection_name=self._config.collection,
            filter=f'{self._config.tenant_partition_key} == "{_quote(tenant_id)}"',
            output_fields=["version_id"],
            limit=16_384,
            consistency_level=_CONSISTENCY_LEVEL,
        )
        return tuple(sorted({str(row["version_id"]) for row in rows or []}))

    async def _query_prefix(
        self, *, tenant_id: str, vector_id_prefix: str, include_vector: bool
    ) -> list[dict[str, Any]]:
        """Fetch one manifest's rows.

        The tenant term is included even though the prefix already encodes it:
        defence in depth costs nothing here, and it keeps every single query in
        this module tenant-scoped by construction.
        """
        fields = [
            _ID_FIELD,
            self._config.tenant_partition_key,
            "knowledge_base_id",
            "subject_kind",
            "subject_id",
            "version_id",
            "chunk_id",
            "embedding_version_id",
            "retrievable",
            "text",
        ]
        if include_vector:
            fields.append(_VECTOR_FIELD)
        expression = (
            f'{self._config.tenant_partition_key} == "{_quote(tenant_id)}" and '
            f'{_ID_FIELD} like "{_quote(vector_id_prefix)}:%"'
        )
        rows = await self._call(
            "query",
            collection_name=self._config.collection,
            filter=expression,
            output_fields=fields,
            limit=16_384,
            consistency_level=_CONSISTENCY_LEVEL,
        )
        return [dict(row) for row in rows or []]


def vector_id(vector_id_prefix: str, chunk_id: str) -> str:
    """Deterministic primary key for one chunk of one immutable version.

    Deterministic rather than random so re-indexing is an overwrite: the same
    (version, chunk) always maps to the same row, which is what makes staging
    idempotent under retry and makes orphan detection possible by prefix.
    """
    return f"{vector_id_prefix}:{chunk_id}"


def _quote(value: str) -> str:
    """Escape a value for a Milvus filter string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _literal_list(values: tuple[str, ...]) -> str:
    inner = ", ".join(f'"{_quote(value)}"' for value in values)
    return f"[{inner}]"
