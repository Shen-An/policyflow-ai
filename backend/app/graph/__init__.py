"""Unified, versioned LangGraph-style agent runtime (Stage 3, US3).

Chat, streaming Chat, Eval and the file workflow all execute through the one
typed decision path assembled here. The package intentionally keeps the state
contract (:mod:`state`), the authorized checkpoint binding
(:mod:`checkpoints`), the deterministic evidence gate (:mod:`evidence_gate`)
and the unified service surface (:mod:`service`) as separate modules so each
carries a single responsibility and can be tested in isolation.

Honesty boundary: the production PostgreSQL checkpoint saver
(``langgraph-checkpoint-postgres``) is not wired here yet — the in-memory
stores in :mod:`checkpoints` satisfy the Stage 3 contract tests and the durable
saver lands with the Stage 4 durability work.
"""

from __future__ import annotations
