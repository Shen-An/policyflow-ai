"""Local lexical reranker (always available) with optional external hook.

This is intentionally honest:
- Default path is a local score fusion reranker (lexical overlap + original score).
- It is NOT a cross-encoder / BGE-reranker model.
- Metadata records `rerank_method` so demos don't oversell the implementation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from backend.app.schemas.retrieval import Evidence


def _terms(value: str) -> set[str]:
    normalized = (value or "").lower()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    chinese = re.findall(r"[一-鿿]", normalized)
    if len(chinese) == 1:
        words.add(chinese[0])
    words.update(chinese[index] + chinese[index + 1] for index in range(max(0, len(chinese) - 1)))
    return {term for term in words if term}


def _lexical_score(query_terms: set[str], evidence: Evidence) -> float:
    if not query_terms:
        return 0.0
    blob = f"{evidence.knowledge_base_name} {evidence.document_title or ''} {evidence.snippet}"
    evidence_terms = _terms(blob)
    if not evidence_terms:
        return 0.0
    overlap = len(query_terms & evidence_terms)
    # Precision-ish overlap against query terms, lightly boosted by coverage of evidence.
    precision = overlap / len(query_terms)
    coverage = overlap / max(len(evidence_terms), 1)
    return 0.8 * precision + 0.2 * coverage


class RerankService:
    """Default local reranker used by RAGService."""

    def __init__(self, *, lexical_weight: float = 0.65, original_weight: float = 0.35) -> None:
        self.lexical_weight = lexical_weight
        self.original_weight = original_weight

    @property
    def available(self) -> bool:
        return True

    async def rerank(
        self,
        query: str,
        candidates: Sequence[Evidence],
        limit: int,
    ) -> list[Evidence]:
        if limit <= 0:
            return []
        if not candidates:
            return []

        query_terms = _terms(query)
        scored: list[tuple[float, int, Evidence]] = []
        for index, item in enumerate(candidates):
            lexical = _lexical_score(query_terms, item)
            original = float(item.score or 0.0)
            # Synthetic original scores contribute less.
            if bool((item.metadata or {}).get("score_is_synthetic")):
                fused = 0.85 * lexical + 0.15 * original
            else:
                fused = self.lexical_weight * lexical + self.original_weight * original
            scored.append((fused, index, item))

        scored.sort(key=lambda row: (-row[0], row[1]))
        reranked: list[Evidence] = []
        for rank, (fused, _, item) in enumerate(scored[:limit], start=1):
            metadata = dict(item.metadata or {})
            metadata.update(
                {
                    "rerank_method": "local_lexical_fusion",
                    "rerank_score": round(fused, 6),
                }
            )
            reranked.append(
                item.model_copy(
                    update={
                        "rank": rank,
                        "rerank_score": fused,
                        "metadata": metadata,
                    }
                )
            )
        return reranked
