"""MVP response compliance checks."""

from backend.app.schemas.chat import ComplianceResult
from backend.app.schemas.retrieval import Evidence


class ComplianceAgent:
    async def run(self, answer: str, evidence: list[Evidence]) -> ComplianceResult:
        warnings: list[str] = []
        if not evidence:
            warnings.append("NO_RELIABLE_EVIDENCE")
        if not answer.strip():
            warnings.append("EMPTY_ANSWER")
        return ComplianceResult(passed="EMPTY_ANSWER" not in warnings, warnings=warnings)
