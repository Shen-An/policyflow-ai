"""Cleanup must only change and count the authenticated tenant's dataset."""

import pytest
from sqlmodel import Session

from backend.app.db.models import (
    Department, EvalCase, FAQDraft, KnowledgeBase, KnowledgeDocument, RagIndexJob,
    RetrievalEvalItem, Role, UserRole,
)
from backend.app.core.exceptions import ApplicationError
from backend.app.services.eval_service import cleanup_eval_dataset
from tests.contract.test_runs_api import (
    ROLE_ALPHA,
    TENANT_ALPHA,
    TENANT_BETA,
    USER_ALPHA,
    _alpha,
    client,
)


def test_cleanup_preserves_other_tenant_and_scopes_counts(client):
    with Session(client.app.state.engine) as session:
        role = session.get(Role, ROLE_ALPHA)
        role.code = "kb_admin"
        session.add(role)
        session.add(UserRole(user_id=USER_ALPHA, role_id=ROLE_ALPHA))
        a_case = EvalCase(tenant_id=TENANT_ALPHA, question="a", category="hr")
        b_case = EvalCase(tenant_id=TENANT_BETA, question="b", category="hr")
        a_item = RetrievalEvalItem(tenant_id=TENANT_ALPHA, query="stale a")
        b_item = RetrievalEvalItem(tenant_id=TENANT_BETA, query="stale b")
        session.add_all([a_case, b_case, a_item, b_item])
        session.commit()
        ids = [row.id for row in (a_case, b_case, a_item, b_item)]
    response = client.post("/api/eval/datasets/cleanup", headers=_alpha(), json={})
    assert response.status_code == 200, response.text
    assert response.json()["stale_items_deleted"] == 1
    assert response.json()["remaining_enabled_items"] == 0
    with Session(client.app.state.engine) as session:
        assert session.get(EvalCase, ids[0]).enabled is False
        assert session.get(EvalCase, ids[1]).enabled is True
        assert session.get(RetrievalEvalItem, ids[2]) is None
        assert session.get(RetrievalEvalItem, ids[3]).enabled is True


def test_cleanup_requires_tenant(client):
    with Session(client.app.state.engine) as session:
        with pytest.raises(ApplicationError):
            cleanup_eval_dataset(session, tenant_id="")


def test_purge_deleted_docs_keeps_foreign_files_and_json_references(client, tmp_path):
    with Session(client.app.state.engine) as session:
        docs = []
        for tenant in (TENANT_ALPHA, TENANT_BETA):
            department = Department(tenant_id=tenant, name=tenant, code=f"cleanup-{tenant}")
            session.add(department)
            session.flush()
            kb = KnowledgeBase(tenant_id=tenant, code=f"cleanup-{tenant}", name="测试库",
                               department_id=department.id, rag_workspace=str(tmp_path))
            session.add(kb)
            session.flush()
            path = tmp_path / f"{tenant}.txt"
            path.write_text("old policy", encoding="utf-8")
            doc = KnowledgeDocument(tenant_id=tenant, knowledge_base_id=kb.id,
                title=tenant, file_type="txt", file_path=str(path), content_hash=tenant,
                created_by=USER_ALPHA, index_status="deleted")
            session.add(doc)
            session.flush()
            session.add(RagIndexJob(knowledge_document_id=doc.id, job_type="insert"))
            docs.append(doc)
        # Malformed foreign JSON must not be rewritten by another tenant's purge.
        foreign = RetrievalEvalItem(tenant_id=TENANT_BETA, query="foreign",
            relevant_document_ids=[docs[0].id, docs[1].id])
        faq = FAQDraft(knowledge_base_id=docs[1].knowledge_base_id,
            source_document_id=docs[1].id, question="q", answer="a")
        session.add_all([foreign, faq])
        session.commit()
        ids, paths = [d.id for d in docs], [d.file_path for d in docs]
        foreign_id, faq_id = foreign.id, faq.id
        result = cleanup_eval_dataset(session, tenant_id=TENANT_ALPHA)
        assert result.deleted_documents_purged == 1
        assert session.get(KnowledgeDocument, ids[0]) is None
        assert session.get(KnowledgeDocument, ids[1]) is not None
        assert session.get(RetrievalEvalItem, foreign_id).relevant_document_ids == ids
        assert session.get(FAQDraft, faq_id).source_document_id == ids[1]
    from pathlib import Path
    assert not Path(paths[0]).exists()
    assert Path(paths[1]).exists()
