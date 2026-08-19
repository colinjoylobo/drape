"""Drape — branding-shoot generation for clothing catalogues."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .config import ALLOWED_ROOT, STORAGE_DIR, missing_credentials
from .db import init_db
from .routers import avatars, export, garments, generations, library, sessions

app = FastAPI(title="Drape", version="0.1.0")

# Local-only tool; the frontend dev server is the sole client.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()
    missing = missing_credentials()
    if missing:
        # Loud but non-fatal: analysis and browsing still work without generation
        # credentials, and failing to boot would hide which credential is absent.
        print(f"[Drape] WARNING — generation will fail, missing credentials: {', '.join(missing)}")


@app.get("/api/health")
def health():
    return {"ok": True, "app": "Drape"}


@app.get("/api/file")
def serve_file(path: str):
    """Serve an image by absolute path.

    Uploads keep their original location on disk rather than being copied into the
    app, so the frontend needs to read arbitrary paths. Restricted to files under
    storage or an explicitly configured root, so a stray path cannot walk the filesystem.
    """
    p = Path(path).resolve()
    allowed_roots = [STORAGE_DIR.resolve()]
    if ALLOWED_ROOT:
        allowed_roots.append(ALLOWED_ROOT.resolve())
    if not any(str(p).startswith(str(root)) for root in allowed_roots):
        return {"error": "path not allowed"}
    if not p.is_file():
        return {"error": "not found"}
    return FileResponse(p)


for r in (sessions.router, garments.router, avatars.router,
          generations.router, library.router, export.router):
    app.include_router(r, prefix="/api")
