"""The seeded ``sys_admin`` really can reach the admin surface.

``tests/security/test_admin_rbac.py`` injects principals straight out of
``tests/support_admin.py``, so it never walks the path a fresh install walks:
bootstrap administrator -> seeded ``sys_admin`` role -> ``role.actions`` ->
principal scopes.  When :data:`backend.app.db.init_db.ROLE_SEEDS` forgot to
declare actions, every ``/api/v2/admin/*`` call in a real deployment answered
403 ``Not authorized to perform this action`` while every RBAC test stayed
green.  This module pins the end-to-end path so that regression cannot come
back silently.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.core.config import Settings
from backend.app.main import create_app

ADMIN_READS = (
    "/api/v2/admin/health",
    "/api/v2/admin/quota",
    "/api/v2/admin/roles",
    "/api/v2/admin/grants",
    "/api/v2/admin/runs",
    "/api/v2/admin/reconciliation",
)


def _settings(tmp_path) -> Settings:
    home = tmp_path / "seeded-admin"
    home.mkdir(parents=True, exist_ok=True)
    return Settings(
        ENVIRONMENT="development",
        DATABASE_URL=f"sqlite:///{(home / 'policyflow.db').as_posix()}",
        LOG_DIR=home / "logs",
        UPLOAD_DIR=home / "uploads",
        RAG_WORKSPACE_DIR=home / "rag_workspaces",
        SECRET_KEY="seeded-admin-secret",
        BOOTSTRAP_ADMIN_PASSWORD="seeded-admin-password",
        _env_file=None,
    )


def _login(client: TestClient, settings: Settings) -> dict[str, str]:
    password = settings.BOOTSTRAP_ADMIN_PASSWORD
    response = client.post(
        "/api/auth/login",
        json={
            "username": settings.BOOTSTRAP_ADMIN_USERNAME,
            "password": (
                password.get_secret_value()
                if hasattr(password, "get_secret_value")
                else password
            ),
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_bootstrap_sys_admin_reaches_every_admin_read(tmp_path) -> None:
    """A freshly seeded install must not answer 403 on the admin surface."""
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        headers = _login(client, settings)
        for path in ADMIN_READS:
            response = client.get(path, headers=headers)
            assert response.status_code == 200, f"{path} -> {response.status_code} {response.text}"
