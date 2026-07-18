"""Batch retrieval and answer evaluation orchestration."""

from statistics import mean
from typing import Any, cast

from sqlalchemy.engine import Engine
from sqlmodel import Session, col, select

from backend.app.agents.pipeline import AgentPipeline
from backend.app.db.models import (
    EvalCase,
    EvalResult,
    EvalRun,
    KnowledgeBase,
    RetrievalEvalItem,
    utc_now,
)
from backend.app.evals.ragas_runner import RagasEvaluationInput, RagasRunner
from backend.app.evals.retrieval_metrics import calculate_retrieval_metrics
from backend.app.schemas.eval import EvalRunCreate
from backend.app.schemas.retrieval import RetrievalRequest
from backend.app.services.rag_service import RAGService


def _average_numeric_metrics(metric_sets: list[dict[str, Any]]) -> dict[str, float]:
    names = {
        key
        for metrics in metric_sets
        for key, value in metrics.items()
        if key != "status" and isinstance(value, int | float)
    }
    return {
        name: mean(
            float(cast(int | float, metrics[name]))
            for metrics in metric_sets
            if isinstance(metrics.get(name), int | float)
        )
        for name in names
    }


class EvalRunner:
    def __init__(
        self,
        engine: Engine,
        rag_service: RAGService,
        pipeline: AgentPipeline,
    ) -> None:
        self.engine = engine
        self.rag_service = rag_service
        self.pipeline = pipeline
        self.ragas_runner = RagasRunner()

    async def _run_retrieval_item(
        self,
        run_id: str,
        item: RetrievalEvalItem,
        request: EvalRunCreate,
        strategy: Any | None = None,
    ) -> EvalResult:
        selected_strategy = strategy or request.retrieval_config.strategy
        retrieval = await self.rag_service.retrieve(
            RetrievalRequest(
                query=item.query,
                knowledge_base_ids=item.knowledge_base_ids,
                strategy=selected_strategy,
                top_k=max(request.retrieval_config.top_k_values),
                rerank_enabled=request.retrieval_config.rerank_enabled,
                lightrag_query_mode=request.retrieval_config.query_mode,
            )
        )
        metrics = calculate_retrieval_metrics(
            retrieval.evidence,
            item.relevant_document_ids,
            item.relevant_chunk_ids,
            request.retrieval_config.top_k_values,
        )
        metrics = {
            **metrics,
            "strategy": str(
                selected_strategy.value
                if hasattr(selected_strategy, "value")
                else selected_strategy
            ),
        }
        status = str(metrics["status"])
        if status == "completed":
            max_k = max(request.retrieval_config.top_k_values)
            score = mean(
                [
                    float(metrics["mrr"]),
                    float(metrics[f"hit_at_{max_k}"]),
                    float(metrics[f"recall_at_{max_k}"]),
                ]
            )
            passed = bool(metrics[f"hit_at_{max_k}"])
        else:
            score = 0.0
            passed = False
        return EvalResult(
            eval_run_id=run_id,
            retrieval_eval_item_id=item.id,
            question=item.query,
            retrieved_sources=[trace.model_dump(mode="json") for trace in retrieval.trace],
            retrieval_metrics=metrics,
            ragas_metrics={"status": "skipped", "reason": "not_requested"},
            type_statuses={"retrieval": status},
            score=score,
            passed=passed,
            latency_ms=retrieval.latency_ms,
        )

    def _case_knowledge_bases(self, case: EvalCase) -> list[KnowledgeBase]:
        with Session(self.engine) as session:
            statement = select(KnowledgeBase).where(KnowledgeBase.status == "active")
            if case.category != "no_answer":
                statement = statement.where(KnowledgeBase.code == case.category)
            return [
                KnowledgeBase.model_validate(item.model_dump())
                for item in session.exec(statement).all()
            ]

    async def _run_answer_case(
        self,
        run_id: str,
        case: EvalCase,
        request: EvalRunCreate,
    ) -> EvalResult:
        knowledge_bases = self._case_knowledge_bases(case)
        if not knowledge_bases:
            raise ValueError(f"No knowledge base matches eval category: {case.category}")
        retrieval_request = RetrievalRequest(
            query=case.question,
            knowledge_base_ids=[item.id for item in knowledge_bases],
            strategy=request.retrieval_config.strategy,
            top_k=max(request.retrieval_config.top_k_values),
            rerank_enabled=request.retrieval_config.rerank_enabled,
            lightrag_query_mode=request.retrieval_config.query_mode,
        )
        pipeline_result = await self.pipeline.run(
            case.question,
            knowledge_bases,
            retrieval_request,
            enable_skill=False,
            hitl=False,  # eval auto-picks recommended ToT option; never pause
        )
        answer = pipeline_result.answer_result.answer
        answer_lower = answer.lower()
        expected_keywords = [item.lower() for item in case.expected_answer_keywords]
        if case.should_answer:
            answer_accuracy = (
                sum(keyword in answer_lower for keyword in expected_keywords)
                / len(expected_keywords)
                if expected_keywords
                else float(pipeline_result.answer_result.confidence_score > 0)
            )
            expected_sources = {item.lower() for item in case.expected_source_documents}
            retrieved_titles = {
                (item.document_title or "").lower()
                for item in pipeline_result.retrieval_result.evidence
            }
            citation_hit_rate = (
                len(expected_sources & retrieved_titles) / len(expected_sources)
                if expected_sources
                else float(bool(retrieved_titles))
            )
            no_answer_refusal_rate = 0.0
            passed = answer_accuracy == 1.0 and citation_hit_rate == 1.0
        else:
            refused = (
                pipeline_result.answer_result.confidence_score == 0
                and not pipeline_result.retrieval_result.evidence
                and "NO_RELIABLE_EVIDENCE" in pipeline_result.compliance.warnings
            )
            answer_accuracy = float(refused)
            citation_hit_rate = float(not pipeline_result.retrieval_result.evidence)
            no_answer_refusal_rate = float(refused)
            passed = refused
        answer_metrics = {
            "answer_accuracy": answer_accuracy,
            "citation_hit_rate": citation_hit_rate,
            "no_answer_refusal_rate": no_answer_refusal_rate,
        }
        type_statuses: dict[str, str] = {}
        if "rag_answer" in request.eval_types:
            type_statuses["rag_answer"] = "completed"
        ragas_result = await self.ragas_runner.run(
            RagasEvaluationInput(
                question=case.question,
                answer=answer,
                contexts=[
                    evidence.snippet
                    for evidence in pipeline_result.retrieval_result.evidence
                ],
                reference_answer=(
                    " ".join(case.expected_answer_keywords)
                    if case.expected_answer_keywords
                    else None
                ),
            ),
            "ragas" in request.eval_types and request.ragas_config.enabled,
            metrics=request.ragas_config.metrics,
        )
        if "ragas" in request.eval_types:
            type_statuses["ragas"] = ragas_result.status
        else:
            ragas_result = ragas_result.model_copy(
                update={"status": "skipped", "reason": "not_requested"}
            )
        return EvalResult(
            eval_run_id=run_id,
            eval_case_id=case.id,
            question=case.question,
            answer=answer,
            retrieved_sources=[
                trace.model_dump(mode="json")
                for trace in pipeline_result.retrieval_result.trace
            ],
            answer_metrics=answer_metrics,
            ragas_metrics=ragas_result.model_dump(mode="json"),
            type_statuses=type_statuses,
            score=mean(answer_metrics.values()),
            passed=passed,
            latency_ms=pipeline_result.retrieval_result.latency_ms,
        )

    async def run(self, run_id: str, request: EvalRunCreate) -> None:
        with Session(self.engine) as session:
            eval_run = session.get(EvalRun, run_id)
            if eval_run is None:
                return
            retrieval_items = [
                RetrievalEvalItem.model_validate(item.model_dump())
                for item in session.exec(
                    select(RetrievalEvalItem).where(
                        col(RetrievalEvalItem.id).in_(request.retrieval_item_ids)
                    )
                ).all()
            ]
            cases = [
                EvalCase.model_validate(item.model_dump())
                for item in session.exec(
                    select(EvalCase).where(col(EvalCase.id).in_(request.case_ids))
                ).all()
            ]
            strategies_for_count = (
                list(request.compare_strategies) or [request.retrieval_config.strategy]
                if "retrieval" in request.eval_types
                else []
            )
            # Deduplicate while preserving order.
            ordered_count: list[Any] = []
            for strategy in strategies_for_count:
                if strategy not in ordered_count:
                    ordered_count.append(strategy)
            strategy_multiplier = max(len(ordered_count), 1 if "retrieval" in request.eval_types else 0)
            eval_run.status = "running"
            eval_run.total_cases = len(retrieval_items) * strategy_multiplier + len(cases)
            eval_run.started_at = utc_now()
            session.add(eval_run)
            session.commit()

        results: list[EvalResult] = []
        retrieval_metric_sets: list[dict[str, Any]] = []
        answer_metric_sets: list[dict[str, Any]] = []
        failed_cases = 0
        skipped_cases = 0

        if "retrieval" in request.eval_types:
            strategies = list(request.compare_strategies) or [request.retrieval_config.strategy]
            # Primary strategy first for default aggregate metrics.
            primary = request.retrieval_config.strategy
            ordered = [primary] + [s for s in strategies if s != primary]
            per_strategy_sets: dict[str, list[dict[str, Any]]] = {
                str(getattr(s, "value", s)): [] for s in ordered
            }
            for strategy in ordered:
                strategy_key = str(getattr(strategy, "value", strategy))
                for item in retrieval_items:
                    try:
                        result = await self._run_retrieval_item(
                            run_id, item, request, strategy=strategy
                        )
                    except Exception as exc:
                        failed_cases += 1
                        result = EvalResult(
                            eval_run_id=run_id,
                            retrieval_eval_item_id=item.id,
                            question=item.query,
                            error_message=str(exc),
                            retrieval_metrics={"strategy": strategy_key, "status": "failed"},
                            ragas_metrics={"status": "skipped", "reason": "case_failed"},
                            type_statuses={"retrieval": "failed"},
                        )
                    if result.retrieval_metrics:
                        if result.type_statuses.get("retrieval") == "completed":
                            per_strategy_sets[strategy_key].append(result.retrieval_metrics)
                            if strategy == primary:
                                retrieval_metric_sets.append(result.retrieval_metrics)
                        elif result.type_statuses.get("retrieval") == "skipped":
                            skipped_cases += 1
                    results.append(result)

        if {"rag_answer", "ragas"} & set(request.eval_types):
            for case in cases:
                try:
                    result = await self._run_answer_case(run_id, case, request)
                except Exception as exc:
                    failed_cases += 1
                    statuses = {
                        eval_type: "failed"
                        for eval_type in request.eval_types
                        if eval_type in {"rag_answer", "ragas"}
                    }
                    result = EvalResult(
                        eval_run_id=run_id,
                        eval_case_id=case.id,
                        question=case.question,
                        error_message=str(exc),
                        ragas_metrics={"status": "skipped", "reason": "case_failed"},
                        type_statuses=statuses,
                    )
                if result.answer_metrics:
                    answer_metric_sets.append(result.answer_metrics)
                if result.type_statuses and all(
                    status == "skipped" for status in result.type_statuses.values()
                ):
                    skipped_cases += 1
                results.append(result)

        completed_cases = sum(
            any(status == "completed" for status in result.type_statuses.values())
            for result in results
        )
        aggregate: dict[str, Any] = {
            "total_cases": len(results),
            "completed_cases": completed_cases,
            "failed_cases": failed_cases,
            "skipped_cases": skipped_cases,
        }
        aggregate.update(_average_numeric_metrics(retrieval_metric_sets))
        aggregate.update(_average_numeric_metrics(answer_metric_sets))
        # Rank histogram explains collapsed Hit@1==Hit@5==Hit@10 (all rank-1 or miss).
        if retrieval_metric_sets:
            rank_hist: dict[str, int] = {}
            mid_rank_hits = 0
            for metrics in retrieval_metric_sets:
                first_rank = metrics.get("first_relevant_rank")
                key = "miss" if first_rank in (None, 0) else str(int(first_rank))
                rank_hist[key] = rank_hist.get(key, 0) + 1
                if isinstance(first_rank, int | float) and 1 < int(first_rank) <= 10:
                    mid_rank_hits += 1
            aggregate["first_rank_histogram"] = rank_hist
            aggregate["mid_rank_hits"] = mid_rank_hits
        if "retrieval" in request.eval_types:
            strategies = list(request.compare_strategies) or [request.retrieval_config.strategy]
            if len(strategies) > 1 or request.compare_strategies:
                comparison: dict[str, Any] = {}
                # Rebuild per-strategy aggregates from results for honesty even if loop vars change.
                buckets: dict[str, list[dict[str, Any]]] = {}
                for result in results:
                    metrics = result.retrieval_metrics or {}
                    strategy_name = str(metrics.get("strategy") or "unknown")
                    if result.type_statuses.get("retrieval") == "completed":
                        buckets.setdefault(strategy_name, []).append(metrics)
                for strategy_name, metric_sets in buckets.items():
                    comparison[strategy_name] = _average_numeric_metrics(metric_sets)
                aggregate["strategy_comparison"] = comparison

        if completed_cases:
            status = "success"
        elif failed_cases:
            status = "failed"
        else:
            status = "skipped"

        with Session(self.engine) as session:
            for result in results:
                session.add(result)
            session.commit()

        with Session(self.engine) as session:
            eval_run = session.get(EvalRun, run_id)
            if eval_run is not None:
                eval_run.status = status
                eval_run.metrics = aggregate
                eval_run.error_summary = (
                    f"{failed_cases} evaluation case(s) failed" if failed_cases else None
                )
                eval_run.finished_at = utc_now()
                session.add(eval_run)
                session.commit()
