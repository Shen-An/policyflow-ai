"""Unified frontend hosting and launcher tests."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import start as launcher
from backend.app.core.config import Settings
from backend.app.main import create_app


def test_fastapi_serves_vite_build_and_spa_fallback(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>PolicyFlow shell</body></html>", encoding="utf-8")
    (assets / "app-hash.js").write_text("console.log('app')", encoding="utf-8")
    settings = Settings(
        DATABASE_URL=f"sqlite:///{(tmp_path / 'app.db').as_posix()}",
        LOG_DIR=tmp_path / "logs",
        SECRET_KEY="test-secret",
        BOOTSTRAP_ADMIN_PASSWORD="test-password",
        _env_file=None,
    )
    app = create_app(settings, frontend_dist=dist)

    with TestClient(app) as client:
        root = client.get("/")
        deep_link = client.get("/admin/skills")
        asset = client.get("/assets/app-hash.js")
        api_missing = client.get("/api/does-not-exist")
        file_missing = client.get("/missing.js")
        health = client.get("/health")

    assert root.status_code == 200
    assert root.headers["cache-control"] == "no-store"
    assert "PolicyFlow shell" in root.text
    assert deep_link.status_code == 200
    assert "PolicyFlow shell" in deep_link.text
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert api_missing.status_code == 404
    assert "PolicyFlow shell" not in api_missing.text
    assert file_missing.status_code == 404
    assert health.json() == {"status": "ok"}


def test_launcher_detects_missing_and_stale_frontend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frontend = tmp_path / "frontend"
    source = frontend / "src"
    dist = frontend / "dist"
    source.mkdir(parents=True)
    dist.mkdir()
    source_file = source / "main.tsx"
    source_file.write_text("initial", encoding="utf-8")

    monkeypatch.setattr(launcher, "FRONTEND_ROOT", frontend)
    monkeypatch.setattr(launcher, "FRONTEND_DIST", dist)
    monkeypatch.setattr(launcher, "FRONTEND_INDEX", dist / "index.html")

    assert launcher.frontend_needs_build() is True
    (dist / "index.html").write_text("built", encoding="utf-8")
    output_time = (dist / "index.html").stat().st_mtime
    os.utime(source_file, (output_time + 5, output_time + 5))
    assert source_file.stat().st_mtime > output_time
    assert launcher.frontend_needs_build() is True
