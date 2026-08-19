"""Phase 1 knowledge-base, document, ACL, and audit tests."""

from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.core.config import Settings
from backend.app.db.models import (
    AuditLog,
    KnowledgeBase,
    KnowledgeDocument,
    RagIndexJob,
)
from backend.app.main import create_app
from backend.app.rag.document_loader import load_document_text
from backend.app.schemas.retrieval import Evidence, RetrievalRequest


class SuccessfulIndexBackend:
    name = "lightrag"

    def __init__(self) -> None:
        self.indexed_documents: list[KnowledgeDocument] = []

    @property
    def available(self) -> bool:
        return True

    async def insert_document(
        self,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
    ) -> None:
        self.indexed_documents.append(
            KnowledgeDocument.model_validate(document.model_dump())
        )

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        return []


def build_knowledge_app(tmp_path: Path) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'knowledge-test.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag-workspaces",
        SECRET_KEY="test-secret-key",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    return create_app(settings, lightrag_adapter=SuccessfulIndexBackend())


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_docx(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_pdf(text: str) -> bytes:
    escaped_text = text
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped_text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >> stream" + bytes([10]) + stream + bytes([10]) + b"endstream",
    ]
    output = bytearray(b"%PDF-1.4" + bytes([10]))
    offsets = [0]
    for index, object_body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj".encode() + bytes([10]))
        output.extend(object_body + bytes([10]) + b"endobj" + bytes([10]))
    xref_offset = len(output)
    output.extend(f"xref{chr(10)}0 {len(objects) + 1}{chr(10)}".encode())
    output.extend(b"0000000000 65535 f " + bytes([10]))
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n {chr(10)}".encode())
    output.extend(
        f"trailer{chr(10)}<< /Size {len(objects) + 1} /Root 1 0 R >>{chr(10)}startxref{chr(10)}{xref_offset}{chr(10)}%%EOF{chr(10)}".encode()
    )
    return bytes(output)


def create_employee(client: TestClient, app: FastAPI, admin_headers: dict[str, str]) -> str:
    departments = client.get("/api/departments", headers=admin_headers).json()["items"]
    hr_department = next(item for item in departments if item["code"] == "hr")
    response = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "username": "employee1",
            "email": "employee1@example.com",
            "display_name": "Employee One",
            "password": "employee-password",
            "department_id": hr_department["id"],
            "role_codes": ["employee"],
        },
    )
    assert response.status_code == 201
    return login(client, "employee1", "employee-password")


def test_document_loader_extracts_all_supported_formats() -> None:
    assert load_document_text(b"Plain policy", "txt") == "Plain policy"
    assert load_document_text(b"# Markdown policy", "md") == "# Markdown policy"
    assert "DOCX policy" in load_document_text(make_docx("DOCX policy"), "docx")
    assert "PDF policy" in load_document_text(make_pdf("PDF policy"), "pdf")


def test_knowledge_acl_upload_index_and_audit_flow(tmp_path: Path) -> None:
    app = build_knowledge_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = headers(login(client, "admin", "test-password"))
        employee_headers = headers(create_employee(client, app, admin_headers))

        employee_list = client.get("/api/knowledge-bases", headers=employee_headers)
        hr_knowledge_base = employee_list.json()["items"][0]
        hr_id = hr_knowledge_base["id"]

        create_options = client.get(
            "/api/knowledge-bases/create-options",
            headers=admin_headers,
        ).json()
        hr_department = next(
            item for item in create_options["departments"] if item["code"] == "hr"
        )
        create_kb_response = client.post(
            "/api/knowledge-bases",
            headers=admin_headers,
            json={
                "name": "HR Process Library",
                "code": "hr-process",
                "department_id": hr_department["id"],
                "description": "HR process documents",
                "default_query_mode": "hybrid",
            },
        )

        denied_upload = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=employee_headers,
            files={"file": ("denied.txt", b"Denied upload", "text/plain")},
        )

        uploads: list[tuple[str, bytes, str, str]] = [
            ("txt-policy.txt", b"TXT policy content", "text/plain", "TXT policy content"),
            (
                "markdown-policy.md",
                b"# Markdown policy content",
                "text/markdown",
                "Markdown policy content",
            ),
            (
                "docx-policy.docx",
                make_docx("DOCX policy content"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "DOCX policy content",
            ),
            (
                "pdf-policy.pdf",
                make_pdf("PDF policy content"),
                "application/pdf",
                "PDF policy content",
            ),
        ]
        document_ids: list[str] = []
        for filename, content, media_type, _ in uploads:
            response = client.post(
                f"/api/knowledge-bases/{hr_id}/documents",
                headers=admin_headers,
                files={"file": (filename, content, media_type)},
            )
            assert response.status_code == 201
            assert response.json()["index_status"] == "pending"
            document_ids.append(str(response.json()["document_id"]))

        document_list = client.get(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=employee_headers,
        )
        status_response = client.get(
            f"/api/documents/{document_ids[0]}/status",
            headers=employee_headers,
        )
        denied_index = client.post(
            f"/api/documents/{document_ids[0]}/index",
            headers=employee_headers,
        )
        index_response = client.post(
            f"/api/documents/{document_ids[0]}/index",
            headers=admin_headers,
        )
        unsupported_response = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=admin_headers,
            files={"file": ("policy.exe", b"binary", "application/octet-stream")},
        )

    assert employee_list.status_code == 200
    assert employee_list.json()["total"] == 1
    assert hr_knowledge_base["code"] == "hr"
    assert hr_knowledge_base["permission"] == "read"
    assert create_kb_response.status_code == 201
    assert Path(create_kb_response.json()["rag_workspace"]).exists()
    assert denied_upload.status_code == 403
    assert denied_upload.json()["error"]["code"] == "KB_ACCESS_DENIED"
    assert document_list.status_code == 200
    assert document_list.json()["total"] == 4
    assert status_response.status_code == 200
    assert status_response.json()["index_status"] == "indexed"
    assert status_response.json()["latest_job"]["status"] == "success"
    assert denied_index.status_code == 403
    assert index_response.status_code == 200
    assert unsupported_response.status_code == 415
    assert unsupported_response.json()["error"]["code"] == "DOCUMENT_TYPE_NOT_SUPPORTED"

    with Session(app.state.engine) as session:
        documents = session.exec(select(KnowledgeDocument)).all()
        jobs = session.exec(select(RagIndexJob)).all()
        audits = session.exec(select(AuditLog)).all()
        extracted_by_type: dict[str, str] = {
            document.file_type: document.content_text or "" for document in documents
        }

    assert len(documents) == 4
    assert len(jobs) == 5
    assert all(Path(document.file_path).exists() for document in documents)
    for _, _, _, expected_text in uploads:
        assert any(expected_text in extracted for extracted in extracted_by_type.values())
    assert {audit.action for audit in audits} >= {
        "knowledge_base.create",
        "document.upload",
        "document.index_requested",
    }


def test_same_name_changed_hash_replaces_document_and_reindexes(tmp_path: Path) -> None:
    app = build_knowledge_app(tmp_path)

    with TestClient(app) as client:
        admin_headers = headers(login(client, "admin", "test-password"))
        knowledge_bases = client.get(
            "/api/knowledge-bases", headers=admin_headers
        ).json()["items"]
        hr_id = next(item["id"] for item in knowledge_bases if item["code"] == "hr")

        first_response = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=admin_headers,
            files={"file": ("leave-policy.txt", b"Old leave policy", "text/plain")},
        )
        assert first_response.status_code == 201
        document_id = first_response.json()["document_id"]

        with Session(app.state.engine) as session:
            first_document = session.get(KnowledgeDocument, document_id)
            assert first_document is not None
            first_file_path = Path(first_document.file_path)
            assert first_document.source_version == 1

        second_response = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=admin_headers,
            files={"file": ("leave-policy.txt", b"New leave policy", "text/plain")},
        )
        assert second_response.status_code == 201
        assert second_response.json()["document_id"] == document_id

        duplicate_response = client.post(
            f"/api/knowledge-bases/{hr_id}/documents",
            headers=admin_headers,
            files={"file": ("leave-policy.txt", b"New leave policy", "text/plain")},
        )

    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["error"]["code"] == "DOCUMENT_DUPLICATE"

    with Session(app.state.engine) as session:
        documents = session.exec(
            select(KnowledgeDocument).where(
                KnowledgeDocument.knowledge_base_id == hr_id,
                KnowledgeDocument.title == "leave-policy",
            )
        ).all()
        jobs = session.exec(
            select(RagIndexJob).where(
                RagIndexJob.knowledge_document_id == document_id
            )
        ).all()
        audits = session.exec(
            select(AuditLog).where(AuditLog.target_id == document_id)
        ).all()

    assert len(documents) == 1
    updated_document = documents[0]
    assert updated_document.id == document_id
    assert updated_document.content_text == "New leave policy"
    assert updated_document.source_version == 2
    assert updated_document.index_status == "indexed"
    assert Path(updated_document.file_path).exists()
    assert Path(updated_document.file_path) != first_file_path
    assert not first_file_path.exists()
    assert [job.job_type for job in jobs] == ["insert", "reindex"]
    assert all(job.status == "success" for job in jobs)
    assert any(audit.action == "document.replace" for audit in audits)

    indexed_documents = app.state.lightrag_adapter.indexed_documents
    assert [document.id for document in indexed_documents] == [document_id, document_id]
    assert [document.source_version for document in indexed_documents] == [1, 2]
    assert [document.content_text for document in indexed_documents] == [
        "Old leave policy",
        "New leave policy",
    ]
