"""Durable background job orchestration (Phase 4 / US2).

The transactional-outbox job state machine lives in :mod:`.service`; Celery
wiring and the outbox publisher are added alongside it.
"""
