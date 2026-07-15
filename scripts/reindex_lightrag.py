"""Rebuild PolicyFlow documents into the in-process LightRAG workspaces."""
# ruff: noqa: E402

import argparse
import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.models import KnowledgeBase, KnowledgeDocument, utc_now
from backend.app.db.session import build_engine
from backend.app.rag.inprocess_lightrag import InProcessLightRAGAdapter
from backend.app.services.embedding_service import OpenAICompatibleEmbeddingService
from backend.app.services.llm_service import OpenAICompatibleLLMService


async def rebuild(
    database_url: str,
    demo_only: bool,
    *,
    kb_code: str | None = None,
    statuses: list[str] | None = None,
    limit: int | None = None,
) -> tuple[int, int]:
    settings = Settings(DATABASE_URL=database_url)
    engine = build_engine(database_url)
    adapter = InProcessLightRAGAdapter(
        engine,
        settings,
        OpenAICompatibleLLMService(engine, settings),
        OpenAICompatibleEmbeddingService(engine, settings),
    )
    with Session(engine) as session:
        statement = select(KnowledgeDocument)
        if demo_only:
            statement = statement.where(KnowledgeDocument.title.startswith("[DEMO]"))
        if statuses:
            statement = statement.where(KnowledgeDocument.index_status.in_(statuses))
        if kb_code:
            kb = session.exec(
                select(KnowledgeBase).where(KnowledgeBase.code == kb_code)
            ).first()
            if kb is None:
                raise SystemExit(f"Knowledge base not found: {kb_code}")
            statement = statement.where(KnowledgeDocument.knowledge_base_id == kb.id)
        statement = statement.order_by(KnowledgeDocument.created_at.desc())
        rows = list(session.exec(statement).all())
        if limit is not None:
            rows = rows[: max(limit, 0)]
        documents = [
            KnowledgeDocument.model_validate(item.model_dump()) for item in rows
        ]
        knowledge_bases = {
            item.id: KnowledgeBase.model_validate(item.model_dump())
            for item in session.exec(select(KnowledgeBase)).all()
        }

    succeeded = 0
    failed = 0
    try:
        for document in documents:
            knowledge_base = knowledge_bases.get(document.knowledge_base_id)
            if knowledge_base is None:
                continue
            with Session(engine) as session:
                stored = session.get(KnowledgeDocument, document.id)
                if stored is not None:
                    stored.index_status = "indexing"
                    stored.index_error = None
                    stored.updated_at = utc_now()
                    session.add(stored)
                    session.commit()
            try:
                print(f"Indexing {knowledge_base.code}: {document.title}", flush=True)
                await adapter.insert_document(knowledge_base, document)
            except Exception as exc:
                failed += 1
                with Session(engine) as session:
                    stored = session.get(KnowledgeDocument, document.id)
                    if stored is not None:
                        stored.index_status = "failed"
                        stored.index_error = str(exc)
                        stored.updated_at = utc_now()
                        session.add(stored)
                        session.commit()
                print(f"FAILED {document.title}: {exc}", flush=True)
                continue
            succeeded += 1
            with Session(engine) as session:
                stored = session.get(KnowledgeDocument, document.id)
                if stored is not None:
                    stored.index_status = "indexed"
                    stored.index_error = None
                    stored.updated_at = utc_now()
                    session.add(stored)
                    session.commit()
            print(f"INDEXED {document.title}", flush=True)
    finally:
        await adapter.close()
        engine.dispose()
    return succeeded, failed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="sqlite:///./policyflow.db")
    parser.add_argument("--demo-only", action="store_true")
    parser.add_argument(
        "--kb-code",
        default=None,
        help="Only reindex documents in this knowledge-base code (e.g. eval_test)",
    )
    parser.add_argument(
        "--status",
        action="append",
        dest="statuses",
        default=None,
        help="Only reindex documents with this index_status; repeatable (failed/pending)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max documents to process")
    args = parser.parse_args()
    succeeded, failed = asyncio.run(
        rebuild(
            args.database_url,
            args.demo_only,
            kb_code=args.kb_code,
            statuses=args.statuses,
            limit=args.limit,
        )
    )
    print({"succeeded": succeeded, "failed": failed})
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
