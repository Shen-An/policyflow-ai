"""Optional RAGAS evaluation with honest skip/proxy fallbacks.

Priority when enabled:
1. Real `ragas` package metrics (if installed and runnable)
2. Token-overlap proxy metrics (clearly labeled, for local demos without ragas)
3. skipped with an explicit reason
"""

from __future__ import annotations

import asyncio
import re
from importlib.util import find_spec
from typing import Any, Literal

from pydantic import BaseModel, Field


class RagasEvaluationInput(BaseModel):
    question: str
    answer: str
    contexts: list[str]
    reference_answer: str | None = None


class RagasEvaluationResult(BaseModel):
    status: Literal["completed", "skipped", "failed"]
    metrics: dict[str, float] = Field(default_factory=dict)
    reason: str | None = None
    metrics_source: str | None = None


_TOKEN_RE = re.compile(r"[一-鿿]{2,}|[A-Za-z0-9_]{2,}")


def _tokens(text: str) -> set[str]:
    return {item.lower() for item in _TOKEN_RE.findall(text or "")}


def _overlap_ratio(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def _proxy_metrics(data: RagasEvaluationInput) -> dict[str, float]:
    """Cheap non-LLM proxies for interview demos when ragas is unavailable.

    These are NOT RAGAS scores. They approximate:
    - answer_relevancy ≈ answer∩question / answer
    - faithfulness ≈ answer∩contexts / answer
    - context_precision ≈ contexts overlapping answer / contexts
    - context_recall ≈ reference∩contexts / reference (if reference present)
    """
    answer_tokens = _tokens(data.answer)
    question_tokens = _tokens(data.question)
    context_tokens = _tokens("\n".join(data.contexts))
    per_context = [_tokens(item) for item in data.contexts if item.strip()]

    answer_relevancy = _overlap_ratio(answer_tokens, question_tokens | context_tokens)
    faithfulness = _overlap_ratio(answer_tokens, context_tokens) if context_tokens else 0.0
    if per_context:
        context_precision = sum(
            1.0 if _overlap_ratio(ctx, answer_tokens) > 0.05 else 0.0 for ctx in per_context
        ) / len(per_context)
    else:
        context_precision = 0.0

    metrics = {
        "answer_relevancy": round(answer_relevancy, 4),
        "faithfulness": round(faithfulness, 4),
        "context_precision": round(context_precision, 4),
    }
    if data.reference_answer:
        reference_tokens = _tokens(data.reference_answer)
        metrics["context_recall"] = round(_overlap_ratio(reference_tokens, context_tokens), 4)
        metrics["answer_correctness"] = round(
            _overlap_ratio(answer_tokens, reference_tokens), 4
        )
    return metrics


def _extract_metric_value(row: Any, key: str) -> float | None:
    if isinstance(row, dict) and key in row:
        try:
            return float(row[key])
        except (TypeError, ValueError):
            return None
    value = getattr(row, key, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class RagasRunner:
    def __init__(self, *, allow_proxy_fallback: bool = True) -> None:
        self.allow_proxy_fallback = allow_proxy_fallback

    async def run(
        self,
        data: RagasEvaluationInput,
        enabled: bool,
        metrics: list[str] | None = None,
    ) -> RagasEvaluationResult:
        if not enabled:
            return RagasEvaluationResult(status="skipped", reason="disabled")
        if not data.answer.strip():
            return RagasEvaluationResult(status="skipped", reason="empty_answer")
        if not data.contexts:
            return RagasEvaluationResult(status="skipped", reason="empty_contexts")

        if find_spec("ragas") is not None:
            try:
                real = await asyncio.to_thread(self._run_ragas_sync, data, metrics or [])
                return real
            except Exception as exc:  # noqa: BLE001 - keep eval path resilient
                if not self.allow_proxy_fallback:
                    return RagasEvaluationResult(
                        status="failed",
                        reason=f"ragas_error:{type(exc).__name__}",
                    )
                proxy = _proxy_metrics(data)
                return RagasEvaluationResult(
                    status="completed",
                    metrics=proxy,
                    reason=f"ragas_error_fallback_proxy:{type(exc).__name__}",
                    metrics_source="token_overlap_proxy",
                )

        if self.allow_proxy_fallback:
            return RagasEvaluationResult(
                status="completed",
                metrics=_proxy_metrics(data),
                reason="missing_dependency_using_proxy",
                metrics_source="token_overlap_proxy",
            )
        return RagasEvaluationResult(status="skipped", reason="missing_dependency")

    def _run_ragas_sync(
        self,
        data: RagasEvaluationInput,
        metric_names: list[str],
    ) -> RagasEvaluationResult:
        # Lazy imports: ragas is optional and heavy.
        from datasets import Dataset  # type: ignore
        from ragas import evaluate  # type: ignore
        from ragas.metrics import (  # type: ignore
            answer_relevancy,
            context_precision,
            context_recall,
            faithfulness,
        )

        selected = {
            "faithfulness": faithfulness,
            "answer_relevancy": answer_relevancy,
            "context_precision": context_precision,
            "context_recall": context_recall,
        }
        if metric_names:
            metrics = [selected[name] for name in metric_names if name in selected]
        else:
            metrics = [faithfulness, answer_relevancy, context_precision]
            if data.reference_answer:
                metrics.append(context_recall)
        if not metrics:
            metrics = [faithfulness, answer_relevancy]

        row: dict[str, Any] = {
            "question": data.question,
            "answer": data.answer,
            "contexts": data.contexts,
        }
        if data.reference_answer:
            row["ground_truth"] = data.reference_answer

        dataset = Dataset.from_list([row])
        result = evaluate(dataset, metrics=metrics)
        try:
            frame = result.to_pandas()
            record = frame.iloc[0].to_dict()
        except Exception:
            record = dict(result) if isinstance(result, dict) else {}

        metrics_out: dict[str, float] = {}
        for key in (
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
            "answer_correctness",
        ):
            value = _extract_metric_value(record, key)
            if value is not None:
                metrics_out[key] = round(value, 4)
        if not metrics_out:
            raise RuntimeError("ragas returned no numeric metrics")
        return RagasEvaluationResult(
            status="completed",
            metrics=metrics_out,
            metrics_source="ragas",
        )
