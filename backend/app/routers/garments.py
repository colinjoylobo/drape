"""Garments — analysis and every editable field hanging off it.

Everything the analyzer produces is a proposal the user can override. When a field
is edited by hand, `edited_by_user` is set and a later re-analysis is refused
unless explicitly forced, so a run cannot quietly discard someone's corrections.
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import DEFAULT_PROMPT_PROFILE
from ..core import pipeline
from ..core.detail_crop import make_crop
from ..core.vision import VisionError
from ..db import get_db, row_to_dict

router = APIRouter(prefix="/garments", tags=["garments"])

VALID_ROLES = {"full_front", "full_back", "close_up_detail", "flat_lay_or_other_angle", "irrelevant"}


class GarmentUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    size_variant: Optional[str] = None
    avatar_id: Optional[int] = None


class AnalysisUpdate(BaseModel):
    garment_desc: Optional[str] = None
    pieces: Optional[List[str]] = None
    coverage_risk: Optional[bool] = None
    pairing_note: Optional[str] = None
    back_has_structure: Optional[bool] = None
    detail_regions: Optional[List[Dict[str, Any]]] = None


class RoleUpdate(BaseModel):
    role: str


class LookCreate(BaseModel):
    text: str
    label: Optional[str] = None
    props: Optional[str] = None
    template_id: Optional[int] = None


class LookUpdate(BaseModel):
    text: Optional[str] = None
    label: Optional[str] = None
    # Empty string is meaningful here — it clears the props — so this is handled
    # separately from the "omitted means unchanged" fields above.
    props: Optional[str] = None


class ProposeLooks(BaseModel):
    n: int = 2
    replace: bool = False
    direction: Optional[str] = None
    profile: str = DEFAULT_PROMPT_PROFILE
    # Off by default: every shot should be its own photograph. On means the
    # opposite — keep this garment inside the same set as the rest of the shoot.
    match_existing: bool = False


@router.get("/{garment_id}")
def get_garment(garment_id: int):
    try:
        return pipeline.get_garment_detail(garment_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.patch("/{garment_id}")
def update_garment(garment_id: int, payload: GarmentUpdate):
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    with get_db() as conn:
        conn.execute(f"UPDATE garments SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     (*fields.values(), garment_id))
    return pipeline.get_garment_detail(garment_id)


@router.delete("/{garment_id}")
def delete_garment(garment_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM garments WHERE id=?", (garment_id,))
    return {"deleted": garment_id}


@router.post("/{garment_id}/analyze")
def analyze(garment_id: int, propose_looks: bool = True, n_looks: int = 2, force: bool = False):
    with get_db() as conn:
        row = conn.execute(
            "SELECT edited_by_user FROM garment_analysis WHERE garment_id=?",
            (garment_id,)).fetchone()
    if row and row["edited_by_user"] and not force:
        raise HTTPException(
            409, "this analysis has been edited by hand; re-analysing would discard those edits. "
                 "Retry with force=true to overwrite.")
    try:
        return pipeline.analyze_garment(garment_id, propose_looks=propose_looks, n_looks=n_looks)
    except (ValueError, VisionError) as e:
        raise HTTPException(400, str(e))


@router.patch("/{garment_id}/analysis")
def update_analysis(garment_id: int, payload: AnalysisUpdate):
    fields = {k: v for k, v in payload.dict().items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    for key in ("pieces", "detail_regions"):
        if key in fields:
            fields[key] = json.dumps(fields[key])
    for key in ("coverage_risk", "back_has_structure"):
        if key in fields:
            fields[key] = int(bool(fields[key]))

    with get_db() as conn:
        if not conn.execute("SELECT 1 FROM garment_analysis WHERE garment_id=?",
                            (garment_id,)).fetchone():
            raise HTTPException(404, "garment has not been analyzed yet")
        conn.execute(
            f"UPDATE garment_analysis SET {', '.join(f'{k}=?' for k in fields)}, "
            f"edited_by_user=1, updated_at=datetime('now') WHERE garment_id=?",
            (*fields.values(), garment_id))
    return pipeline.get_garment_detail(garment_id)


@router.patch("/images/{image_id}/role")
def set_image_role(image_id: int, payload: RoleUpdate):
    """Reclassify a reference photo. Locks the role so a re-analysis leaves it alone."""
    if payload.role not in VALID_ROLES:
        raise HTTPException(400, f"role must be one of {sorted(VALID_ROLES)}")
    with get_db() as conn:
        row = conn.execute("SELECT garment_id FROM garment_images WHERE id=?",
                           (image_id,)).fetchone()
        if not row:
            raise HTTPException(404, "image not found")
        conn.execute("UPDATE garment_images SET role=?, role_locked=1 WHERE id=?",
                     (payload.role, image_id))
    return pipeline.get_garment_detail(row["garment_id"])


@router.post("/{garment_id}/detail-crop")
def add_detail_crop(garment_id: int, image_id: int, box_2d: List[int], why: str = ""):
    """Add a detail crop by hand, for when the user can see a feature the analyzer
    missed. Box is [ymin, xmin, ymax, xmax] normalized 0-1000."""
    if len(box_2d) != 4:
        raise HTTPException(400, "box_2d must be [ymin, xmin, ymax, xmax], 0-1000")
    with get_db() as conn:
        img = row_to_dict(conn.execute(
            "SELECT * FROM garment_images WHERE id=? AND garment_id=?",
            (image_id, garment_id)).fetchone())
        if not img:
            raise HTTPException(404, "image not found on this garment")
        analysis = row_to_dict(conn.execute(
            "SELECT * FROM garment_analysis WHERE garment_id=?", (garment_id,)).fetchone(),
            json_cols=("detail_regions",))
        if not analysis:
            raise HTTPException(404, "garment has not been analyzed yet")

    try:
        make_crop(img["path"], box_2d)   # fail fast on a bad box
    except (ValueError, OSError) as e:
        raise HTTPException(400, f"could not crop: {e}")

    regions = (analysis.get("detail_regions") or []) + [{
        "image": f"IMAGE {img['sort_order'] + 1}", "image_index": img["sort_order"],
        "source_path": img["path"], "box_2d": box_2d, "why": why or "user-selected detail"}]

    with get_db() as conn:
        conn.execute(
            "UPDATE garment_analysis SET detail_regions=?, edited_by_user=1, "
            "updated_at=datetime('now') WHERE garment_id=?",
            (json.dumps(regions), garment_id))
    return pipeline.get_garment_detail(garment_id)


@router.delete("/{garment_id}/detail-crop/{index}")
def remove_detail_crop(garment_id: int, index: int):
    with get_db() as conn:
        analysis = row_to_dict(conn.execute(
            "SELECT * FROM garment_analysis WHERE garment_id=?", (garment_id,)).fetchone(),
            json_cols=("detail_regions",))
        if not analysis:
            raise HTTPException(404, "garment has not been analyzed yet")
        regions = analysis.get("detail_regions") or []
        if not (0 <= index < len(regions)):
            raise HTTPException(404, "no detail region at that index")
        regions.pop(index)
        conn.execute(
            "UPDATE garment_analysis SET detail_regions=?, edited_by_user=1, "
            "updated_at=datetime('now') WHERE garment_id=?", (json.dumps(regions), garment_id))
    return pipeline.get_garment_detail(garment_id)


# ---------------- looks ----------------
@router.post("/{garment_id}/looks/propose")
def propose_looks(garment_id: int, payload: ProposeLooks):
    try:
        return pipeline.generate_looks(garment_id, n=payload.n, replace=payload.replace,
                                       user_direction=payload.direction, profile=payload.profile,
                                       match_existing=payload.match_existing)
    except (ValueError, VisionError) as e:
        raise HTTPException(400, str(e))


@router.post("/{garment_id}/looks")
def create_look(garment_id: int, payload: LookCreate):
    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM looks WHERE garment_id=?",
                         (garment_id,)).fetchone()["c"]
        source = "library" if payload.template_id else "user"
        cur = conn.execute(
            "INSERT INTO looks (garment_id, label, text, props, source, template_id, sort_order) "
            "VALUES (?,?,?,?,?,?,?)",
            (garment_id, payload.label or f"Look {n + 1}", payload.text, payload.props, source,
             payload.template_id, n))
        if payload.template_id:
            conn.execute("UPDATE look_templates SET times_used = times_used + 1 WHERE id=?",
                         (payload.template_id,))
        return row_to_dict(conn.execute("SELECT * FROM looks WHERE id=?",
                                        (cur.lastrowid,)).fetchone())


@router.post("/looks/{look_id}/back-view")
def add_back_view(look_id: int):
    """Add a back-view counterpart to a look, holding its scene and lighting constant."""
    try:
        return pipeline.create_back_look(look_id)
    except (ValueError, VisionError) as e:
        raise HTTPException(400, str(e))


@router.patch("/looks/{look_id}")
def update_look(look_id: int, payload: LookUpdate):
    fields = {k: v for k, v in payload.dict(exclude_unset=True).items() if v is not None}
    if not fields:
        raise HTTPException(400, "nothing to update")
    # A hand-edited look is no longer the model's proposal.
    fields["source"] = "user"
    with get_db() as conn:
        conn.execute(f"UPDATE looks SET {', '.join(f'{k}=?' for k in fields)} WHERE id=?",
                     (*fields.values(), look_id))
        return row_to_dict(conn.execute("SELECT * FROM looks WHERE id=?", (look_id,)).fetchone())


@router.delete("/looks/{look_id}")
def delete_look(look_id: int):
    with get_db() as conn:
        conn.execute("DELETE FROM looks WHERE id=?", (look_id,))
    return {"deleted": look_id}
