"""Serve the built frontend (frontend/dist) from the same origin as the API.

Mounted only when FRONTEND_DIST is set. Unknown /api/ paths keep the JSON 404; an existing
file is served as a file; every other GET returns index.html so that react-router deep links
such as /windows/W-123 open on a page reload.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def frontend_root(frontend_dist: str) -> Path:
    """The resolved dist folder; raises at startup when it holds no index.html."""
    root = Path(frontend_dist).resolve()
    if not (root / "index.html").is_file():
        raise ValueError(
            f"FRONTEND_DIST must be a folder with index.html, got {frontend_dist!r}; "
            "run `npm run build` in frontend/ or leave FRONTEND_DIST empty"
        )
    return root


def mount_frontend(app: FastAPI, frontend_dist: str) -> None:
    """Add the catch-all GET route. Call it after every API router is included."""
    root = frontend_root(frontend_dist)
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise StarletteHTTPException(status_code=404)
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise StarletteHTTPException(status_code=404)
        if target.is_file():
            return FileResponse(target)
        return FileResponse(index)
