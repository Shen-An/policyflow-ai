"""Grounding helpers: confidence estimation and light claim–evidence checks."""

from __future__ import annotations

import re
from typing import Any

from backend.app.schemas.retrieval import Evidence

_CITE_RE = re.compile(r"\[(\d+)\]")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])\s*|\n+")
_TOKEN_RE = re.compile(r"[一-鿿]{2,}|[A-Za-z0-9_]{2,}")


def _tokens(text: str) -> set[str]:
    """Tokenize for lexical overlap.

    Continuous Chinese has no spaces; use 2-gram CJK windows plus alnum words so
    short policy sentences still yield multiple tokens.
    """
    text = text or ""
    tokens: set[str] = set()
    for match in re.finditer(r"[A-Za-z0-9_]{2,}", text):
        tokens.add(match.group(0).lower())
    # CJK characters as overlapping bigrams.
    chars = re.findall(r"[一-鿿]", text)
    if len(chars) == 1:
        tokens.add(chars[0])
    for index in range(len(chars) - 1):
        tokens.add(chars[index] + chars[index + 1])
    return tokens


def _sentences(text: str) -> list[str]:
    parts = [item.strip() for item in _SENTENCE_SPLIT.split(text or "") if item and item.strip()]
    return [item for item in parts if len(item) >= 8]


def question_evidence_support(
    question: str,
    evidence: list[Evidence],
    *,
    min_overlap_ratio: float = 0.08,
) -> dict[str, Any]:
    """Measure whether retrieved evidence is about the original question.

    Used to avoid answering from off-topic hits after aggressive query rewrite.
    """
    q_tokens = _tokens(question)
    if not evidence:
        return {
            "supported": False,
            "overlap_ratio": 0.0,
            "overlap_tokens": 0,
            "question_tokens": len(q_tokens),
            "long_term_hit": False,
        }
    if not q_tokens:
        return {
            "supported": True,
            "overlap_ratio": 1.0,
            "overlap_tokens": 0,
            "question_tokens": 0,
            "long_term_hit": False,
        }
    evidence_tokens = _tokens(
        "\n".join(f"{item.document_title or ''} {item.snippet}" for item in evidence)
    )
    overlap = len(q_tokens & evidence_tokens)
    ratio = overlap / max(len(q_tokens), 1)
    long_terms = [term for term in q_tokens if len(term) >= 5 and term.isascii()]
    long_hit = any(
        term in (item.snippet or "").lower() or term in (item.document_title or "").lower()
        for item in evidence
        for term in long_terms
    )
    supported = ratio >= min_overlap_ratio or long_hit
    return {
        "supported": supported,
        "overlap_ratio": round(ratio, 4),
        "overlap_tokens": overlap,
        "question_tokens": len(q_tokens),
        "long_term_hit": long_hit,
    }


def citation_stats(answer: str, evidence_count: int) -> dict[str, Any]:
    cited = sorted({int(item) for item in _CITE_RE.findall(answer or "")})
    valid = set(range(1, evidence_count + 1))
    valid_cited = [item for item in cited if item in valid]
    dangling = [item for item in cited if item not in valid]
    return {
        "cited": cited,
        "valid_cited": valid_cited,
        "dangling": dangling,
        "coverage": (len(valid_cited) / evidence_count) if evidence_count else 0.0,
    }


def claim_evidence_analysis(
    answer: str,
    evidence: list[Evidence],
    *,
    min_overlap: int = 2,
    weak_ratio_threshold: float = 0.45,
) -> dict[str, Any]:
    """Lexical claim–evidence support check (not an LLM judge).

    Splits the answer into sentences and measures token overlap with the
    concatenated evidence snippets. Returns weak sentence ratio and samples.
    """
    if not answer.strip() or not evidence:
        return {
            "sentence_count": 0,
            "weak_sentence_count": 0,
            "weak_ratio": 0.0,
            "weak_samples": [],
            "flag": False,
        }

    evidence_tokens = _tokens("\n".join(item.snippet for item in evidence))
    sentences = _sentences(answer)
    # Ignore pure meta / refuse sentences.
    skip_markers = ("未检索到", "不可信", "仅供参考", "请与相关", "非正式系统表单")
    evaluable = [
        sentence
        for sentence in sentences
        if not any(marker in sentence for marker in skip_markers)
        and not sentence.startswith("【")
    ]
    weak_samples: list[str] = []
    weak = 0
    for sentence in evaluable:
        tokens = _tokens(sentence)
        if len(tokens) < 3:
            continue
        overlap = len(tokens & evidence_tokens)
        if overlap < min_overlap:
            weak += 1
            if len(weak_samples) < 3:
                weak_samples.append(sentence[:120])
    total = max(len(evaluable), 1)
    weak_ratio = weak / total if evaluable else 0.0
    return {
        "sentence_count": len(evaluable),
        "weak_sentence_count": weak,
        "weak_ratio": round(weak_ratio, 4),
        "weak_samples": weak_samples,
        "flag": bool(evaluable) and weak_ratio >= weak_ratio_threshold and weak >= 2,
    }


def estimate_answer_confidence(
    *,
    answer: str,
    evidence: list[Evidence],
    skill_results: list[dict[str, Any]] | None = None,
    compliance_warnings: list[str] | None = None,
) -> float:
    """Evidence/citation/verifier-aware confidence in [0, 0.95]."""
    if not evidence:
        return 0.0

    warnings = set(compliance_warnings or [])
    cites = citation_stats(answer, len(evidence))
    claim = claim_evidence_analysis(answer, evidence)

    # Base from evidence breadth, but cap contribution of synthetic-only stacks.
    synthetic_count = sum(
        1
        for item in evidence
        if bool((item.metadata or {}).get("score_is_synthetic"))
    )
    real_score_count = len(evidence) - synthetic_count
    base = 0.45 + min(len(evidence), 6) * 0.04
    if real_score_count:
        base += 0.05
    if synthetic_count == len(evidence):
        base = min(base, 0.72)

    # Citation quality
    if cites["valid_cited"]:
        base += 0.08 + min(len(cites["valid_cited"]), 3) * 0.02
    else:
        base -= 0.08
    if cites["dangling"]:
        base -= 0.06

    # Skill context is mild positive only when successful.
    if skill_results and any(item.get("status") == "success" for item in skill_results):
        base += 0.03

    # Claim–evidence lexical support
    if claim["flag"]:
        base -= 0.12
    elif claim["weak_ratio"] > 0.25:
        base -= 0.05

    # Compliance warnings
    penalty_map = {
        "DANGLING_CITATIONS": 0.08,
        "CITATION_EVIDENCE_MISMATCH": 0.1,
        "MISSING_CITATION_MARKERS": 0.06,
        "UNGROUNDED_NUMERIC_CLAIMS": 0.12,
        "WEAK_CLAIM_EVIDENCE_SUPPORT": 0.1,
        "SOFT_ANSWER_WITHOUT_EVIDENCE": 0.2,
        "EMPTY_ANSWER": 0.5,
    }
    for code, penalty in penalty_map.items():
        if code in warnings:
            base -= penalty

    # Template-ish answers are less authoritative.
    if any(token in (answer or "") for token in ("参考草稿", "非正式系统表单", "填写模板")):
        base = min(base, 0.85)

    return max(0.0, min(0.95, round(base, 4)))
