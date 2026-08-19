"""Generation, QC, and repair.

Nothing here fires automatically. A QC failure produces a *suggested* repair with
its reasoning; applying it is a separate, explicit call, because each application
spends credits.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from ..config import DEFAULT_AVATAR_REF_COUNT, DEFAULT_IMAGE_SIZE, DEFAULT_PROMPT_PROFILE
from ..core import pipeline
from ..core.generator import IMAGE_SIZES
from ..core.shoot_style import PROFILES
from ..db import get_db, row_to_dict, rows_to_dicts

router = APIRouter(tags=["generations"])


class GenerateRequest(BaseModel):
    look_id: int
    avatar_id: Optional[int] = None
    image_size: str = DEFAULT_IMAGE_SIZE
    avatar_ref_count: int = DEFAULT_AVATAR_REF_COUNT
    extra_direction: Optional[str] = None
    profile: str = DEFAULT_PROMPT_PROFILE
    run_qc: bool = True


class PreviewRequest(BaseModel):
    look_id: int
    avatar_id: Optional[int] = None
    avatar_ref_count: int = DEFAULT_AVATAR_REF_COUNT
    extra_direction: Optional[str] = None
    profile: str = DEFAULT_PROMPT_PROFILE


class RepairRequest(BaseModel):
    generation_id: int
    # Allows editing the suggestion before spending on it.
    extra_direction: Optional[str] = None
    image_size: Optional[str] = None
    avatar_ref_count: Optional[int] = None


@router.post("/generations/preview")
def preview(payload: PreviewRequest):
    """The exact prompt and ordered reference list that would be sent. Free."""
    try:
        built = pipeline.preview_prompt(payload.look_id, payload.avatar_id,
                                        payload.avatar_ref_count, payload.extra_direction,
                                        payload.profile)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"prompt": built["prompt"], "ref_manifest": built["ref_manifest"],
            "ref_count": len(built["ref_paths"])}


@router.post("/generations")
def generate(payload: GenerateRequest):
    if payload.image_size not in IMAGE_SIZES:
        raise HTTPException(400, f"image_size must be one of {IMAGE_SIZES}")
    if payload.profile not in PROFILES:
        raise HTTPException(400, f"profile must be one of {list(PROFILES)}")
    try:
        return pipeline.generate_for_look(
            look_id=payload.look_id, avatar_id=payload.avatar_id,
            image_size=payload.image_size, avatar_ref_count=payload.avatar_ref_count,
            extra_direction=payload.extra_direction, profile=payload.profile,
            run_qc=payload.run_qc)
    except ValueError as e:
        raise HTTPException(400, str(e))


class BatchRequest(BaseModel):
    session_id: int
    # Empty/omitted means the whole session; otherwise only these garments.
    garment_ids: Optional[List[int]] = None
    image_size: str = DEFAULT_IMAGE_SIZE
    profile: str = DEFAULT_PROMPT_PROFILE
    only_ungenerated: bool = True


@router.post("/generations/batch")
def generate_batch(payload: BatchRequest, background: BackgroundTasks):
    """Generate the looks in a session (or a chosen subset of its garments).

    Runs in the background and returns the count queued; the UI polls
    /sessions/{id}/progress. `only_ungenerated` defaults to true so a second click
    cannot re-bill work that already exists — the single most expensive mistake
    this endpoint could make.
    """
    params: list = [payload.session_id]
    sql = ("SELECT l.id FROM looks l JOIN garments g ON g.id = l.garment_id "
           "WHERE g.session_id=? AND g.avatar_id IS NOT NULL")
    if payload.garment_ids:
        sql += f" AND g.id IN ({','.join('?' * len(payload.garment_ids))})"
        params += payload.garment_ids
    if payload.only_ungenerated:
        sql += " AND l.id NOT IN (SELECT DISTINCT look_id FROM generations WHERE status='done')"

    with get_db() as conn:
        look_ids = [r["id"] for r in conn.execute(sql, params).fetchall()]
        # Distinguish "already done" from "cannot run yet" — they need different fixes.
        no_avatar = conn.execute(
            "SELECT COUNT(*) n FROM garments WHERE session_id=? AND avatar_id IS NULL",
            (payload.session_id,)).fetchone()["n"]

    if not look_ids:
        note = "Nothing to generate — every selected look already has a shot."
        if no_avatar:
            note = (f"Nothing to generate. {no_avatar} garment(s) still need a model assigned "
                    f"before they can be shot.")
        return {"queued": 0, "note": note}

    def run():
        for look_id in look_ids:
            try:
                pipeline.generate_for_look(look_id=look_id, image_size=payload.image_size,
                                           profile=payload.profile)
            except Exception:  # one bad garment must not halt the batch
                continue

    background.add_task(run)
    return {"queued": len(look_ids),
            "note": f"Generating {len(look_ids)} shot(s). You can keep working; "
                    f"progress appears above."}


@router.get("/generations/{generation_id}")
def get_generation(generation_id: int):
    with get_db() as conn:
        gen = row_to_dict(conn.execute("SELECT * FROM generations WHERE id=?",
                                       (generation_id,)).fetchone(), json_cols=("ref_paths",))
        if not gen:
            raise HTTPException(404, "generation not found")
        gen["qc_history"] = rows_to_dicts(conn.execute(
            "SELECT * FROM qc_results WHERE generation_id=? ORDER BY id DESC",
            (generation_id,)).fetchall(), json_cols=("checks", "repair"))
    return gen


@router.post("/generations/{generation_id}/qc")
def rerun_qc(generation_id: int):
    try:
        return pipeline.run_qc_for_generation(generation_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/generations/repair")
def apply_repair(payload: RepairRequest):
    """Regenerate a failed look with its suggested fix applied.

    The new image is a fresh attempt linked to the one it repairs, so the pair can
    be compared rather than the failure being silently replaced.
    """
    with get_db() as conn:
        gen = row_to_dict(conn.execute("SELECT * FROM generations WHERE id=?",
                                       (payload.generation_id,)).fetchone())
        if not gen:
            raise HTTPException(404, "generation not found")
        qc = row_to_dict(conn.execute(
            "SELECT * FROM qc_results WHERE generation_id=? ORDER BY id DESC LIMIT 1",
            (payload.generation_id,)).fetchone(), json_cols=("checks", "repair"))

    repair: Dict[str, Any] = (qc or {}).get("repair") or {}
    if not repair and not payload.extra_direction:
        raise HTTPException(400, "no repair was suggested for this generation, and no direction "
                                 "was supplied to override it")

    action = repair.get("action")
    extra = payload.extra_direction or repair.get("extra_direction")
    avatar_ref_count = payload.avatar_ref_count or (2 if action == "add_avatar_ref"
                                                    else DEFAULT_AVATAR_REF_COUNT)

    if action == "add_colour_reference":
        # Adds a fabric patch to the garment's references; the prompt builder picks
        # it up on the next build. Idempotent, so a repeated repair is harmless.
        pipeline.add_colour_reference(gen["garment_id"])

    if action == "change_pose_standing" and not payload.extra_direction:
        extra = ("POSE OVERRIDE: she must be STANDING, not seated and not with legs crossed. "
                 "The garment's coverage depends on the fabric hanging straight down, so keep her "
                 "stance relaxed but upright with feet close together.")

    try:
        return pipeline.generate_for_look(
            look_id=gen["look_id"], avatar_id=gen.get("avatar_id"),
            image_size=payload.image_size or gen.get("image_size") or DEFAULT_IMAGE_SIZE,
            avatar_ref_count=avatar_ref_count, extra_direction=extra,
            parent_generation_id=payload.generation_id,
            repair_applied=repair.get("label") or "manual direction")
    except ValueError as e:
        raise HTTPException(400, str(e))
