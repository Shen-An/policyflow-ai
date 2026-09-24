"""Server-sent-event delivery for durable runs (Phase 4 / US2).

Two pieces cooperate so an SSE stream survives instance restarts and slow
clients: :mod:`.stream` is the cross-instance, resumable event log backed by a
bounded Redis Stream; :mod:`.channel` is the per-connection bounded fan-out that
turns backpressure into a clean disconnect instead of unbounded buffering.
"""
