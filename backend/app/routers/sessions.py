"""Sessions — one per batch of clothes.

Garments live inside a session and stay there. Avatars and look templates do not:
they are persistent and shared across every session, which is the whole reason the
two tiers are separated.
"""
import shutil
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..config import STORAGE_DIR
from ..core.extractor import IMAGE_EXTS, SKIP_PATTERNS
from ..db import get_db, row_to_dict, rows_to_dicts

router = APIRouter(prefix="/sessions", tags=["sessions"])
UPLOAD_DIR = STORAGE_DIR / "uploads"


class SessionCreate(BaseModel):
    name: str
    notes: Optional[str] = None


class SessionUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[str] = None


@router.get("")
def list_sessions():
    with get_db() as conn:
        sessions = rows_to_dicts(conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC, id DESC").fetchall())
        for s in sessions:
            row = conn.execute(
                """SELECT COUNT(*) n,
                          SUM(CASE WHEN status='generated' THEN 1 ELSE 0 END) generated
                   FROM garments WHERE session_id=?""", (s["id"],)).fetchone()
            s["garment_count"] = row["n"] or 0
            s["generated_count"] = row["generated"] or 0
            s["passed_count"] = conn.execute(
                """SELECT COUNT(*) n FROM qc_results q
                   JOIN generations g ON g.id = q.generation_id
                   JOIN garments gar ON gar.id = g.garment_id
                   WHERE gar.session_id=? AND q.overall_pass=1""", (s["id"],)).fetchone()["n"]
            # A few finished shots to show on the card — a session of photographs
            # should be recognisable at a glance, not a row of text.
            s["preview"] = [r["output_path"] for r in conn.execute(
                """SELECT g.output_path FROM generations g
                   JOIN garments gar ON gar.id = g.garment_id
                   WHERE gar.session_id=? AND g.status='done' AND g.output_path IS NOT NULL
                   ORDER BY g.id LIMIT 4""", (s["id"],)).fetchall()]
            if not s["preview"]:
                s["preview"] = [r["path"] for r in conn.execute(
                    """SELECT i.path FROM garment_images i
                       JOIN garments gar ON gar.id = i.garment_id
                       WHERE gar.session_id=? AND (i.role IS NULL OR i.role != 'irrelevant')
                       ORDER BY i.garment_id, i.sort_order LIMIT 4""", (s["id"],)).fetchall()]
    return sessions


@router.post("")
def create_session(payload: SessionCreate):
    with get_db() as conn:
        cur = conn.execute("INSERT INTO sessions (name, notes) VALUES (?,?)",
                           (payload.name, payload.notes))
        return row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE id=?", (cur.lastrowid,)).fetchone())


@router.get("/{session_id}")
def get_session(session_id: int):
    with get_db() as conn:
        session = row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())
        if not session:
            raise HTTPException(404, "session not found")
        garments = rows_to_dicts(conn.execute(
            "SELECT * FROM garments WHERE session_id=? ORDER BY id", (session_id,)).fetchall())
        for g in garments:
            g["image_count"] = conn.execute(
                "SELECT COUNT(*) n FROM garment_images WHERE garment_id=?",
                (g["id"],)).fetchone()["n"]
            g["thumbnail"] = (conn.execute(
                """SELECT path FROM garment_images WHERE garment_id=?
                   ORDER BY CASE role WHEN 'full_front' THEN 0 ELSE 1 END, sort_order, id LIMIT 1""",
                (g["id"],)).fetchone() or {"path": None})["path"]
            g["look_count"] = conn.execute(
                "SELECT COUNT(*) n FROM looks WHERE garment_id=?", (g["id"],)).fetchone()["n"]
            g["passed_count"] = conn.execute(
                """SELECT COUNT(DISTINCT l.id) n FROM looks l
                   JOIN generations gen ON gen.look_id=l.id
                   JOIN qc_results q ON q.generation_id=gen.id
                   WHERE l.garment_id=? AND q.overall_pass=1""", (g["id"],)).fetchone()["n"]
        session["garments"] = garments
    return session


@router.get("/{session_id}/progress")
def session_progress(session_id: int):
    """Live counts for a running batch, so the UI can show progress instead of
    leaving a click with no visible consequence."""
    with get_db() as conn:
        row = conn.execute(
            """SELECT
                 SUM(CASE WHEN g.status IN ('pending','running') THEN 1 ELSE 0 END) in_flight,
                 SUM(CASE WHEN g.status='done'  THEN 1 ELSE 0 END) done,
                 SUM(CASE WHEN g.status='error' THEN 1 ELSE 0 END) failed
               FROM generations g JOIN garments gar ON gar.id = g.garment_id
               WHERE gar.session_id=?""", (session_id,)).fetchone()
        pending_looks = conn.execute(
            """SELECT COUNT(*) n FROM looks l JOIN garments g ON g.id = l.garment_id
               WHERE g.session_id=? AND g.avatar_id IS NOT NULL
                 AND l.id NOT IN (SELECT look_id FROM generations WHERE status='done')""",
            (session_id,)).fetchone()["n"]
    return {"in_flight": row["in_flight"] or 0, "done": row["done"] or 0,
            "failed": row["failed"] or 0, "not_yet_generated": pending_looks}


@router.get("/{session_id}/shots")
def session_shots(session_id: int):
    """Every finished shot in the session, newest attempt per look.

    A session is ultimately a set of photographs, so it needs to be viewable as
    one — not only as a list of garments to drill into.
    """
    with get_db() as conn:
        rows = conn.execute(
            """SELECT g.id, g.output_path, g.look_id, gar.name garment_name,
                      gar.category, gar.id garment_id, l.text look_text,
                      q.overall_pass, q.confirmed
               FROM generations g
               JOIN garments gar ON gar.id = g.garment_id
               JOIN looks l ON l.id = g.look_id
               LEFT JOIN qc_results q ON q.id = (
                   SELECT id FROM qc_results WHERE generation_id = g.id ORDER BY id DESC LIMIT 1)
               WHERE gar.session_id = ? AND g.status='done' AND g.output_path IS NOT NULL
                 AND g.attempt_no = (
                     SELECT MAX(attempt_no) FROM generations WHERE look_id = g.look_id
                       AND status='done')
               ORDER BY gar.category, gar.name, l.sort_order""", (session_id,)).fetchall()
    return rows_to_dicts(rows)


@router.patch("/{session_id}")
def update_session(session_id: int, payload: SessionUpdate):
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    with get_db() as conn:
        conn.execute(f"UPDATE sessions SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     (*fields.values(), session_id))
        return row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())


def _session_files(conn, session_id: int) -> dict:
    """Files this session owns, and would therefore lose on deletion.

    Source photography is reference-counted: the bundled sample data is shared,
    and deleting one session must not pull images out from under another.
    Avatars are never included — they are persistent by design and outlive every
    session that used them.
    """
    outputs = [r["output_path"] for r in conn.execute(
        """SELECT g.output_path FROM generations g
           JOIN garments gar ON gar.id = g.garment_id
           WHERE gar.session_id=? AND g.output_path IS NOT NULL""", (session_id,)).fetchall()]

    mine = {r["path"] for r in conn.execute(
        """SELECT i.path FROM garment_images i
           JOIN garments g ON g.id = i.garment_id
           WHERE g.session_id=?""", (session_id,)).fetchall()}
    shared = {r["path"] for r in conn.execute(
        """SELECT i.path FROM garment_images i
           JOIN garments g ON g.id = i.garment_id
           WHERE g.session_id!=?""", (session_id,)).fetchall()}
    sources = sorted(mine - shared)

    upload_dir = UPLOAD_DIR / f"session_{session_id}"
    return {"outputs": outputs, "sources": sources,
            "upload_dir": str(upload_dir) if upload_dir.exists() else None}


@router.get("/{session_id}/deletion-preview")
def deletion_preview(session_id: int):
    """What deleting this session would destroy. Generated shots cost credits, so
    the number is shown before the button is pressed rather than after."""
    with get_db() as conn:
        session = row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone())
        if not session:
            raise HTTPException(404, "session not found")
        counts = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM garments WHERE session_id=?) garments,
                 (SELECT COUNT(*) FROM generations g JOIN garments gar ON gar.id=g.garment_id
                   WHERE gar.session_id=? AND g.status='done') shots,
                 (SELECT COUNT(*) FROM looks l JOIN garments gar ON gar.id=l.garment_id
                   WHERE gar.session_id=?) looks""", (session_id,) * 3).fetchone()
        files = _session_files(conn, session_id)

    paths = [p for p in files["outputs"] + files["sources"] if p]
    size = sum(Path(p).stat().st_size for p in paths if Path(p).exists())
    return {"name": session["name"], "garments": counts["garments"],
            "shots": counts["shots"], "looks": counts["looks"],
            "files": len(paths), "bytes": size,
            # Named so the UI can say what survives: these are the things people
            # worry about losing and they are not touched.
            "kept": ["models", "saved looks", "learned lessons"]}


@router.delete("/{session_id}")
def delete_session(session_id: int, delete_files: bool = True):
    """Delete a session and everything scoped to it.

    Rows cascade. Files are removed too unless `delete_files=false`, because
    otherwise storage grows forever with images nothing references. Models, look
    templates and lessons are persistent and survive.
    """
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise HTTPException(404, "session not found")
        files = _session_files(conn, session_id) if delete_files else None
        conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    removed = 0
    if files:
        for path in files["outputs"] + files["sources"]:
            try:
                if path and Path(path).is_file():
                    Path(path).unlink()
                    removed += 1
            except OSError:
                pass          # a file we cannot remove must not fail the delete
        if files["upload_dir"]:
            shutil.rmtree(files["upload_dir"], ignore_errors=True)

    return {"deleted": session_id, "files_removed": removed}


@router.post("/{session_id}/garments")
async def upload_garment(
    session_id: int,
    name: str = Form(...),
    category: Optional[str] = Form(None),
    size_variant: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
):
    """Create one garment from a set of product photos.

    Obvious non-garment shots (wash-care labels, barcodes) are skipped by filename
    here; the analyzer independently classifies anything that slips through as
    irrelevant, so a missed one costs accuracy nothing.
    """
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise HTTPException(404, "session not found")
        cur = conn.execute(
            "INSERT INTO garments (session_id, name, category, size_variant) VALUES (?,?,?,?)",
            (session_id, name, category, size_variant))
        garment_id = cur.lastrowid

    dest = UPLOAD_DIR / f"session_{session_id}" / f"garment_{garment_id}"
    dest.mkdir(parents=True, exist_ok=True)

    saved = 0
    with get_db() as conn:
        for order, upload in enumerate(files):
            filename = Path(upload.filename or "").name
            if not filename or Path(filename).suffix.lower() not in IMAGE_EXTS:
                continue
            if SKIP_PATTERNS.search(filename):
                continue
            target = dest / filename
            with target.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh)
            conn.execute(
                "INSERT INTO garment_images (garment_id, path, filename, sort_order) VALUES (?,?,?,?)",
                (garment_id, str(target), filename, order))
            saved += 1

    if not saved:
        with get_db() as conn:
            conn.execute("DELETE FROM garments WHERE id=?", (garment_id,))
        raise HTTPException(400, "no usable images in upload")

    return {"garment_id": garment_id, "images_saved": saved}


@router.post("/{session_id}/import-folder")
def import_folder(session_id: int, root: str, category: Optional[str] = None):
    """Register garments from a folder already on disk, one garment per subfolder.

    Files are referenced in place rather than copied — these shoots run to hundreds
    of megabytes and duplicating them buys nothing.
    """
    root_path = Path(root).expanduser().resolve()
    if not root_path.is_dir():
        raise HTTPException(400, f"not a directory: {root_path}")

    created = []
    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (session_id,)).fetchone():
            raise HTTPException(404, "session not found")

        for sub in sorted(p for p in root_path.iterdir() if p.is_dir()):
            images = [p for p in sorted(sub.iterdir())
                      if p.suffix.lower() in IMAGE_EXTS and not SKIP_PATTERNS.search(p.name)]
            if not images:
                continue
            cur = conn.execute(
                "INSERT INTO garments (session_id, name, category) VALUES (?,?,?)",
                (session_id, sub.name, category))
            gid = cur.lastrowid
            for order, img in enumerate(images):
                conn.execute(
                    "INSERT INTO garment_images (garment_id, path, filename, sort_order) "
                    "VALUES (?,?,?,?)", (gid, str(img), img.name, order))
            created.append({"garment_id": gid, "name": sub.name, "images": len(images)})

    if not created:
        raise HTTPException(400, "no garment subfolders with images found")
    return {"created": created}
