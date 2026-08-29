"""End-to-end seeding contract for the 40 refusal negatives.

Unit coverage lives in tests/test_eval_negatives.py; this file walks the real
route so the marker, idempotency and cleanup-exemption seams are checked on
actual DB rows instead of hand-built objects.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.models import EvalCase, KnowledgeBase, KnowledgeDocument, RetrievalEvalItem
from backend.app.evals.negatives import NEGATIVE_KINDS, is_negative_judgement, negative_kind
from backend.app.main import create_app
from backend.app.schemas.retrieval import Evidence, RetrievalRequest
from backend.app.services.enterprise_eval_dataset import (
    ENTERPRISE_EVAL_KB_CODE,
    POLICY_NEGATIVES,
)


class SeedLightRAG:
    name = "lightrag"

    def __init__(self) -> None:
        self.inserted: list[str] = []

    @property
    def available(self) -> bool:
        return True

    async def insert_document(
        self,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
    ) -> None:
        self.inserted.append(document.id)

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        return []


class SeedLLM:
    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        return "unused"


def build_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'seed.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(settings, lightrag_adapter=SeedLightRAG(), llm_service=SeedLLM())


def login(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _negative_items(session: Session) -> list[RetrievalEvalItem]:
    return [
        item
        for item in session.exec(select(RetrievalEvalItem)).all()
        if is_negative_judgement(item.relevance_judgement)
    ]


def test_enterprise_seed_creates_marked_negatives_once_and_cleanup_keeps_them(
    tmp_path: Path,
) -> None:
    app = build_app(tmp_path)

    with TestClient(app) as client:
        headers = login(client)

        first = client.post("/api/eval/datasets/enterprise-seed", headers=headers)
        assert first.status_code == 201
        first_body = first.json()
        assert first_body["negative_items_created"] == len(POLICY_NEGATIVES) == 40
        assert first_body["negative_cases_created"] == 40
        assert first_body["negative_count"] == 40

        # Re-seeding is a no-op: the suite is looked up by question/query, not appended.
        second = client.post("/api/eval/datasets/enterprise-seed", headers=headers)
        assert second.status_code == 201
        second_body = second.json()
        assert second_body["negative_items_created"] == 0
        assert second_body["negative_cases_created"] == 0
        assert second_body["negative_count"] == 40

        with Session(app.state.engine) as session:
            items = _negative_items(session)
            assert len(items) == 40
            kinds = [negative_kind(item.relevance_judgement) for item in items]
            assert set(kinds) == set(NEGATIVE_KINDS)
            assert kinds.count("off_topic") == 20
            assert kinds.count("near_miss") == 20
            for item in items:
                judgement = item.relevance_judgement or {}
                # No gold: that is the whole point, and why they cannot enter Hit@K.
                assert item.relevant_document_ids == []
                assert item.relevant_chunk_ids == []
                assert judgement["gold_doc_count"] == 0
                assert judgement["expected_behavior"] == "refuse"
                assert item.enabled is True
                assert item.eval_case_id is not None

            queries = {item.query for item in items}
            assert queries == {negative.query for negative in POLICY_NEGATIVES}

            cases = session.exec(
                select(EvalCase).where(EvalCase.category == ENTERPRISE_EVAL_KB_CODE)
            ).all()
            negative_cases = [case for case in cases if case.question in queries]
            assert len(negative_cases) == 40
            assert all(case.should_answer is False for case in negative_cases)
            assert all(case.expected_source_documents == [] for case in negative_cases)

        # Cleanup deletes stale gold; gold-less negatives must survive it untouched.
        cleanup = client.post(
            "/api/eval/datasets/cleanup",
            headers=headers,
            json={
                "delete_stale_items": True,
                "disable_non_eval_test_items": True,
                "purge_deleted_documents": True,
                "knowledge_base_code": "eval_test",
            },
        )
        assert cleanup.status_code == 200

        with Session(app.state.engine) as session:
            survivors = _negative_items(session)
            assert len(survivors) == 40
            assert all(item.enabled for item in survivors)
            surviving_cases = session.exec(
                select(EvalCase).where(EvalCase.category == ENTERPRISE_EVAL_KB_CODE)
            ).all()
            assert all(case.enabled for case in surviving_cases if case.question in queries)
