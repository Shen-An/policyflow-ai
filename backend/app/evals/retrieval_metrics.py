"""Pure retrieval metric calculations."""

from typing import Any

from backend.app.schemas.retrieval import Evidence


def calculate_retrieval_metrics(
    retrieved: list[Evidence],
    relevant_document_ids: list[str],
    relevant_chunk_ids: list[str],
    top_k_values: list[int],
) -> dict[str, Any]:
    relevant_identities = {f"document:{item}" for item in relevant_document_ids}
    relevant_identities.update(f"chunk:{item}" for item in relevant_chunk_ids)
    if not relevant_identities:
        return {"status": "skipped", "reason": "empty_ground_truth"}

    deduplicated: list[Evidence] = []
    seen: set[str] = set()
    for item in retrieved:
        identity = item.chunk_id or item.document_id or item.source_id or item.snippet
        if identity not in seen:
            seen.add(identity)
            deduplicated.append(item)

    def matched_identities(item: Evidence) -> set[str]:
        identities: set[str] = set()
        if item.document_id and f"document:{item.document_id}" in relevant_identities:
            identities.add(f"document:{item.document_id}")
        if item.chunk_id and f"chunk:{item.chunk_id}" in relevant_identities:
            identities.add(f"chunk:{item.chunk_id}")
        return identities

    first_rank = next(
        (
            index
            for index, item in enumerate(deduplicated, start=1)
            if matched_identities(item)
        ),
        None,
    )
    metrics: dict[str, Any] = {
        "status": "completed",
        "mrr": 1.0 / first_rank if first_rank else 0.0,
        "first_relevant_rank": first_rank,
    }
    for top_k in sorted(set(top_k_values)):
        relevant_found = {
            identity
            for item in deduplicated[:top_k]
            for identity in matched_identities(item)
        }
        metrics[f"hit_at_{top_k}"] = 1.0 if relevant_found else 0.0
        metrics[f"recall_at_{top_k}"] = len(relevant_found) / len(relevant_identities)
    return metrics
