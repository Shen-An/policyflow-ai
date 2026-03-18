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
    ) -> EvalResult:
        retrieval = await self.rag_service.retrieve(
            RetrievalRequest(
                query=item.query,
                knowledge_base_ids=item.knowledge_base_ids,
                strategy=request.retrieval_config.strategy,
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
            ),
            "ragas" in request.eval_types and request.ragas_config.enabled,
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
            eval_run.status = "running"
            eval_run.total_cases = len(retrieval_items) + len(cases)
            eval_run.started_at = utc_now()
            session.add(eval_run)
            session.commit()

        results: list[EvalResult] = []
        retrieval_metric_sets: list[dict[str, Any]] = []
        answer_metric_sets: list[dict[str, Any]] = []
        failed_cases = 0
        skipped_cases = 0

        if "retrieval" in request.eval_types:
            for item in retrieval_items:
                try:
                    result = await self._run_retrieval_item(run_id, item, request)
                except Exception as exc:
                    failed_cases += 1
                    result = EvalResult(
                        eval_run_id=run_id,
                        retrieval_eval_item_id=item.id,
                        question=item.query,
                        error_message=str(exc),
                        ragas_metrics={"status": "skipped", "reason": "case_failed"},
                        type_statuses={"retrieval": "failed"},
                    )
                if result.retrieval_metrics:
                    if result.type_statuses.get("retrieval") == "completed":
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
