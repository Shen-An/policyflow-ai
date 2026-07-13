"""Multi-layer memory system unit and integration tests."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.agents.memory_agent import MemoryAgent
from backend.app.core.config import Settings
from backend.app.core.exceptions import ApplicationError
from backend.app.db.models import KnowledgeBase, KnowledgeDocument, MemoryItem, User
from backend.app.main import create_app
from backend.app.schemas.retrieval import Evidence, RetrievalRequest
from backend.app.services.context_service import build_context
from backend.app.services.memory_extractor import extract_memory_events
from backend.app.services.memory_service import (
    MEMORY_TYPE_ENTITY,
    MEMORY_TYPE_LONG_TERM,
    MEMORY_TYPE_PREFERENCE,
    cosine_similarity,
    list_fixed_memories,
    search_memories,
    upsert_entity,
    write_memory,
)
from backend.app.services.memory_window import (
    dump_conversation_summary,
    parse_conversation_summary,
    should_compress,
)


class MemoryLightRAG:
    name = "lightrag"

    @property
    def available(self) -> bool:
        return True

    async def insert_document(
        self,
        knowledge_base: KnowledgeBase,
        document: KnowledgeDocument,
    ) -> None:
        return None

    async def retrieve(self, request: RetrievalRequest, limit: int) -> list[Evidence]:
        return [
            Evidence(
                knowledge_base_id=request.knowledge_base_ids[0],
                knowledge_base_name="HR",
                document_id="doc-memory",
                document_title="Travel Policy",
                snippet="Travel applications require manager approval.",
                score=0.9,
                retriever_type="lightrag",
                rank=1,
            )
        ]


class CapturingLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        return True

    async def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        if "记忆抽取器" in system_prompt:
            return "[]"
        if "会话摘要器" in system_prompt:
            return "用户持续咨询差旅流程，偏好简洁回答。"
        # Answer path
        if "最近对话" in user_prompt or "用户固定偏好" in user_prompt:
            return "根据上文与偏好，差旅申请需要经理审批 [1]。"
        return "差旅申请需要经理审批 [1]。"


class FakeEmbedding:
    @property
    def available(self) -> bool:
        return True

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            # Deterministic tiny vectors from char codes.
            seed = sum(ord(ch) for ch in text) % 97
            vectors.append([float(seed), float((seed * 3) % 97), float((seed * 7) % 97)])
        return vectors


def build_memory_app(tmp_path: Path, llm: CapturingLLM | None = None) -> FastAPI:
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'memory-system.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        UPLOAD_DIR=tmp_path / "uploads",
        RAG_WORKSPACE_DIR=tmp_path / "rag",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        MEMORY_STM_WINDOW_TURNS=2,
        MEMORY_COMPRESS_TURN_THRESHOLD=2,
        MEMORY_LTM_SALIENCE_THRESHOLD=0.4,
        _env_file=None,
    )
    language_model = llm or CapturingLLM()
    app = create_app(
        settings,
        lightrag_adapter=MemoryLightRAG(),
        llm_service=language_model,
    )
    # Replace embedding service used by memory agent for deterministic tests.
    app.state.embedding_service = FakeEmbedding()
    app.state.memory_agent = MemoryAgent(
        settings,
        llm_service=language_model,
        embedding_service=FakeEmbedding(),
    )
    return app


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return str(response.json()["access_token"])


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_cosine_and_keyword_search(tmp_path: Path) -> None:
    app = build_memory_app(tmp_path)
    with TestClient(app):
        with Session(app.state.engine) as session:
            write_memory(
                session,
                "user",
                "u1",
                MEMORY_TYPE_LONG_TERM,
                "用户偏好使用表格回答差旅问题",
                embedding=[1.0, 0.0, 0.0],
                meta_json={"event_type": "preference"},
            )
            write_memory(
                session,
                "user",
                "u1",
                MEMORY_TYPE_LONG_TERM,
                "用户默认打印机在3楼",
                embedding=[0.0, 1.0, 0.0],
                meta_json={"event_type": "conversation_fact"},
            )
            hits = search_memories(
                session,
                owner_specs=[("user", "u1")],
                query="差旅 表格",
                query_embedding=[1.0, 0.0, 0.0],
                top_k=1,
            )
            assert len(hits) == 1
            assert "表格" in hits[0].content
            assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)


def test_entity_upsert_merges_facts(tmp_path: Path) -> None:
    app = build_memory_app(tmp_path)
    with TestClient(app):
        with Session(app.state.engine) as session:
            first = upsert_entity(
                session,
                user_id="u1",
                entity_type="department",
                name="财务部",
                facts=["默认部门是财务部"],
            )
            second = upsert_entity(
                session,
                user_id="u1",
                entity_type="department",
                name="财务部",
                facts=["报销对接人是张三"],
            )
            assert first.id == second.id
            facts = second.meta_json.get("facts") or []
            assert "默认部门是财务部" in facts
            assert "报销对接人是张三" in facts
            items = session.exec(
                select(MemoryItem).where(MemoryItem.memory_type == MEMORY_TYPE_ENTITY)
            ).all()
            assert len(items) == 1


def test_policy_fact_forbidden_in_preference(tmp_path: Path) -> None:
    app = build_memory_app(tmp_path)
    with TestClient(app):
        with Session(app.state.engine) as session:
            with pytest.raises(ApplicationError) as exc:
                write_memory(
                    session,
                    "user",
                    "u1",
                    MEMORY_TYPE_PREFERENCE,
                    "制度规定必须经理审批",
                )
            assert exc.value.code == "MEMORY_POLICY_FACT_FORBIDDEN"


@pytest.mark.asyncio
async def test_extractor_heuristic_preference() -> None:
    events = await extract_memory_events(
        "以后请用表格回答",
        "好的，后续会用表格。",
        llm_service=None,
    )
    assert any(event.event_type == "preference" for event in events)


def test_summary_parse_and_compress_threshold() -> None:
    parsed = parse_conversation_summary('{"rolling_summary":"hello","key_entities":["a"]}')
    assert parsed["rolling_summary"] == "hello"
    legacy = parse_conversation_summary("plain text summary")
    assert legacy["rolling_summary"] == "plain text summary"
    assert should_compress(5, threshold_turns=2) is True
    assert should_compress(2, threshold_turns=2) is False
    dumped = dump_conversation_summary(parsed)
    assert "hello" in dumped


def test_context_partitions_memory(tmp_path: Path) -> None:
    app = build_memory_app(tmp_path)
    with TestClient(app):
        with Session(app.state.engine) as session:
            pref = write_memory(
                session,
                "user",
                "u1",
                MEMORY_TYPE_PREFERENCE,
                "Prefers bullet points",
            )
            ltm = write_memory(
                session,
                "user",
                "u1",
                MEMORY_TYPE_LONG_TERM,
                "上次讨论过差旅上限",
            )
            evidence = [
                Evidence(
                    knowledge_base_id="kb",
                    knowledge_base_name="KB",
                    snippet="Authoritative policy evidence",
                    retriever_type="lightrag",
                    rank=1,
                )
            ]
            context = build_context(
                evidence,
                [],
                [{"role": "user", "content": "hi"}],
                fixed_memories=[pref],
                recalled_memories=[ltm],
                rolling_summary="earlier summary",
            )
    assert context["authoritative_evidence"][0]["snippet"] == "Authoritative policy evidence"
    assert context["fixed_memories"][0]["content"] == "Prefers bullet points"
    assert context["recalled_memories"][0]["content"] == "上次讨论过差旅上限"
    assert context["rolling_summary"] == "earlier summary"
    assert "must not replace" in context["rules"][0]


def test_chat_uses_history_and_writes_preference(tmp_path: Path) -> None:
    llm = CapturingLLM()
    app = build_memory_app(tmp_path, llm)
    with TestClient(app) as client:
        token = login(client, "admin", "test-password")
        auth = headers(token)

        first = client.post(
            "/api/chat",
            headers=auth,
            json={"question": "差旅申请流程有哪些步骤？"},
        )
        assert first.status_code == 200
        conversation_id = first.json()["conversation_id"]

        pref = client.post(
            "/api/chat",
            headers=auth,
            json={
                "conversation_id": conversation_id,
                "question": "以后请用表格回答",
            },
        )
        assert pref.status_code == 200

        follow = client.post(
            "/api/chat",
            headers=auth,
            json={
                "conversation_id": conversation_id,
                "question": "那报销呢？",
            },
        )
        assert follow.status_code == 200

        # Answer prompts should include non-authoritative history/preferences on later turns.
        answer_prompts = [
            user_prompt
            for system_prompt, user_prompt in llm.calls
            if "企业制度助手" in system_prompt
        ]
        assert any(
            "最近对话" in prompt or "用户固定偏好" in prompt for prompt in answer_prompts[1:]
        )

        with Session(app.state.engine) as session:
            user = session.exec(select(User).where(User.username == "admin")).one()
            user_memories = session.exec(
                select(MemoryItem).where(MemoryItem.owner_id == user.id)
            ).all()
            all_memories = session.exec(select(MemoryItem)).all()
            assert any(item.memory_type == MEMORY_TYPE_PREFERENCE for item in user_memories)
            assert any(item.memory_type == "conversation_summary" for item in all_memories)
            fixed = list_fixed_memories(session, user.id)
            assert any(item.memory_type == MEMORY_TYPE_PREFERENCE for item in fixed)


def test_memory_tool_rejects_other_user_owner(tmp_path: Path) -> None:
    app = build_memory_app(tmp_path)
    with TestClient(app) as client:
        token = login(client, "admin", "test-password")
        response = client.post(
            "/api/tools/memory.write/run",
            headers=headers(token),
            json={
                "input": {
                    "owner_type": "user",
                    "owner_id": "someone-else",
                    "content": "Prefers concise answers",
                }
            },
        )
        assert response.status_code == 403
