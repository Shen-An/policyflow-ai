"""Idempotently seed a populated PolicyFlow demo database."""
# ruff: noqa: E402

import argparse
import hashlib
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from sqlmodel import Session, SQLModel, select

from backend.app.core.config import Settings
from backend.app.core.mcp_security import protect_command, protect_config
from backend.app.core.security import hash_password
from backend.app.db.init_db import initialize_database
from backend.app.db.models import (
    AIQueryLog,
    AuditLog,
    Conversation,
    Department,
    Draft,
    EvalCase,
    EvalResult,
    EvalRun,
    FAQDraft,
    KnowledgeBase,
    KnowledgeDocument,
    MCPServer,
    Message,
    QueryFeedback,
    RetrievalEvalItem,
    Role,
    Skill,
    ToolCallLog,
    User,
    UserRole,
)
from backend.app.db.session import build_engine

DEMO_PASSWORD = "12345678"
DOCUMENTS = {
    "hr": [
        (
            "[DEMO] 员工休假管理办法",
            "年假需提前三个工作日提交申请，由直属经理审批。病假超过一天需补充医疗证明。",
        ),
        (
            "[DEMO] 差旅与费用标准",
            "国内差旅住宿标准为每晚五百元。机票优先选择经济舱，出差申请须由部门负责人批准。",
        ),
    ],
    "finance": [
        (
            "[DEMO] 费用报销制度",
            "报销单应在费用发生后三十日内提交。单笔超过五千元需要财务负责人复核。",
        ),
        (
            "[DEMO] 采购付款流程",
            "采购付款需匹配合同、验收单和发票。付款申请由业务负责人和财务共同审批。",
        ),
    ],
    "it": [
        (
            "[DEMO] 信息安全管理规范",
            "员工不得共享账号密码。发现疑似钓鱼邮件应立即报告信息安全团队。",
        ),
        (
            "[DEMO] 账号与权限管理",
            "新员工账号由直属经理申请，离职账号应在最后工作日关闭。高权限账号每季度复核。",
        ),
    ],
    "admin": [
        ("[DEMO] 印章使用管理", "印章使用必须登记用途、经办人和审批人。合同用章需法务审核通过。")
    ],
    "legal": [
        (
            "[DEMO] 合同审查指引",
            "对外合同签署前必须完成法务审查。涉及个人信息处理时需增加数据保护条款。",
        )
    ],
}


def require(session: Session, model: type[SQLModel], field: Any, value: str) -> Any:
    item = session.exec(select(model).where(field == value)).first()
    if item is None:
        raise RuntimeError(f"Missing required record: {model.__name__}.{value}")
    return item


def ensure_user(
    session: Session, username: str, name: str, department: Department, role: Role
) -> User:
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None:
        user = User(
            username=username,
            email=f"{username}@example.com",
            display_name=name,
            password_hash=hash_password(DEMO_PASSWORD),
            department_id=department.id,
        )
        session.add(user)
        session.flush()
    if session.get(UserRole, (user.id, role.id)) is None:
        session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def ensure_document(
    session: Session,
    upload_dir: Path,
    kb: KnowledgeBase,
    admin: User,
    title: str,
    content: str,
    index: int,
) -> KnowledgeDocument:
    relative = Path("demo") / kb.code / f"policy-{index}.txt"
    target = upload_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    document = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.knowledge_base_id == kb.id, KnowledgeDocument.title == title
        )
    ).first()
    if document is None:
        document = KnowledgeDocument(
            knowledge_base_id=kb.id,
            title=title,
            file_path=str(relative),
            file_type="txt",
            content_text=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            index_status="indexed",
            created_by=admin.id,
        )
        session.add(document)
        session.flush()
    return document


def seed(database_url: str, admin_password: str) -> dict[str, int]:
    settings = Settings(
        DATABASE_URL=database_url, BOOTSTRAP_ADMIN_PASSWORD=admin_password, _env_file=None
    )
    engine = build_engine(database_url)
    initialize_database(engine, settings)
    now = datetime.now(UTC)
    with Session(engine) as session:
        admin = require(session, User, User.username, "admin")
        admin.password_hash = hash_password(admin_password)
        admin.status = "active"
        session.add(admin)
        departments = {row.code: row for row in session.exec(select(Department)).all()}
        roles = {row.code: row for row in session.exec(select(Role)).all()}
        kbs = {row.code: row for row in session.exec(select(KnowledgeBase)).all()}
        ensure_user(session, "demo.employee", "演示员工", departments["hr"], roles["employee"])
        ensure_user(
            session, "demo.kbadmin", "演示知识库管理员", departments["hr"], roles["kb_admin"]
        )
        docs = {}
        for code, specs in DOCUMENTS.items():
            docs[code] = [
                ensure_document(session, settings.UPLOAD_DIR, kbs[code], admin, title, content, i)
                for i, (title, content) in enumerate(specs, 1)
            ]
        for skill in session.exec(select(Skill)).all():
            skill.config = {
                **skill.config,
                "demo": True,
                "usage_hint": f"最终验收：{skill.description}",
            }
            session.add(skill)
        conversation = session.exec(
            select(Conversation).where(Conversation.title == "[DEMO] 年假与差旅咨询")
        ).first()
        if conversation is None:
            conversation = Conversation(
                user_id=admin.id,
                title="[DEMO] 年假与差旅咨询",
                summary="演示带引用、可信度和反馈的制度问答",
            )
            session.add(conversation)
            session.flush()
            question = "年假怎么申请，出差住宿标准是多少？"
            answer = (
                "年假需提前三个工作日申请并由直属经理审批 [1]。国内差旅住宿标准为每晚五百元 [2]。"
            )
            sources = [
                {"document_id": item.id, "title": item.title, "score": 0.95} for item in docs["hr"]
            ]
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="user",
                    content=question,
                    created_at=now - timedelta(minutes=8),
                )
            )
            query = AIQueryLog(
                conversation_id=conversation.id,
                user_id=admin.id,
                question=question,
                answer=answer,
                knowledge_base_ids=[kbs["hr"].id],
                retrieved_sources=sources,
                confidence_score=0.93,
                query_mode="mix",
                latency_ms=842,
                token_usage={"prompt_tokens": 420, "completion_tokens": 96},
            )
            session.add(query)
            session.flush()
            session.add(
                Message(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=answer,
                    meta_json={
                        "query_log_id": query.id,
                        "citations": sources,
                        "confidence_score": 0.93,
                        "query_mode": "mix",
                        "router_result": {"route": "knowledge_qa"},
                        "suggested_skills": ["process_checklist", "application_draft"],
                        "compliance": {"status": "passed"},
                    },
                    created_at=now - timedelta(minutes=7),
                )
            )
            session.add(
                QueryFeedback(
                    query_log_id=query.id,
                    user_id=admin.id,
                    rating="useful",
                    comment="[DEMO] 引用准确",
                )
            )
        draft_specs = [
            (
                "checklist",
                "[DEMO] 年假申请清单",
                "- 确认可用年假\n- 提前三个工作日申请\n- 等待经理审批",
                "draft",
            ),
            ("email", "[DEMO] 差旅申请邮件", "经理您好，我计划下周出差，现申请审批。", "confirmed"),
            ("form", "[DEMO] 报销申请表", "费用类型：差旅；金额：4800 元；附件：发票。", "draft"),
            ("memo", "[DEMO] 信息安全整改说明", "已完成高权限账号季度复核。", "discarded"),
        ]
        for draft_type, title, content, status in draft_specs:
            if session.exec(select(Draft).where(Draft.title == title)).first() is None:
                session.add(
                    Draft(
                        user_id=admin.id,
                        conversation_id=conversation.id,
                        draft_type=draft_type,
                        title=title,
                        content=content,
                        source_question="[DEMO] 请根据制度生成材料",
                        related_sources=[{"title": docs["hr"][0].title}],
                        status=status,
                    )
                )
        faq_specs = [
            ("年假需要提前多久申请？", "需提前三个工作日申请。", "draft", docs["hr"][0]),
            ("国内出差住宿标准是多少？", "每晚五百元。", "pending_review", docs["hr"][1]),
            ("报销应在多久内提交？", "费用发生后三十日内。", "approved", docs["finance"][0]),
            ("员工可以共享账号吗？", "不可以共享账号密码。", "rejected", docs["it"][0]),
        ]
        for question, answer, status, document in faq_specs:
            question = f"[DEMO] {question}"
            if session.exec(select(FAQDraft).where(FAQDraft.question == question)).first() is None:
                reviewed = status in {"approved", "rejected"}
                session.add(
                    FAQDraft(
                        knowledge_base_id=document.knowledge_base_id,
                        source_document_id=document.id,
                        source_conversation_id=conversation.id,
                        question=question,
                        answer=answer,
                        status=status,
                        reviewer_id=admin.id if reviewed else None,
                        review_note="[DEMO] 最终验收样例" if reviewed else None,
                    )
                )
        eval_specs = [
            ("[DEMO] 年假申请需要提前几天？", "hr", ["三个工作日"], docs["hr"][0]),
            (
                "[DEMO] 超过五千元的报销如何处理？",
                "finance",
                ["财务负责人", "复核"],
                docs["finance"][0],
            ),
            ("[DEMO] 发现钓鱼邮件怎么办？", "it", ["立即报告", "信息安全"], docs["it"][0]),
        ]
        cases = []
        for question, category, keywords, document in eval_specs:
            case = session.exec(select(EvalCase).where(EvalCase.question == question)).first()
            if case is None:
                case = EvalCase(
                    question=question,
                    category=category,
                    expected_answer_keywords=keywords,
                    expected_source_documents=[document.id],
                )
                session.add(case)
                session.flush()
            cases.append(case)
            if (
                session.exec(
                    select(RetrievalEvalItem).where(RetrievalEvalItem.eval_case_id == case.id)
                ).first()
                is None
            ):
                session.add(
                    RetrievalEvalItem(
                        eval_case_id=case.id,
                        query=question,
                        knowledge_base_ids=[document.knowledge_base_id],
                        relevant_document_ids=[document.id],
                        relevance_judgement={document.id: 3},
                    )
                )
        run = session.exec(select(EvalRun).where(EvalRun.name == "[DEMO] 最终验收评测")).first()
        if run is None:
            run = EvalRun(
                name="[DEMO] 最终验收评测",
                status="completed",
                total_cases=len(cases),
                started_at=now - timedelta(minutes=15),
                finished_at=now - timedelta(minutes=14),
                metrics={"pass_rate": 1.0, "average_score": 0.91, "hit_rate_at_5": 1.0},
                config_snapshot={"query_mode": "mix", "demo": True},
                created_by=admin.id,
                request_id="demo-eval-run",
            )
            session.add(run)
            session.flush()
            for i, case in enumerate(cases):
                session.add(
                    EvalResult(
                        eval_run_id=run.id,
                        eval_case_id=case.id,
                        question=case.question,
                        answer=f"[DEMO] 命中关键词：{'、'.join(case.expected_answer_keywords)}",
                        retrieval_metrics={"hit_rate_at_5": 1.0, "mrr": 1.0},
                        answer_metrics={"keyword_recall": 1.0},
                        type_statuses={"retrieval": "passed", "answer": "passed"},
                        score=0.94 - i * 0.03,
                        passed=True,
                        latency_ms=700 + i * 80,
                    )
                )
        if (
            session.exec(select(MCPServer).where(MCPServer.name == "[DEMO] 企业流程工具")).first()
            is None
        ):
            session.add(
                MCPServer(
                    name="[DEMO] 企业流程工具",
                    server_type="mock",
                    integration_mode="mock",
                    endpoint="mock://policyflow-demo",
                    command=protect_command("policyflow-demo-mcp", settings.SECRET_KEY),
                    config=protect_config(
                        {"environment": "demo", "api_key": "masked-demo-secret"}, settings.SECRET_KEY
                    ),
                    enabled=True,
                    health_status="healthy",
                    tools=["leave.balance", "expense.validate", "contract.check"],
                    last_checked_at=now,
                )
            )
        tool_specs = [
            ("knowledge.search", "success", {"query": "年假申请"}, {"matches": 2}, None, 183),
            ("draft.create", "success", {"draft_type": "checklist"}, {"created": True}, None, 91),
            (
                "mcp.call",
                "failed",
                {"tool": "expense.validate"},
                {},
                "[DEMO] 上游服务暂不可用",
                1200,
            ),
        ]
        for i, (name, status, input_data, output_data, error, latency) in enumerate(tool_specs):
            request_id = f"demo-tool-{i}"
            if (
                session.exec(
                    select(ToolCallLog).where(ToolCallLog.request_id == request_id)
                ).first()
                is None
            ):
                session.add(
                    ToolCallLog(
                        conversation_id=conversation.id,
                        agent_name="demo_agent",
                        tool_name=name,
                        user_id=admin.id,
                        request_id=request_id,
                        input_summary=input_data,
                        output_summary=output_data,
                        status=status,
                        error_message=error,
                        latency_ms=latency,
                        created_at=now - timedelta(minutes=i),
                    )
                )
        actions = [
            "demo.seed",
            "knowledge_base.view",
            "document.upload",
            "chat.query",
            "skill.run",
            "draft.create",
            "faq.approve",
            "eval.run",
            "mcp.server.health_check",
        ]
        for i, action in enumerate(actions):
            request_id = f"demo-audit-{i}"
            if (
                session.exec(select(AuditLog).where(AuditLog.request_id == request_id)).first()
                is None
            ):
                session.add(
                    AuditLog(
                        actor_id=admin.id,
                        action=action,
                        target_type="demo",
                        target_id=conversation.id,
                        detail={"demo": True, "sequence": i},
                        ip_address="127.0.0.1",
                        request_id=request_id,
                        created_at=now - timedelta(minutes=i),
                    )
                )
        session.commit()
        counts = {
            "users": len(session.exec(select(User)).all()),
            "documents": len(session.exec(select(KnowledgeDocument)).all()),
            "drafts": len(session.exec(select(Draft)).all()),
            "faqs": len(session.exec(select(FAQDraft)).all()),
            "eval_cases": len(session.exec(select(EvalCase)).all()),
            "eval_runs": len(session.exec(select(EvalRun)).all()),
            "mcp_servers": len(session.exec(select(MCPServer)).all()),
            "tool_logs": len(session.exec(select(ToolCallLog)).all()),
            "audit_logs": len(session.exec(select(AuditLog)).all()),
        }
    engine.dispose()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default="sqlite:///./policyflow.db")
    parser.add_argument("--admin-password", default=DEMO_PASSWORD)
    args = parser.parse_args()
    print(seed(args.database_url, args.admin_password))


if __name__ == "__main__":
    main()
