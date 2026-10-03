"""T065/T070 [US2] app lifespan wires the outbox relay when a broker is configured.

The relay loop is what carries committed ``job.*`` outbox rows to RabbitMQ in a
broker-backed deployment. These sync TestClient tests assert the application
lifespan starts it exactly when ``CELERY_BROKER_URL`` is set, publishes it on
``app.state`` for introspection, and stops it cleanly on shutdown (the context
manager exit must not hang on the loop).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlmodel import SQLModel

from backend.app.core.config import Settings
from backend.app.db.models import Tenant
from backend.app.main import create_app

TENANT = "11111111-1111-1111-1111-111111111111"


def _make_db() -> str:
    path = Path(tempfile.mkdtemp(), "relay-wiring.db").resolve().as_posix()
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT, code="acme", name="Acme"))
        session.commit()
    engine.dispose()
    return url


def test_lifespan_starts_relay_when_broker_configured(broker_url) -> None:
    settings = Settings(
        DATABASE_URL=_make_db(), CELERY_BROKER_URL=broker_url, _env_file=None
    )
    app = create_app(settings=settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.outbox_relay is not None  # loop started
    # Clean exit here means lifespan shutdown stopped the loop without hanging.


def test_lifespan_skips_relay_without_broker() -> None:
    settings = Settings(DATABASE_URL=_make_db(), CELERY_BROKER_URL=None, _env_file=None)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.outbox_relay is None  # no broker -> no loop
