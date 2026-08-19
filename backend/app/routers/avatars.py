"""Avatars — persistent across every session.

Avatars are archived rather than deleted: past generations reference them, and a
hard delete would leave finished work unable to explain who it depicts.
"""
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..config import DEFAULT_PROMPT_PROFILE, STORAGE_DIR
from ..core.avatars import build_avatar_prompt, generate_avatar
from ..db import get_db, row_to_dict, rows_to_dicts

router = APIRouter(prefix="/avatars", tags=["avatars"])
AVATAR_DIR = STORAGE_DIR / "avatars"


class AvatarCreate(BaseModel):
    name: str
    description: str
    category: Optional[str] = None
    size_variant: Optional[str] = None
    with_back: bool = True
    profile: str = DEFAULT_PROMPT_PROFILE


class AvatarUpdate(BaseModel):
    name: Optional[str] = None
    styling: Optional[str] = None
    category: Optional[str] = None
    size_variant: Optional[str] = None
    notes: Optional[str] = None
    archived: Optional[bool] = None


@router.get("")
def list_avatars(include_archived: bool = False):
    with get_db() as conn:
        sql = "SELECT * FROM avatars"
        if not include_archived:
            sql += " WHERE archived=0"
        sql += " ORDER BY created_at DESC, id DESC"
        return rows_to_dicts(conn.execute(sql).fetchall())


@router.post("/preview-prompt")
def preview_prompt(payload: AvatarCreate):
    """Show the exact avatar prompt before spending anything on it."""
    return {"prompt": build_avatar_prompt(payload.description, payload.category, payload.profile)}


@router.post("")
def create_avatar(payload: AvatarCreate):
    result = generate_avatar(description=payload.description, name=payload.name,
                             category=payload.category, with_back=payload.with_back,
                             profile=payload.profile)
    if result.get("error"):
        raise HTTPException(502, result["error"])

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO avatars (name, category, size_variant, front_path, back_path, prompt, notes)
               VALUES (?,?,?,?,?,?,?)""",
            (payload.name, payload.category, payload.size_variant, result["front_path"],
             result.get("back_path"), result["prompt"], result.get("back_error")))
        avatar = row_to_dict(conn.execute("SELECT * FROM avatars WHERE id=?",
                                          (cur.lastrowid,)).fetchone())
    avatar["back_error"] = result.get("back_error")
    return avatar


@router.post("/upload")
async def upload_avatar(
    name: str = Form(...),
    category: Optional[str] = Form(None),
    size_variant: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    front: UploadFile = File(...),
    back: Optional[UploadFile] = File(None),
):
    """Register an avatar from existing images — how avatars made outside Drape
    (or in an earlier pipeline) get brought in."""
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    slug = name.lower().replace(" ", "_")

    def save(upload: UploadFile, suffix: str) -> str:
        ext = Path(upload.filename or "x.png").suffix or ".png"
        target = AVATAR_DIR / f"{slug}_{suffix}{ext}"
        with target.open("wb") as fh:
            shutil.copyfileobj(upload.file, fh)
        return str(target)

    front_path = save(front, "front")
    back_path = save(back, "back") if back else None

    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO avatars (name, category, size_variant, front_path, back_path, notes)
               VALUES (?,?,?,?,?,?)""",
            (name, category, size_variant, front_path, back_path, notes))
        return row_to_dict(conn.execute("SELECT * FROM avatars WHERE id=?",
                                        (cur.lastrowid,)).fetchone())


@router.patch("/{avatar_id}")
def update_avatar(avatar_id: int, payload: AvatarUpdate):
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    if "archived" in fields:
        fields["archived"] = int(bool(fields["archived"]))
    with get_db() as conn:
        conn.execute(f"UPDATE avatars SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     (*fields.values(), avatar_id))
        return row_to_dict(conn.execute("SELECT * FROM avatars WHERE id=?",
                                        (avatar_id,)).fetchone())
