"""Evaluation history exposes immutable retrieval and rerank choices."""

from backend.app.services.eval_service import _run_retrieval_summary


def test_retrieval_summary_reports_cross_encoder() -> None:
    strategy, enabled, method, backend = _run_retrieval_summary(
        {
            "retrieval_config": {
                "strategy": "hybrid_lightrag_bm25",
                "rerank_enabled": True,
                "reranker_method": "cross_encoder",
            },
            "reranker_backend": "cross_encoder",
        }
    )

    assert strategy == "hybrid_lightrag_bm25"
    assert enabled is True
    assert method == "cross_encoder"
    assert backend == "cross_encoder"


def test_retrieval_summary_reports_disabled_rerank_even_with_default_method() -> None:
    strategy, enabled, method, backend = _run_retrieval_summary(
        {
            "retrieval_config": {
                "strategy": "bm25_only",
                "rerank_enabled": False,
                "reranker_method": "local_lexical_fusion",
            },
            "reranker_backend": "local",
        }
    )

    assert strategy == "bm25_only"
    assert enabled is False
    assert method is None
    assert backend is None
