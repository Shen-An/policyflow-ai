"""Response compliance / verifier gate."""

from __future__ import annotations

import re

from backend.app.agents.grounding import claim_evidence_analysis
from backend.app.core.config import Settings, get_settings
from backend.app.schemas.chat import ComplianceResult
from backend.app.schemas.retrieval import Evidence

_CITE_RE = re.compile(r"\[(\d+)\]")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z_])(\d+(?:\.\d+)?)(?:\s*)(元|天|日|小时|%|％|人|次|年|月|周)?"
)
_REFUSE_MARKERS = (
    "没有检索到可靠制度证据",
    "未检索到知识库依据",
    "无法给出可作为制度依据",
    "没有检索到",
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _token_set(text: str) -> set[str]:
    return set(re.findall(r"[一-鿿]{2,}|[A-Za-z0-9_]{2,}", (text or "").lower()))


class ComplianceAgent:
    """Rule-based verifier for refuse consistency, citations, and light grounding."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    async def run(self, answer: str, evidence: list[Evidence]) -> ComplianceResult:
        warnings: list[str] = []
        cleaned = (answer or "").strip()
        if not cleaned:
            warnings.append("EMPTY_ANSWER")

        has_evidence = bool(evidence)
        if not has_evidence:
            warnings.append("NO_RELIABLE_EVIDENCE")
            if cleaned and not any(marker in cleaned for marker in _REFUSE_MARKERS):
                warnings.append("SOFT_ANSWER_WITHOUT_EVIDENCE")
        else:
            cited_indices = {int(item) for item in _CITE_RE.findall(cleaned)}
            valid = set(range(1, len(evidence) + 1))
            if cited_indices:
                dangling = cited_indices - valid
                if dangling:
                    warnings.append("DANGLING_CITATIONS")
                unsupported = 0
                answer_tokens = _token_set(cleaned)
                for index in sorted(cited_indices & valid):
                    snippet_tokens = _token_set(evidence[index - 1].snippet)
                    if snippet_tokens and len(answer_tokens & snippet_tokens) == 0:
                        unsupported += 1
                if unsupported:
                    warnings.append("CITATION_EVIDENCE_MISMATCH")
            elif "无法给出" not in cleaned and not any(
                marker in cleaned for marker in _REFUSE_MARKERS
            ):
                warnings.append("MISSING_CITATION_MARKERS")

            evidence_blob = _normalize("\n".join(item.snippet for item in evidence))
            suspicious_numbers = 0
            for match in _NUMBER_RE.finditer(cleaned):
                number = match.group(1)
                unit = match.group(2) or ""
                if unit == "" and len(number) <= 1:
                    continue
                needle = _normalize(f"{number}{unit}")
                if needle and needle not in evidence_blob and number not in evidence_blob:
                    suspicious_numbers += 1
            if suspicious_numbers >= 2:
                warnings.append("UNGROUNDED_NUMERIC_CLAIMS")

            # Sentence-level lexical claim support (honest non-LLM claim check).
            claim = claim_evidence_analysis(cleaned, evidence)
            if claim["flag"]:
                warnings.append("WEAK_CLAIM_EVIDENCE_SUPPORT")

        hard_fail_codes = {"EMPTY_ANSWER"}
        if self.settings.CHAT_HARD_REFUSE_WITHOUT_EVIDENCE:
            hard_fail_codes.add("NO_RELIABLE_EVIDENCE")
            hard_fail_codes.add("SOFT_ANSWER_WITHOUT_EVIDENCE")

        passed = not any(code in hard_fail_codes for code in warnings)
        deduped: list[str] = []
        for item in warnings:
            if item not in deduped:
                deduped.append(item)
        return ComplianceResult(passed=passed, warnings=deduped)
