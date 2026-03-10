"""Serve the built frontend from the FastAPI process."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


class ImmutableStaticFiles(StaticFiles):
    """Static files with long-lived caching for content-hashed assets."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_frontend(application: FastAPI, dist_directory: Path) -> bool:
    """Mount a Vite build and register SPA fallback routes when it exists."""
    dist = dist_directory.resolve()
    index = dist / "index.html"
    if not index.is_file():
        return False

    assets = dist / "assets"
    if assets.is_dir():
        application.mount(
            "/assets",
            ImmutableStaticFiles(directory=assets),
            name="frontend-assets",
        )

    def index_response() -> FileResponse:
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    @application.get("/", include_in_schema=False)
    async def frontend_index() -> FileResponse:
        return index_response()

    @application.get("/{full_path:path}", include_in_schema=False)
    async def frontend_fallback(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = (dist / full_path).resolve()
        if candidate.is_relative_to(dist) and candidate.is_file():
            return FileResponse(candidate, headers={"Cache-Control": "no-cache"})
        if Path(full_path).suffix:
            raise HTTPException(status_code=404, detail="Not Found")
        return index_response()

    return True
