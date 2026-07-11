from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "frontend" / ".tmp" / "e2e-backend"
shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{(RUNTIME_DIR / 'policyflow-e2e.db').as_posix()}"
os.environ["LOG_DIR"] = str(RUNTIME_DIR / "logs")
os.environ["UPLOAD_DIR"] = str(RUNTIME_DIR / "uploads")
os.environ["RAG_WORKSPACE_DIR"] = str(RUNTIME_DIR / "rag-workspaces")
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "frontend-e2e-only"
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import Settings  # noqa: E402
from backend.app.db.models import KnowledgeBase, KnowledgeDocument  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from backend.app.schemas.retrieval import Evidence, RetrievalRequest  # noqa: E402


class SuccessfulIndexBackend:
    name = "e2e-indexer"

    @property
    def available(self) -> bool:
        return True

    async def insert_document(
        self,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
    ) -> None:
        return None

    async def retrieve(
        self,
        request: RetrievalRequest,
        limit: int,
    ) -> list[Evidence]:
        if "unknown" in request.query.lower():
            return []
        return [
            Evidence(
                knowledge_base_id=request.knowledge_base_ids[0],
                knowledge_base_name="HR",
                document_id="frontend-e2e-document",
                document_title="Travel Policy",
                chunk_id="frontend-e2e-chunk",
                snippet="Travel requests require manager approval.",
                score=0.91,
                retriever_type="e2e-indexer",
                rank=1,
            )
        ]


class DeterministicLLM:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "Travel requests require manager approval [1]."

settings = Settings(_env_file=None)

if __name__ == "__main__":
    uvicorn.run(
        create_app(
            settings,
            lightrag_adapter=SuccessfulIndexBackend(),
            llm_service=DeterministicLLM(),
        ),
        host="127.0.0.1",
        port=8000,
        log_level="warning",
    )
