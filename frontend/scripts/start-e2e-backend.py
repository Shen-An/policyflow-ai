from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = PROJECT_ROOT / "frontend" / ".tmp" / "e2e-backend"
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

os.environ["DATABASE_URL"] = f"sqlite:///{(RUNTIME_DIR / 'policyflow-e2e.db').as_posix()}"
os.environ["LOG_DIR"] = str(RUNTIME_DIR / "logs")
os.environ["UPLOAD_DIR"] = str(RUNTIME_DIR / "uploads")
os.environ["RAG_WORKSPACE_DIR"] = str(RUNTIME_DIR / "rag-workspaces")
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "frontend-e2e-only"
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402

settings = Settings(_env_file=None)

if __name__ == "__main__":
    uvicorn.run(create_app(settings), host="127.0.0.1", port=8000, log_level="warning")
