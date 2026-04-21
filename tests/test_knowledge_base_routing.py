"""Knowledge-base candidate routing tests."""

from backend.app.db.models import KnowledgeBase
from backend.app.services.knowledge_base_router import select_candidate_knowledge_bases


def knowledge_base(code: str, name: str, description: str) -> KnowledgeBase:
    return KnowledgeBase(
        code=code,
        name=name,
        description=description,
        department_id=f"department-{code}",
        rag_workspace=f"rag/{code}",
    )


def test_routes_information_security_question_to_it_workspace() -> None:
    knowledge_bases = [
        knowledge_base("hr", "人力资源制度库", "员工与休假制度"),
        knowledge_base("finance", "财务制度库", "报销与付款制度"),
        knowledge_base("it", "IT 制度库", "信息技术与安全制度"),
        knowledge_base("admin", "行政制度库", "行政与印章制度"),
        knowledge_base("legal", "法务制度库", "合同与合规制度"),
    ]

    selected = select_candidate_knowledge_bases("信息技术安全制度有哪些要求？", knowledge_bases)

    assert [item.code for item in selected] == ["it"]


def test_routes_cross_domain_question_to_multiple_workspaces() -> None:
    knowledge_bases = [
        knowledge_base("hr", "人力资源制度库", "员工与休假制度"),
        knowledge_base("finance", "财务制度库", "报销与付款制度"),
        knowledge_base("admin", "行政制度库", "行政与差旅申请"),
    ]

    selected = select_candidate_knowledge_bases(
        "出差申请和费用报销分别需要什么材料？", knowledge_bases
    )

    assert {item.code for item in selected} == {"finance", "admin"}


def test_ambiguous_question_falls_back_to_all_authorized_workspaces() -> None:
    knowledge_bases = [
        knowledge_base("hr", "人力资源制度库", "员工制度"),
        knowledge_base("finance", "财务制度库", "财务制度"),
    ]

    selected = select_candidate_knowledge_bases("请总结公司的主要制度", knowledge_bases)

    assert selected == knowledge_bases
