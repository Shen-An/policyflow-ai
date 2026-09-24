"""Durable quota coordination and admission control (Phase 4 / US2).

Overload protection is atomic and shared across stateless API instances, so it
lives in Redis rather than in per-process memory. :mod:`.coordinator` bounds
request rate (token bucket) and concurrency (lease semaphore) with Lua scripts;
PostgreSQL remains the authority for quota *policies*, final usage and audit.
"""
