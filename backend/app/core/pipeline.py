"""
Orchestration: analysis -> looks -> generation -> QC, with DB state.

Every step is separately callable because the whole point of Drape is that a user
can intervene between any two of them. Nothing here chains automatically into a
generation — generation costs credits and is always an explicit action.
"""
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import (DEFAULT_AVATAR_REF_COUNT, DEFAULT_IMAGE_SIZE,
                      DEFAULT_PROMPT_PROFILE, STORAGE_DIR)
from ..db import get_db, row_to_dict, rows_to_dicts
from . import art_director, extractor, prompt_builder, qc as qc_mod
from .detail_crop import build_detail_refs
from .garment_crop import resolve_garment_ref
from .lessons import record_failure, record_fix_worked
from .generator import get_client
from .vision import VisionError

GEN_DIR = STORAGE_DIR / "generations"


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
def analyze_garment(garment_id: int, propose_looks: bool = True,
                    n_looks: int = 2) -> Dict[str, Any]:
    """Run factual extraction, persist roles + analysis, and optionally propose looks."""
    with get_db() as conn:
        garment = row_to_dict(conn.execute(
            "SELECT * FROM garments WHERE id=?", (garment_id,)).fetchone())
        if not garment:
            raise ValueError(f"garment {garment_id} not found")
        images = rows_to_dicts(conn.execute(
            "SELECT * FROM garment_images WHERE garment_id=? ORDER BY sort_order, id",
            (garment_id,)).fetchall())

    if not images:
        raise ValueError("garment has no images")

    paths = [Path(i["path"]) for i in images]
    analysis = extractor.extract(paths, category=garment.get("category"))

    # Persist roles, but never overwrite a role a user has deliberately set.
    by_index = {extractor.label_to_index(c.get("image", "")): c
                for c in analysis.get("image_classification", [])}
    with get_db() as conn:
        for idx, img in enumerate(images):
            entry = by_index.get(idx) or {}
            role = entry.get("role")
            if role and not img["role_locked"]:
                conn.execute("UPDATE garment_images SET role=? WHERE id=?", (role, img["id"]))
            # Person detection is a fact about the photo, not a user preference, so
            # it is refreshed even on images whose role the user has pinned.
            conn.execute(
                "UPDATE garment_images SET contains_person=?, garment_box=? WHERE id=?",
                (int(bool(entry.get("contains_person"))),
                 json.dumps(entry["garment_box"]) if entry.get("garment_box") else None,
                 img["id"]))

        conn.execute(
            """INSERT INTO garment_analysis
                 (garment_id, garment_desc, pieces, coverage_risk, pairing_note,
                  back_has_structure, detail_regions, raw, edited_by_user, updated_at)
               VALUES (?,?,?,?,?,?,?,?,0,datetime('now'))
               ON CONFLICT(garment_id) DO UPDATE SET
                 garment_desc=excluded.garment_desc, pieces=excluded.pieces,
                 coverage_risk=excluded.coverage_risk, pairing_note=excluded.pairing_note,
                 back_has_structure=excluded.back_has_structure,
                 detail_regions=excluded.detail_regions, raw=excluded.raw,
                 edited_by_user=0, updated_at=datetime('now')""",
            (garment_id, analysis.get("garment_desc"), json.dumps(analysis.get("pieces", [])),
             int(bool(analysis.get("coverage_risk"))), analysis.get("pairing_note", ""),
             int(bool(analysis.get("back_has_structure"))),
             json.dumps(analysis.get("detail_regions", [])), json.dumps(analysis)))
        conn.execute("UPDATE garments SET status='analyzed' WHERE id=?", (garment_id,))

    if propose_looks:
        try:
            generate_looks(garment_id, n=n_looks, replace=True)
        except VisionError as e:
            analysis["look_error"] = str(e)

    return get_garment_detail(garment_id)


def generate_looks(garment_id: int, n: int = 2, replace: bool = False,
                   user_direction: Optional[str] = None,
                   profile: str = DEFAULT_PROMPT_PROFILE,
                   match_existing: bool = False) -> List[Dict[str, Any]]:
    """Propose looks, grounded in the category's proven library.

    By default every look is steered AWAY from settings already used in this shoot,
    because a collection where every frame shares a backdrop reads as templated
    rather than photographed. Set match_existing=True for the deliberate opposite:
    a coherent set shot in one place."""
    with get_db() as conn:
        garment = row_to_dict(conn.execute(
            "SELECT * FROM garments WHERE id=?", (garment_id,)).fetchone())
        analysis = row_to_dict(conn.execute(
            "SELECT * FROM garment_analysis WHERE garment_id=?", (garment_id,)).fetchone(),
            json_cols=("pieces", "detail_regions"))
        if not analysis or not analysis.get("garment_desc"):
            raise ValueError("garment must be analyzed before looks can be proposed")

        category = garment.get("category")
        library = rows_to_dicts(conn.execute(
            "SELECT * FROM look_templates WHERE category=? ORDER BY times_used DESC, id DESC LIMIT 6",
            (category,)).fetchall()) if category else []

        # Everything already spoken for in this shoot. Read from the looks
        # themselves — an earlier version joined through look_templates, so it only
        # saw looks imported from the library and effectively never fired.
        used = [r["scene_tag"] for r in conn.execute(
            """SELECT DISTINCT l.scene_tag FROM looks l
               JOIN garments g ON g.id = l.garment_id
               WHERE g.session_id=? AND l.scene_tag IS NOT NULL AND l.scene_tag != ''""",
            (garment["session_id"],)).fetchall()]
        # The tags are terse, so the director also gets the actual sentences to
        # differentiate against — two looks can carry different tags and still be
        # the same photograph.
        used_texts = [r["text"] for r in conn.execute(
            """SELECT l.text FROM looks l JOIN garments g ON g.id = l.garment_id
               WHERE g.session_id=? AND l.view='front'
               ORDER BY l.id DESC LIMIT 12""", (garment["session_id"],)).fetchall()]

    looks = art_director.propose_looks(
        garment_desc=analysis["garment_desc"], category=category,
        pieces=analysis.get("pieces") or [], n=n, library=library,
        used_scene_tags=used, used_look_texts=used_texts, match_existing=match_existing,
        user_direction=user_direction, profile=profile)

    with get_db() as conn:
        if replace:
            # Only discard looks that were never generated from — a look with
            # history is a record of work and must not vanish under a re-propose.
            conn.execute(
                """DELETE FROM looks WHERE garment_id=? AND id NOT IN
                   (SELECT DISTINCT look_id FROM generations)""", (garment_id,))
        existing = conn.execute(
            "SELECT COUNT(*) c FROM looks WHERE garment_id=?", (garment_id,)).fetchone()["c"]
        out = []
        for i, look in enumerate(looks):
            label = f"Look {existing + i + 1}"
            cur = conn.execute(
                """INSERT INTO looks (garment_id, label, text, props, scene_tag, source, sort_order)
                   VALUES (?,?,?,?,?,?,?)""",
                (garment_id, label, look["text"], look.get("props"), look.get("scene_tag"),
                 "ai", existing + i))
            out.append({"id": cur.lastrowid, "label": label, "text": look["text"],
                        "props": look.get("props"), "scene_tag": look.get("scene_tag"),
                        "source": "ai"})
    return out


def create_back_look(look_id: int) -> Dict[str, Any]:
    """Add a back-view counterpart to an existing look.

    The scene, light and mood are held constant and only the camera side changes, so
    the two shots read as one sitting — which is what makes a back view useful for a
    catalogue rather than just another image.
    """
    with get_db() as conn:
        look = row_to_dict(conn.execute("SELECT * FROM looks WHERE id=?", (look_id,)).fetchone())
        if not look:
            raise ValueError(f"look {look_id} not found")
        if (look.get("view") or "front") == "back":
            raise ValueError("that look is already a back view")
        analysis = row_to_dict(conn.execute(
            "SELECT * FROM garment_analysis WHERE garment_id=?", (look["garment_id"],)).fetchone())
        if not analysis:
            raise ValueError("garment must be analyzed first")

    variant = art_director.back_view_variant(
        look["text"], analysis.get("garment_desc", ""), look.get("props"))
    if not variant["text"]:
        raise ValueError("could not compose a back view for this look")

    with get_db() as conn:
        n = conn.execute("SELECT COUNT(*) c FROM looks WHERE garment_id=?",
                         (look["garment_id"],)).fetchone()["c"]
        cur = conn.execute(
            """INSERT INTO looks (garment_id, label, text, props, scene_tag, source, view, sort_order)
               VALUES (?,?,?,?,?,?,'back',?)""",
            (look["garment_id"], f"{look['label']} — back", variant["text"],
             variant["props"] or None, variant.get("scene_tag") or look.get("scene_tag"),
             look["source"], n))
        return row_to_dict(conn.execute("SELECT * FROM looks WHERE id=?",
                                        (cur.lastrowid,)).fetchone())


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------
def _resolve_refs(garment_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        images = rows_to_dicts(conn.execute(
            "SELECT * FROM garment_images WHERE garment_id=? ORDER BY sort_order, id",
            (garment_id,)).fetchall(), json_cols=("garment_box",))
        analysis = row_to_dict(conn.execute(
            "SELECT * FROM garment_analysis WHERE garment_id=?", (garment_id,)).fetchone(),
            json_cols=("pieces", "detail_regions"))

    front_row = next((i for i in images if i["role"] == "full_front"), None)
    back_row = next((i for i in images if i["role"] == "full_back"), None)
    if not front_row:
        usable = [i for i in images if i["role"] != "irrelevant"]
        front_row = usable[0] if usable else (images[0] if images else None)

    front, front_cropped = resolve_garment_ref(front_row) if front_row else (None, False)
    back, back_cropped = resolve_garment_ref(back_row) if back_row else (None, False)

    detail_refs = build_detail_refs(analysis.get("detail_regions") or []) if analysis else []
    return {"front": front, "back": back, "front_cropped": front_cropped,
            "back_cropped": back_cropped, "detail_refs": detail_refs, "analysis": analysis}


def preview_prompt(look_id: int, avatar_id: Optional[int] = None,
                   avatar_ref_count: int = DEFAULT_AVATAR_REF_COUNT,
                   extra_direction: Optional[str] = None,
                   profile: str = DEFAULT_PROMPT_PROFILE) -> Dict[str, Any]:
    """Build exactly what generation would send, without sending it."""
    with get_db() as conn:
        look = row_to_dict(conn.execute("SELECT * FROM looks WHERE id=?", (look_id,)).fetchone())
        if not look:
            raise ValueError(f"look {look_id} not found")
        garment = row_to_dict(conn.execute(
            "SELECT * FROM garments WHERE id=?", (look["garment_id"],)).fetchone())
        avatar_id = avatar_id or garment.get("avatar_id")
        avatar = row_to_dict(conn.execute(
            "SELECT * FROM avatars WHERE id=?", (avatar_id,)).fetchone()) if avatar_id else None

    if not avatar:
        raise ValueError("no avatar assigned to this garment")

    r = _resolve_refs(look["garment_id"])
    if not r["analysis"] or not r["analysis"].get("garment_desc"):
        raise ValueError("garment must be analyzed before generating")

    prompt, refs, manifest = prompt_builder.build(
        analysis=r["analysis"], look_text=look["text"],
        avatar_front=avatar["front_path"], avatar_back=avatar.get("back_path"),
        garment_front=r["front"], garment_back=r["back"], detail_refs=r["detail_refs"],
        avatar_ref_count=avatar_ref_count, extra_direction=extra_direction,
        props=look.get("props"), model_styling=avatar.get("styling"),
        view=look.get("view") or "front",
        garment_front_cropped=r["front_cropped"], garment_back_cropped=r["back_cropped"],
        category=garment.get("category"), profile=profile)

    return {"prompt": prompt, "ref_paths": refs, "ref_manifest": manifest,
            "avatar": avatar, "look": look, "garment": garment}


def generate_for_look(look_id: int, avatar_id: Optional[int] = None,
                      image_size: str = DEFAULT_IMAGE_SIZE,
                      avatar_ref_count: int = DEFAULT_AVATAR_REF_COUNT,
                      extra_direction: Optional[str] = None,
                      parent_generation_id: Optional[int] = None,
                      repair_applied: Optional[str] = None,
                      profile: str = DEFAULT_PROMPT_PROFILE,
                      run_qc: bool = True) -> Dict[str, Any]:
    """Generate one image for a look, then judge it. Appends a new attempt; never
    overwrites a previous one."""
    built = preview_prompt(look_id, avatar_id, avatar_ref_count, extra_direction, profile)
    garment_id = built["look"]["garment_id"]
    avatar = built["avatar"]

    with get_db() as conn:
        attempt = conn.execute(
            "SELECT COALESCE(MAX(attempt_no),0)+1 n FROM generations WHERE look_id=?",
            (look_id,)).fetchone()["n"]
        cur = conn.execute(
            """INSERT INTO generations
                 (look_id, garment_id, parent_generation_id, attempt_no, prompt, ref_paths,
                  image_size, avatar_id, status, repair_applied, prompt_profile)
               VALUES (?,?,?,?,?,?,?,?,'running',?,?)""",
            (look_id, garment_id, parent_generation_id, attempt, built["prompt"],
             json.dumps(built["ref_paths"]), image_size, avatar["id"], repair_applied, profile))
        generation_id = cur.lastrowid

    GEN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = GEN_DIR / f"gen_{generation_id:06d}.png"

    result = get_client().generate_with_refs(
        prompt=built["prompt"], reference_paths=built["ref_paths"],
        output_path=str(out_path), image_size=image_size)

    if not result.get("success"):
        with get_db() as conn:
            conn.execute("UPDATE generations SET status='error', error=? WHERE id=?",
                         (result.get("error", "generation failed"), generation_id))
        return {"generation_id": generation_id, "status": "error",
                "error": result.get("error", "generation failed")}

    with get_db() as conn:
        conn.execute("UPDATE generations SET status='done', output_path=? WHERE id=?",
                     (str(out_path), generation_id))
        conn.execute("UPDATE garments SET status='generated' WHERE id=?", (garment_id,))

    out: Dict[str, Any] = {"generation_id": generation_id, "status": "done",
                           "output_path": str(out_path)}
    if run_qc:
        out["qc"] = run_qc_for_generation(generation_id)
    return out


def run_qc_for_generation(generation_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        gen = row_to_dict(conn.execute(
            "SELECT * FROM generations WHERE id=?", (generation_id,)).fetchone(),
            json_cols=("ref_paths",))
        if not gen or not gen.get("output_path"):
            raise ValueError("generation has no output to judge")
        look = row_to_dict(conn.execute(
            "SELECT * FROM looks WHERE id=?", (gen["look_id"],)).fetchone())
        analysis = row_to_dict(conn.execute(
            "SELECT * FROM garment_analysis WHERE garment_id=?", (gen["garment_id"],)).fetchone(),
            json_cols=("pieces", "detail_regions"))

    context = (analysis or {}).get("pairing_note", "") or ""
    if (look.get("view") or "front") == "back":
        # Without this the judge fails avatar_identity every time, because it cannot
        # see a face it is being asked to match.
        context += (" This is a deliberate BACK VIEW shot: the model is photographed from behind "
                    "and her face is not expected to be visible. Judge identity on hair, body type "
                    "and skin tone rather than facial features, and do not fail the image for "
                    "showing her back.")

    result = qc_mod.qc_check(
        reference_paths=gen["ref_paths"] or [], output_path=gen["output_path"],
        prompt=gen["prompt"], context_note=context.strip())

    _learn(gen, result)

    repair = None
    if result.get("overall_pass") is False:
        regions = (analysis or {}).get("detail_regions") or []
        repair = qc_mod.suggest_repair(
            result, analysis or {}, look["text"],
            has_detail_refs=bool(regions),
            avatar_ref_count=2 if (gen["ref_paths"] and len(gen["ref_paths"]) > 1) else 1,
            has_colour_ref=any(r.get("kind") == "colour" for r in regions))

    with get_db() as conn:
        conn.execute(
            """INSERT INTO qc_results (generation_id, overall_pass, checks, summary, confirmed, repair)
               VALUES (?,?,?,?,?,?)""",
            (generation_id,
             None if result.get("overall_pass") is None else int(bool(result["overall_pass"])),
             json.dumps(result.get("checks", [])), result.get("summary", ""),
             int(bool(result.get("confirmed"))), json.dumps(repair) if repair else None))

    result["repair"] = repair
    return result


def _learn(gen: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Feed this verdict into the learning loop.

    Two signals, weighted very differently:
      * a confirmed failure is merely OBSERVED — recorded so a recurring problem
        surfaces, never fed back into a prompt on its own;
      * a repair attempt whose parent failed the same criterion and which now
        PASSES is PROVEN — that pair is evidence the correction works, and only
        those reach the prompt.
    """
    with get_db() as conn:
        row = conn.execute(
            """SELECT gar.category, g.lesson_recorded
               FROM generations g JOIN garments gar ON gar.id = g.garment_id
               WHERE g.id=?""", (gen["id"],)).fetchone()
    if not row or not row["category"] or row["lesson_recorded"]:
        # Already consumed: re-running QC on a shot must not re-credit its lesson.
        return
    category = row["category"]

    checks = result.get("checks") or []
    passed_now = {c["criterion"] for c in checks if c.get("pass")}

    def mark_consumed():
        with get_db() as conn:
            conn.execute("UPDATE generations SET lesson_recorded=1 WHERE id=?", (gen["id"],))

    if result.get("overall_pass") is False and result.get("confirmed"):
        for c in checks:
            if not c.get("pass"):
                record_failure(category, c["criterion"], c.get("reason", ""))
        mark_consumed()
        return

    # A pass. Did it repair a specific failure from the attempt it descends from?
    parent_id = gen.get("parent_generation_id")
    if not (result.get("overall_pass") and parent_id):
        return

    with get_db() as conn:
        parent_qc = row_to_dict(conn.execute(
            "SELECT * FROM qc_results WHERE generation_id=? ORDER BY id DESC LIMIT 1",
            (parent_id,)).fetchone(), json_cols=("checks", "repair"))
    if not parent_qc:
        return

    repair = parent_qc.get("repair") or {}
    guidance = repair.get("extra_direction") or repair.get("detail")
    # Credit ONLY the criterion the repair actually targeted. Failures often
    # cascade — one artefact can fail identity, realism and presence at once — and
    # a single fix then flips all of them. Crediting the fix to every criterion
    # that happened to recover files identity guidance under "garment_colour",
    # which would inject irrelevant advice into every later shot in the category.
    target = repair.get("criterion")
    if target and target in passed_now and any(
            c["criterion"] == target and not c.get("pass")
            for c in parent_qc.get("checks") or []):
        record_fix_worked(category, target, repair.get("label"), guidance)
    mark_consumed()


def add_colour_reference(garment_id: int) -> bool:
    """Attach a cropped patch of the garment's own fabric as a colour ground truth.

    Colour is the one criterion a prompt cannot state precisely — "beige" covers a
    range a buyer would notice. Reusing the detail-crop machinery means the patch
    is produced, cached and labelled exactly like any other close-up.
    """
    with get_db() as conn:
        analysis = row_to_dict(conn.execute(
            "SELECT * FROM garment_analysis WHERE garment_id=?", (garment_id,)).fetchone(),
            json_cols=("detail_regions",))
        images = rows_to_dicts(conn.execute(
            "SELECT * FROM garment_images WHERE garment_id=? ORDER BY sort_order, id",
            (garment_id,)).fetchall())
    if not analysis or not images:
        return False

    regions = analysis.get("detail_regions") or []
    if any(r.get("kind") == "colour" for r in regions):
        return False

    source = next((i for i in images if i["role"] == "full_front"),
                  next((i for i in images if i["role"] != "irrelevant"), images[0]))
    # A central patch: the flattest, most representative area of fabric, and the
    # part least likely to be edge, shadow or background.
    regions.append({
        "image": f"IMAGE {source['sort_order'] + 1}", "image_index": source["sort_order"],
        "source_path": source["path"], "box_2d": [380, 330, 660, 670],
        "kind": "colour",
        "why": "the garment's exact base COLOUR and tone — match this fabric patch precisely, "
               "including how warm or cool it is",
    })
    with get_db() as conn:
        conn.execute(
            "UPDATE garment_analysis SET detail_regions=?, updated_at=datetime('now') "
            "WHERE garment_id=?", (json.dumps(regions), garment_id))
    return True


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------
def get_garment_detail(garment_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        garment = row_to_dict(conn.execute(
            "SELECT * FROM garments WHERE id=?", (garment_id,)).fetchone())
        if not garment:
            raise ValueError(f"garment {garment_id} not found")
        garment["images"] = rows_to_dicts(conn.execute(
            "SELECT * FROM garment_images WHERE garment_id=? ORDER BY sort_order, id",
            (garment_id,)).fetchall())
        garment["analysis"] = row_to_dict(conn.execute(
            "SELECT * FROM garment_analysis WHERE garment_id=?", (garment_id,)).fetchone(),
            json_cols=("pieces", "detail_regions", "raw"))
        garment["avatar"] = row_to_dict(conn.execute(
            "SELECT * FROM avatars WHERE id=?", (garment["avatar_id"],)).fetchone()) \
            if garment.get("avatar_id") else None

        looks = rows_to_dicts(conn.execute(
            "SELECT * FROM looks WHERE garment_id=? ORDER BY sort_order, id",
            (garment_id,)).fetchall())
        for look in looks:
            gens = rows_to_dicts(conn.execute(
                "SELECT * FROM generations WHERE look_id=? ORDER BY attempt_no DESC",
                (look["id"],)).fetchall(), json_cols=("ref_paths",))
            for g in gens:
                g["qc"] = row_to_dict(conn.execute(
                    "SELECT * FROM qc_results WHERE generation_id=? ORDER BY id DESC LIMIT 1",
                    (g["id"],)).fetchone(), json_cols=("checks", "repair"))
            look["generations"] = gens
            look["latest"] = gens[0] if gens else None
        garment["looks"] = looks
    return garment
