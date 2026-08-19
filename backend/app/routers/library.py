"""The Look Style Library — proven looks, kept across sessions.

A look earns its place here by having produced an image that passed QC. That is
the whole mechanism by which the tool gets better with use: the art director is
shown this library as grounding for what the category's bar looks like, and is
told which of its settings are already spoken for.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..db import get_db, row_to_dict, rows_to_dicts

router = APIRouter(prefix="/library", tags=["library"])


class TemplateCreate(BaseModel):
    category: str
    text: str
    scene_tag: Optional[str] = None
    source_generation_id: Optional[int] = None


class TemplateUpdate(BaseModel):
    text: Optional[str] = None
    scene_tag: Optional[str] = None
    category: Optional[str] = None


@router.get("")
def list_templates(category: Optional[str] = None):
    with get_db() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM look_templates WHERE category=? ORDER BY times_used DESC, id DESC",
                (category,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM look_templates ORDER BY category, times_used DESC, id DESC").fetchall()
        return rows_to_dicts(rows)


@router.post("")
def create_template(payload: TemplateCreate):
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO look_templates (category, text, scene_tag, source_generation_id) "
            "VALUES (?,?,?,?)",
            (payload.category, payload.text, payload.scene_tag, payload.source_generation_id))
        return row_to_dict(conn.execute("SELECT * FROM look_templates WHERE id=?",
                                        (cur.lastrowid,)).fetchone())


@router.post("/promote/{generation_id}")
def promote_generation(generation_id: int, scene_tag: Optional[str] = None):
    """Save the look behind a QC-passed generation into its category's library."""
    with get_db() as conn:
        row = row_to_dict(conn.execute(
            """SELECT g.id, l.text look_text, gar.category, q.overall_pass
               FROM generations g
               JOIN looks l ON l.id = g.look_id
               JOIN garments gar ON gar.id = g.garment_id
               LEFT JOIN qc_results q ON q.generation_id = g.id
               WHERE g.id=? ORDER BY q.id DESC LIMIT 1""", (generation_id,)).fetchone())
        if not row:
            raise HTTPException(404, "generation not found")
        if not row.get("category"):
            raise HTTPException(400, "the garment has no category, so there is nothing to file "
                                     "this look under")
        if not row.get("overall_pass"):
            raise HTTPException(400, "only a look that produced a QC-passed image can be promoted — "
                                     "the library is meant to hold what actually worked")

        existing = conn.execute(
            "SELECT id FROM look_templates WHERE category=? AND text=?",
            (row["category"], row["look_text"])).fetchone()
        if existing:
            return row_to_dict(conn.execute("SELECT * FROM look_templates WHERE id=?",
                                            (existing["id"],)).fetchone())

        cur = conn.execute(
            "INSERT INTO look_templates (category, text, scene_tag, source_generation_id) "
            "VALUES (?,?,?,?)", (row["category"], row["look_text"], scene_tag, generation_id))
        return row_to_dict(conn.execute("SELECT * FROM look_templates WHERE id=?",
                                        (cur.lastrowid,)).fetchone())


@router.patch("/{template_id}")
def update_template(template_id: int, payload: TemplateUpdate):
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    with get_db() as conn:
        conn.execute(
            f"UPDATE look_templates SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
            (*fields.values(), template_id))
        return row_to_dict(conn.execute("SELECT * FROM look_templates WHERE id=?",
                                        (template_id,)).fetchone())


@router.delete("/{template_id}")
def delete_template(template_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM look_templates WHERE id=?", (template_id,))
    return {"deleted": template_id}


# ---------------------------------------------------------------------------
# Learned lessons — the failure side of the loop. The Look Library records what
# worked; this records what went wrong and the correction that fixed it.
# ---------------------------------------------------------------------------
class LessonUpdate(BaseModel):
    enabled: Optional[bool] = None
    guidance: Optional[str] = None


@router.get("/lessons")
def list_lessons():
    from ..core.lessons import all_lessons
    return all_lessons()


@router.patch("/lessons/{lesson_id}")
def update_lesson(lesson_id: int, payload: LessonUpdate):
    """Edit or switch off a learned lesson.

    A lesson is inferred from a small number of samples, so it must stay
    overridable — an over-generalised rule quietly degrading every future shot is
    worse than no rule at all.
    """
    fields = {k: v for k, v in payload.dict(exclude_unset=True).items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    if "enabled" in fields:
        fields["enabled"] = int(bool(fields["enabled"]))
    with get_db() as conn:
        conn.execute(
            f"UPDATE qc_lessons SET {', '.join(f'{k}=?' for k in fields)}, "
            f"updated_at=datetime('now') WHERE id=?", (*fields.values(), lesson_id))
        return row_to_dict(conn.execute(
            "SELECT * FROM qc_lessons WHERE id=?", (lesson_id,)).fetchone())


@router.delete("/lessons/{lesson_id}")
def delete_lesson(lesson_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM qc_lessons WHERE id=?", (lesson_id,))
    return {"deleted": lesson_id}
