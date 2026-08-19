"""
Tests for the guardrails — the rules whose failure costs credits or ships a bad
image. These are deliberately not "does it import" tests; each one covers a
mistake this pipeline has actually made.

No network, no generation. Vision calls are stubbed; everything else is real.

    ./.venv/bin/python -m pytest tests -q
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# --------------------------------------------------------------------------
# Point the app at a throwaway database before anything imports config.
# --------------------------------------------------------------------------
@pytest.fixture(scope="session", autouse=True)
def _isolated_storage(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("drape")
    os.environ["DRAPE_TEST_STORAGE"] = str(tmp)
    from app import config
    config.STORAGE_DIR = tmp
    config.DB_PATH = tmp / "test.db"
    for sub in ("uploads", "generations", "avatars", "crops"):
        (tmp / sub).mkdir(parents=True, exist_ok=True)

    from app import db
    db.DB_PATH = config.DB_PATH
    from app.core import detail_crop, garment_crop
    detail_crop.CROP_DIR = tmp / "crops"
    garment_crop.CROP_DIR = tmp / "crops"
    db.init_db()
    yield tmp


@pytest.fixture
def clean_db():
    from app.db import get_db
    with get_db() as conn:
        for table in ("qc_results", "generations", "looks", "garment_analysis",
                      "garment_images", "garments", "sessions", "avatars",
                      "look_templates", "qc_lessons"):
            conn.execute(f"DELETE FROM {table}")
    yield


def _make_image(path: Path, size=(800, 1200), colour=(120, 90, 140)):
    Image.new("RGB", size, colour).save(path, "JPEG")
    return path


# ==========================================================================
# Garment isolation — a person in a product photo must never reach the model
# ==========================================================================
class TestGarmentCrop:
    def test_head_box_clamps_garment_box_below_the_face(self):
        """The whole point: a garment box that starts above the chin would carry
        the reference person's face into the generation."""
        from app.core.extractor import HEAD_CLEARANCE, _normalize
        result = _normalize({
            "image_classification": [{
                "image": "IMAGE 1", "role": "full_front", "contains_person": True,
                "garment_box": [200, 100, 800, 900],   # starts above the head bottom
                "head_box": [50, 300, 300, 700],
            }],
            "detail_regions": [],
        }, [Path("a.jpg")])
        entry = result["image_classification"][0]
        assert entry["garment_box"][0] == 300 + HEAD_CLEARANCE, \
            "garment box must start below the head plus clearance"

    def test_full_frame_box_is_dropped_when_there_is_no_head_box_to_clamp_against(self):
        """Without a head box nothing removes the face, so a near-full-frame box
        would send the whole photograph through as the 'garment' reference."""
        from app.core.extractor import _normalize
        result = _normalize({
            "image_classification": [{
                "image": "IMAGE 1", "role": "full_front", "contains_person": True,
                "garment_box": [5, 5, 995, 995],   # no head_box
            }],
            "detail_regions": [],
        }, [Path("a.jpg")])
        assert result["image_classification"][0]["garment_box"] is None

    def test_a_large_box_is_kept_once_the_head_has_been_clamped_out(self):
        """A generous crop is fine as long as the face is gone — the garment
        reference has to show the whole silhouette, unlike a detail close-up."""
        from app.core.extractor import _normalize
        result = _normalize({
            "image_classification": [{
                "image": "IMAGE 1", "role": "full_front", "contains_person": True,
                "garment_box": [5, 5, 995, 995], "head_box": [0, 0, 10, 10],
            }],
            "detail_regions": [],
        }, [Path("a.jpg")])
        box = result["image_classification"][0]["garment_box"]
        assert box is not None and box[0] == 70, "must start below the head plus clearance"

    def test_no_box_when_no_person(self):
        from app.core.extractor import _normalize
        result = _normalize({
            "image_classification": [{
                "image": "IMAGE 1", "role": "full_front", "contains_person": False,
                "garment_box": [200, 100, 800, 900],
            }],
            "detail_regions": [],
        }, [Path("a.jpg")])
        assert result["image_classification"][0]["garment_box"] is None

    def test_crop_never_pads_upward(self, tmp_path):
        """Upward padding is how a face crept back into the crop once already."""
        from app.core.garment_crop import crop_garment
        src = _make_image(tmp_path / "src.jpg", (1000, 2000))
        out = crop_garment(src, [500, 100, 900, 900])
        assert out is not None
        with Image.open(out) as im:
            # top edge at 50% of 2000px; height must not extend above it
            assert im.height <= 2000 - 1000 + 1, "crop extended above its top edge"

    def test_resolve_falls_back_to_original_on_bad_box(self, tmp_path):
        """A failed crop must never block generation."""
        from app.core.garment_crop import resolve_garment_ref
        src = _make_image(tmp_path / "src.jpg")
        path, cropped = resolve_garment_ref(
            {"path": str(src), "contains_person": 1, "garment_box": [10, 10, 11, 11]})
        assert path == str(src) and cropped is False


# ==========================================================================
# Coverage — the safety-critical check that produced nudity once
# ==========================================================================
class TestCoveragePrompt:
    def _analysis(self, **kw):
        base = {"garment_desc": "a plain navy top", "pieces": ["top"],
                "coverage_risk": False, "pairing_note": "", "back_has_structure": False}
        base.update(kw)
        return base

    def test_pairing_note_reaches_the_prompt_when_a_piece_is_missing(self):
        from app.core.prompt_builder import build
        prompt, _, _ = build(
            analysis=self._analysis(coverage_risk=True,
                                    pairing_note="Add plain high-waisted trousers."),
            look_text="standing", avatar_front="a.png", garment_front="g.jpg", profile="v1")
        assert "Add plain high-waisted trousers." in prompt

    def test_no_pairing_text_when_the_set_is_complete(self):
        """Adding coverage to a complete set hides the product being sold."""
        from app.core.prompt_builder import build
        prompt, _, _ = build(
            analysis=self._analysis(pieces=["bra", "briefs"], coverage_risk=False,
                                    pairing_note="should be ignored"),
            look_text="standing", avatar_front="a.png", garment_front="g.jpg", profile="v1")
        assert "should be ignored" not in prompt

    def test_all_pieces_are_demanded_together(self):
        from app.core.prompt_builder import build
        prompt, _, _ = build(analysis=self._analysis(pieces=["bra_top", "briefs"]),
                             look_text="standing", avatar_front="a.png",
                             garment_front="g.jpg", profile="v1")
        assert "bra_top, briefs" in prompt


# ==========================================================================
# Prompt structure — reference roles, ordering, and the ignore-person clause
# ==========================================================================
class TestPromptStructure:
    ANALYSIS = {"garment_desc": "a navy dress", "pieces": ["dress"],
                "coverage_risk": False, "pairing_note": "", "back_has_structure": True}

    def test_every_reference_is_labelled_and_ordered(self):
        from app.core.prompt_builder import build
        prompt, refs, manifest = build(
            analysis=self.ANALYSIS, look_text="standing", avatar_front="a.png",
            garment_front="front.jpg", garment_back="back.jpg",
            detail_refs=[{"path": "d.jpg", "why": "lace trim"}], profile="v1")
        assert len(refs) == len(manifest) == 4
        for i in range(1, 5):
            assert f"IMAGE {i} =" in prompt
        assert [m["kind"] for m in manifest] == [
            "avatar_front", "garment_front", "garment_back", "detail_crop"]

    def test_back_reference_dropped_when_back_is_plain(self):
        """An ordinary back adds nothing to a front render but competes for attention."""
        from app.core.prompt_builder import build
        analysis = dict(self.ANALYSIS, back_has_structure=False)
        _, _, manifest = build(analysis=analysis, look_text="standing",
                               avatar_front="a.png", garment_front="f.jpg",
                               garment_back="b.jpg", profile="v1")
        assert "garment_back" not in [m["kind"] for m in manifest]

    def test_ignore_person_clause_only_when_a_crop_was_used(self):
        from app.core.prompt_builder import build
        with_crop, _, _ = build(analysis=self.ANALYSIS, look_text="s", avatar_front="a.png",
                                garment_front="f.jpg", garment_front_cropped=True, profile="v1")
        without, _, _ = build(analysis=self.ANALYSIS, look_text="s", avatar_front="a.png",
                              garment_front="f.jpg", garment_front_cropped=False, profile="v1")
        assert "That person is NOT the model" in with_crop
        assert "That person is NOT the model" not in without

    def test_back_view_leads_with_the_models_back_and_the_garment_back(self):
        from app.core.prompt_builder import build
        prompt, _, manifest = build(
            analysis=self.ANALYSIS, look_text="walking away", avatar_front="af.png",
            avatar_back="ab.png", garment_front="gf.jpg", garment_back="gb.jpg",
            view="back", profile="v1")
        kinds = [m["kind"] for m in manifest]
        assert kinds[0] == "avatar_back"
        assert kinds.index("garment_back") < kinds.index("garment_front")
        assert "THIS IS A BACK VIEW SHOT" in prompt

    def test_garment_is_stated_before_scene_and_styling(self):
        """Order is the priority signal: the product must never be displaced."""
        from app.core.prompt_builder import build
        prompt, _, _ = build(analysis=self.ANALYSIS, look_text="on a rooftop",
                             avatar_front="a.png", garment_front="f.jpg",
                             props="tote bag", profile="v1")
        assert prompt.index("THE GARMENT:") < prompt.index("POSE, SETTING AND LIGHTING:")
        assert prompt.index("POSE, SETTING AND LIGHTING:") < prompt.index("STYLING AND PROPS:")

    def test_props_carry_a_subordination_clause(self):
        from app.core.prompt_builder import build
        prompt, _, _ = build(analysis=self.ANALYSIS, look_text="s", avatar_front="a.png",
                             garment_front="f.jpg", props="tote bag", profile="v1")
        assert "secondary to the garment" in prompt


# ==========================================================================
# Shoot-craft profiles
# ==========================================================================
class TestProfiles:
    ANALYSIS = {"garment_desc": "a dress", "pieces": ["dress"], "coverage_risk": False,
                "pairing_note": "", "back_has_structure": False}

    def test_v1_carries_no_craft_block(self):
        from app.core.prompt_builder import build
        prompt, _, _ = build(analysis=self.ANALYSIS, look_text="s", avatar_front="a.png",
                             garment_front="f.jpg", category="Dresses", profile="v1")
        assert "PHOTOGRAPHIC CRAFT" not in prompt

    def test_v2_adds_craft_and_category_direction(self):
        from app.core.prompt_builder import build
        prompt, _, _ = build(analysis=self.ANALYSIS, look_text="s", avatar_front="a.png",
                             garment_front="f.jpg", category="Lingerie", profile="v2")
        assert "PHOTOGRAPHIC CRAFT" in prompt
        assert "CATEGORY DIRECTION — lingerie" in prompt

    def test_v2_does_not_invite_a_colour_grade(self):
        """A garment that passed colour on v1 failed it three times on v2. The
        cause was v2 asking for "refined colour grading" — an instruction to shift
        colour — in a pipeline whose whole job is colour fidelity."""
        from app.core.shoot_style import CATEGORY_CRAFT, UNIVERSAL_CRAFT
        assert "colour grading" not in UNIVERSAL_CRAFT
        assert "GARMENT COLOUR IS INVARIANT" in UNIVERSAL_CRAFT
        # Scene light must not be described in a way that tints the fabric.
        assert "pale blue" not in CATEGORY_CRAFT["Nightwear"]

    def test_v2_forbids_glowing_eyes(self):
        """A regression guard: over-emphasising 'alive' eyes produced luminous irises."""
        from app.core.shoot_style import UNIVERSAL_CRAFT
        assert "never glow" in UNIVERSAL_CRAFT

    def test_avatar_prompt_excludes_studio_hardware_and_demands_full_body(self):
        """Both were real defects in generated model references: a softbox in
        frame, and feet cropped off a body reference."""
        from app.core.shoot_style import AVATAR_CRAFT_V2
        text = AVATAR_CRAFT_V2.lower()
        assert "softboxes" in text and "no lighting equipment" in text
        assert "must not be cropped" in text

    def test_category_hint_does_not_dress_the_model_in_that_category(self):
        """Naming the category put her IN a shift dress, hiding the body shape the
        reference exists to record."""
        from app.core.avatars import build_avatar_prompt
        prompt = build_avatar_prompt("late twenties, athletic", "Dresses")
        assert "Do not dress her in dresses here" in prompt
        assert "plain fitted trousers" in prompt


# ==========================================================================
# The learning loop
# ==========================================================================
class TestLessons:
    def test_observed_failures_never_reach_a_prompt(self, clean_db):
        from app.core.lessons import lessons_block, record_failure
        record_failure("Dresses", "garment_color", "drifted warm")
        record_failure("Dresses", "garment_color", "drifted warm again")
        assert lessons_block("Dresses") == "", \
            "an unverified failure must not be fed back into generation"

    def test_a_proven_fix_is_injected(self, clean_db):
        from app.core.lessons import lessons_block, record_fix_worked
        record_fix_worked("Dresses", "garment_color", "Pin colour",
                          "CRITICAL COLOUR ACCURACY: match the exact shade.")
        block = lessons_block("Dresses")
        assert "CRITICAL COLOUR ACCURACY" in block

    def test_lessons_do_not_leak_across_categories(self, clean_db):
        from app.core.lessons import lessons_block, record_fix_worked
        record_fix_worked("Dresses", "garment_color", "Pin colour", "match the exact shade")
        assert lessons_block("Sportswear") == ""

    def test_disabled_lesson_is_not_injected(self, clean_db):
        from app.core.lessons import lessons_block, record_fix_worked
        from app.db import get_db
        record_fix_worked("Tops", "garment_color", "Pin colour", "match the exact shade")
        with get_db() as conn:
            conn.execute("UPDATE qc_lessons SET enabled=0 WHERE category='Tops'")
        assert lessons_block("Tops") == ""

    def test_injection_is_capped(self, clean_db):
        """A wall of caveats would drown the garment description."""
        from app.core.lessons import MAX_INJECTED, lessons_block, record_fix_worked
        for crit in ("garment_color", "garment_pattern", "garment_structure",
                     "coverage_and_pieces", "photorealism", "presence"):
            record_fix_worked("Tops", crit, "fix", f"guidance for {crit}")
        assert lessons_block("Tops").count("* [") == MAX_INJECTED

    def test_a_criterion_proven_in_two_categories_is_promoted_to_global(self, clean_db):
        """Colour drift belongs to the generator, not the garment type — it turned
        up independently in dresses, nightwear and tops, and each had to relearn
        it. Once proven twice, it should apply everywhere."""
        from app.core.lessons import lessons_block, record_fix_worked
        record_fix_worked("Dresses", "garment_color", "Pin colour", "match the exact shade")
        assert lessons_block("Sportswear") == "", "one category is not yet evidence"
        record_fix_worked("Nightwear", "garment_color", "Pin colour", "match the exact shade")
        block = lessons_block("Sportswear")
        assert "match the exact shade" in block, "should now apply to an untouched category"
        assert "across several garment types" in block

    def test_a_category_specific_lesson_stays_local(self, clean_db):
        from app.core.lessons import lessons_block, record_fix_worked
        record_fix_worked("Lingerie", "coverage_and_pieces", "Standing pose",
                          "use a standing pose for sheer skirts")
        assert "standing pose" in lessons_block("Lingerie")
        assert lessons_block("Tops") == ""

    def test_specific_guidance_wins_over_global_for_the_same_criterion(self, clean_db):
        """The more specific guidance is the better guidance."""
        from app.core.lessons import lessons_block, record_fix_worked
        record_fix_worked("Dresses", "garment_color", "Pin", "GENERIC colour rule")
        record_fix_worked("Nightwear", "garment_color", "Pin", "GENERIC colour rule")
        record_fix_worked("Tops", "garment_color", "Pin", "TOPS-SPECIFIC colour rule")
        block = lessons_block("Tops")
        assert "TOPS-SPECIFIC" in block
        assert block.count("[garment_color]") == 1, "must not inject the rule twice"

    def test_lessons_only_apply_on_v2(self, clean_db):
        from app.core.lessons import record_fix_worked
        from app.core.prompt_builder import build
        record_fix_worked("Dresses", "garment_color", "Pin colour", "match the exact shade")
        analysis = {"garment_desc": "d", "pieces": ["dress"], "coverage_risk": False,
                    "pairing_note": "", "back_has_structure": False}
        v1, _, _ = build(analysis=analysis, look_text="s", avatar_front="a.png",
                         garment_front="f.jpg", category="Dresses", profile="v1")
        v2, _, _ = build(analysis=analysis, look_text="s", avatar_front="a.png",
                         garment_front="f.jpg", category="Dresses", profile="v2")
        assert "LEARNED FROM PREVIOUS SHOOTS" not in v1
        assert "LEARNED FROM PREVIOUS SHOOTS" in v2


# ==========================================================================
# QC judging — the failure-to-fix mapping and the false-positive guard
# ==========================================================================
class TestQc:
    def _fail(self, criterion):
        return {"overall_pass": False, "confirmed": True,
                "checks": [{"criterion": criterion, "pass": False, "reason": "bad"}]}

    def test_seated_pose_with_a_slit_is_repaired_by_standing_not_by_wording(self):
        from app.core.qc import suggest_repair
        repair = suggest_repair(self._fail("coverage_and_pieces"),
                                {"garment_desc": "sheer A-line dress with a high slit"},
                                "seated cross-legged on a sofa", has_detail_refs=True)
        assert repair["action"] == "change_pose_standing"

    def test_ordinary_coverage_failure_strengthens_wording(self):
        from app.core.qc import suggest_repair
        repair = suggest_repair(self._fail("coverage_and_pieces"),
                                {"garment_desc": "a cotton t-shirt"},
                                "standing", has_detail_refs=True)
        assert repair["action"] == "amend_prompt"

    def test_colour_failure_asks_for_a_fabric_patch_before_stronger_wording(self):
        """An adjective cannot specify a colour: "match the exact shade" corrected a
        grey dress once then failed twice on a beige. Pixels beat words."""
        from app.core.qc import suggest_repair
        first = suggest_repair(self._fail("garment_color"), {"garment_desc": "d"},
                               "standing", has_detail_refs=True, has_colour_ref=False)
        assert first["action"] == "add_colour_reference"
        second = suggest_repair(self._fail("garment_color"), {"garment_desc": "d"},
                                "standing", has_detail_refs=True, has_colour_ref=True)
        assert second["action"] == "amend_prompt"

    def test_pattern_failure_asks_for_a_close_up_when_none_was_sent(self):
        from app.core.qc import suggest_repair
        assert suggest_repair(self._fail("garment_pattern"), {"garment_desc": "d"},
                              "standing", has_detail_refs=False)["action"] == "add_detail_crop"

    def test_pattern_failure_tightens_an_existing_close_up(self):
        from app.core.qc import suggest_repair
        assert suggest_repair(self._fail("garment_pattern"), {"garment_desc": "d"},
                              "standing", has_detail_refs=True)["action"] == "tighten_detail_crop"

    def test_identity_failure_escalates_to_a_second_reference_then_wording(self):
        from app.core.qc import suggest_repair
        one = suggest_repair(self._fail("avatar_identity"), {"garment_desc": "d"},
                             "standing", True, avatar_ref_count=1)
        two = suggest_repair(self._fail("avatar_identity"), {"garment_desc": "d"},
                             "standing", True, avatar_ref_count=2)
        assert one["action"] == "add_avatar_ref"
        assert two["action"] == "amend_prompt"

    def test_a_pass_produces_no_repair(self):
        from app.core.qc import suggest_repair
        assert suggest_repair({"overall_pass": True, "checks": []}, {}, "s", True) is None

    def test_a_refusal_is_a_hard_failure_not_a_pass(self, monkeypatch):
        """An unjudgeable image must never be treated as 'no problems found'."""
        from app.core import qc as qc_mod
        from app.core.vision import VisionBlocked

        def blocked(*_a, **_k):
            raise VisionBlocked("safety")
        monkeypatch.setattr(qc_mod, "_judge_once", blocked)
        result = qc_mod.qc_check([], "out.png")
        assert result["overall_pass"] is False and result["confirmed"] is True

    def test_a_failure_that_does_not_reproduce_is_treated_as_noise(self, monkeypatch):
        from app.core import qc as qc_mod
        calls = {"n": 0}

        def judge(*_a, **_k):
            calls["n"] += 1
            if calls["n"] == 1:
                return {"overall_pass": False, "summary": "",
                        "checks": [{"criterion": "garment_color", "pass": False, "reason": "x"}]}
            return {"overall_pass": True, "summary": "fine",
                    "checks": [{"criterion": "garment_color", "pass": True, "reason": "ok"}]}
        monkeypatch.setattr(qc_mod, "_judge_once", judge)
        result = qc_mod.qc_check([], "out.png")
        assert result["overall_pass"] is True and calls["n"] == 2

    def test_a_failure_that_repeats_is_believed(self, monkeypatch):
        from app.core import qc as qc_mod

        def judge(*_a, **_k):
            return {"overall_pass": False, "summary": "",
                    "checks": [{"criterion": "garment_color", "pass": False, "reason": "drift"}]}
        monkeypatch.setattr(qc_mod, "_judge_once", judge)
        result = qc_mod.qc_check([], "out.png")
        assert result["overall_pass"] is False
        assert result["corroborated_failures"] == ["garment_color"]

    def test_a_pass_is_never_re_judged(self, monkeypatch):
        """Re-checking passes would make the judge softer, not harder."""
        from app.core import qc as qc_mod
        calls = {"n": 0}

        def judge(*_a, **_k):
            calls["n"] += 1
            return {"overall_pass": True, "checks": [], "summary": ""}
        monkeypatch.setattr(qc_mod, "_judge_once", judge)
        qc_mod.qc_check([], "out.png")
        assert calls["n"] == 1


# ==========================================================================
# Credit discipline — the rules that stop money being spent twice
# ==========================================================================
class TestCreditDiscipline:
    def _session_with_generated_look(self):
        from app.db import get_db
        with get_db() as conn:
            sid = conn.execute("INSERT INTO sessions (name) VALUES ('s')").lastrowid
            aid = conn.execute(
                "INSERT INTO avatars (name, front_path) VALUES ('m','a.png')").lastrowid
            gid = conn.execute(
                "INSERT INTO garments (session_id, name, avatar_id) VALUES (?,?,?)",
                (sid, "g", aid)).lastrowid
            done = conn.execute(
                "INSERT INTO looks (garment_id, label, text) VALUES (?,'L1','t')",
                (gid,)).lastrowid
            pending = conn.execute(
                "INSERT INTO looks (garment_id, label, text) VALUES (?,'L2','t')",
                (gid,)).lastrowid
            conn.execute(
                """INSERT INTO generations (look_id, garment_id, prompt, status, output_path)
                   VALUES (?,?,'p','done','o.png')""", (done, gid))
        return sid, gid, done, pending

    def test_batch_skips_looks_that_already_have_a_shot(self, clean_db):
        """A second click on Generate all must not re-bill a finished session."""
        from app.routers.generations import BatchRequest, generate_batch
        from fastapi import BackgroundTasks
        sid, _, _, pending = self._session_with_generated_look()
        result = generate_batch(BatchRequest(session_id=sid), BackgroundTasks())
        assert result["queued"] == 1, "only the ungenerated look should be queued"

    def test_batch_can_be_scoped_to_selected_garments(self, clean_db):
        from app.routers.generations import BatchRequest, generate_batch
        from fastapi import BackgroundTasks
        sid, gid, _, _ = self._session_with_generated_look()
        assert generate_batch(BatchRequest(session_id=sid, garment_ids=[gid]),
                              BackgroundTasks())["queued"] == 1
        assert generate_batch(BatchRequest(session_id=sid, garment_ids=[gid + 999]),
                              BackgroundTasks())["queued"] == 0

    def test_a_garment_without_a_model_is_never_queued(self, clean_db):
        from app.db import get_db
        from app.routers.generations import BatchRequest, generate_batch
        from fastapi import BackgroundTasks
        with get_db() as conn:
            sid = conn.execute("INSERT INTO sessions (name) VALUES ('s')").lastrowid
            gid = conn.execute(
                "INSERT INTO garments (session_id, name) VALUES (?,'g')", (sid,)).lastrowid
            conn.execute("INSERT INTO looks (garment_id, label, text) VALUES (?,'L','t')", (gid,))
        result = generate_batch(BatchRequest(session_id=sid), BackgroundTasks())
        assert result["queued"] == 0
        assert "need a model" in result["note"]


# ==========================================================================
# Persistence rules
# ==========================================================================
class TestPersistence:
    def test_reproposing_looks_keeps_any_look_that_has_history(self, clean_db, monkeypatch):
        """A look with generations is a record of work and must not vanish."""
        from app.core import art_director, pipeline
        from app.db import get_db
        with get_db() as conn:
            sid = conn.execute("INSERT INTO sessions (name) VALUES ('s')").lastrowid
            gid = conn.execute(
                "INSERT INTO garments (session_id, name, category) VALUES (?,'g','Tops')",
                (sid,)).lastrowid
            conn.execute(
                """INSERT INTO garment_analysis (garment_id, garment_desc, pieces)
                   VALUES (?,'a top','[]')""", (gid,))
            kept = conn.execute(
                "INSERT INTO looks (garment_id, label, text) VALUES (?,'keep','t')",
                (gid,)).lastrowid
            dropped = conn.execute(
                "INSERT INTO looks (garment_id, label, text) VALUES (?,'drop','t')",
                (gid,)).lastrowid
            conn.execute(
                """INSERT INTO generations (look_id, garment_id, prompt, status)
                   VALUES (?,?,'p','done')""", (kept, gid))

        monkeypatch.setattr(art_director, "propose_looks",
                            lambda **_k: [{"text": "new look", "props": "", "scene_tag": "x"}])
        pipeline.generate_looks(gid, n=1, replace=True)

        with get_db() as conn:
            ids = {r["id"] for r in conn.execute(
                "SELECT id FROM looks WHERE garment_id=?", (gid,)).fetchall()}
        assert kept in ids
        assert dropped not in ids

    def test_scene_tags_are_stored_so_variety_can_be_enforced(self, clean_db, monkeypatch):
        from app.core import art_director, pipeline
        from app.db import get_db
        with get_db() as conn:
            sid = conn.execute("INSERT INTO sessions (name) VALUES ('s')").lastrowid
            gid = conn.execute(
                "INSERT INTO garments (session_id, name, category) VALUES (?,'g','Tops')",
                (sid,)).lastrowid
            conn.execute(
                """INSERT INTO garment_analysis (garment_id, garment_desc, pieces)
                   VALUES (?,'a top','[]')""", (gid,))
        monkeypatch.setattr(
            art_director, "propose_looks",
            lambda **_k: [{"text": "t", "props": "", "scene_tag": "sunlit market"}])
        pipeline.generate_looks(gid, n=1)
        with get_db() as conn:
            assert conn.execute(
                "SELECT scene_tag FROM looks WHERE garment_id=?", (gid,)).fetchone()[0] \
                == "sunlit market"

    def test_used_scenes_are_passed_to_the_art_director(self, clean_db, monkeypatch):
        """The guard that was inert for a while: it read only library-sourced looks."""
        from app.core import art_director, pipeline
        from app.db import get_db
        seen = {}
        with get_db() as conn:
            sid = conn.execute("INSERT INTO sessions (name) VALUES ('s')").lastrowid
            g1 = conn.execute(
                "INSERT INTO garments (session_id, name, category) VALUES (?,'a','Tops')",
                (sid,)).lastrowid
            g2 = conn.execute(
                "INSERT INTO garments (session_id, name, category) VALUES (?,'b','Tops')",
                (sid,)).lastrowid
            for g in (g1, g2):
                conn.execute(
                    """INSERT INTO garment_analysis (garment_id, garment_desc, pieces)
                       VALUES (?,'a top','[]')""", (g,))
            conn.execute(
                "INSERT INTO looks (garment_id, label, text, scene_tag) "
                "VALUES (?,'L','rooftop at dusk','rooftop')", (g1,))

        def capture(**kw):
            seen.update(kw)
            return [{"text": "t", "props": "", "scene_tag": "y"}]
        monkeypatch.setattr(art_director, "propose_looks", capture)
        pipeline.generate_looks(g2, n=1)
        assert "rooftop" in seen["used_scene_tags"]
        assert any("rooftop at dusk" in t for t in seen["used_look_texts"])


# ==========================================================================
# Config safety
# ==========================================================================
class TestConfig:
    def test_no_capability_is_committed_to_source(self):
        """The provider session id grants credit spend, so it must come from the
        environment. Detected structurally rather than by listing real values —
        naming them here would put them back in the repo this test exists to keep
        them out of."""
        import re
        source = (Path(__file__).resolve().parents[1] / "app" / "config.py").read_text()
        for name in ("PROVIDER_ORG_ID", "PROVIDER_PROJECT_ID",
                     "PROVIDER_ENTITY_ID", "PROVIDER_SESSION_ID"):
            assignment = re.search(rf"^{name}\s*=\s*(.+)$", source, re.MULTILINE)
            assert assignment, f"{name} not found in config"
            value = assignment.group(1)
            assert "os.getenv(" in value, f"{name} must be read from the environment"
            # No long literal anywhere on the line — that would be a baked-in secret.
            assert not re.search(r"['\"][A-Za-z0-9_\-]{12,}['\"]", value.replace(
                f'"DRAPE_{name}"', "")), f"{name} appears to contain a literal value"

    def test_image_size_default_is_aspect_stable(self):
        """auto_* picks its own aspect ratio and returned landscape once."""
        from app.config import DEFAULT_IMAGE_SIZE
        assert DEFAULT_IMAGE_SIZE == "portrait_4_3"
