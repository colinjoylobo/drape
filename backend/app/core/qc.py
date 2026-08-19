"""
Strict QC judging, plus the failure-to-fix mapping that turns a verdict into an
actionable repair.

Two changes over the original judge:

  * A failure is re-checked before it is believed. The original treated a single
    FAIL as final, and a meaningful share of the failures we saw were transport
    glitches or garbled JSON on images that were actually fine. Re-running the
    judge and only trusting a failure that repeats ON THE SAME CRITERION removes
    that whole class for the cost of one extra Flash call. A pass is never
    re-checked — this makes the judge harder to fool, not softer.
  * The judge is shown the prompt. Without it, an intentional pairing piece is
    indistinguishable from an invented garment, and the judge cannot tell a
    deliberate styling choice from a model hallucination.
"""
from typing import Any, Dict, List, Optional

from .vision import VisionBlocked, VisionError, call_vision_retry, image_part

RUBRIC = """You are a strict quality-control inspector for an AI fashion try-on pipeline.
You will be shown reference images (the model's identity, and the real garment product photos),
the prompt that was used, and finally ONE generated output image to judge.

Judge the output against the references with zero tolerance. Score each criterion independently.

1. avatar_identity — Same face, bone structure, body type and skin tone as the model reference?
   Pose, expression and styling are expected to differ; IDENTITY must not.
2. garment_color — Do the garment colour(s) match the reference exactly? Watch for warm/cool drift
   (a cool grey rendered warm, an off-white rendered beige) — that is a FAIL, not a nuance.
3. garment_pattern — Does the print/pattern/texture match in TYPE (floral, dots and animal print are
   all different from each other) and in SCALE/density?
4. garment_structure — Do straps, neckline, hem, cutouts, hardware, closures and seams match the
   reference garment's actual construction? Flag anything invented, simplified or omitted — including
   details the prompt explicitly said were absent.
5. coverage_and_pieces — Are ALL the garment's pieces present and worn? Flag any missing piece, and
   flag ANY exposure beyond what the reference garment itself would show. If the prompt specified an
   added pairing piece for coverage, that piece must actually be present.
6. photorealism — Does it read as a real photograph? Flag extra or malformed limbs and hands, warped
   jewellery or hardware, impossible fabric behaviour, or lighting inconsistent with the scene.
7. presence — Does she look like a comfortable, present, approachable person? FAIL this if she looks
   stiff, mannequin-like, blank-eyed, or awkwardly posed — this is a branding shoot and a lifeless
   model is a defect, not a matter of taste. Being merely calm or neutral is fine; being frozen is not.

Judge only the criteria listed. Do not fail an image for creative choices the prompt asked for.

Return ONLY valid JSON, no markdown fences:
{
  "checks": [{"criterion": "avatar_identity", "pass": true, "reason": "..."}],
  "overall_pass": true,
  "summary": "one short paragraph"
}
"overall_pass" must be false if ANY check fails."""

CRITERIA = ["avatar_identity", "garment_color", "garment_pattern", "garment_structure",
            "coverage_and_pieces", "photorealism", "presence"]


def _judge_once(reference_paths: List[str], output_path: str, prompt: Optional[str],
                context_note: str = "") -> Dict[str, Any]:
    parts: List[Dict[str, Any]] = [{"text": RUBRIC}]
    if context_note:
        parts.append({"text": f"\nContext for this check: {context_note}"})
    parts.append({"text": "\nREFERENCE IMAGES:"})
    for rp in reference_paths:
        parts.append(image_part(rp))
    if prompt:
        parts.append({"text": f"\nPROMPT THAT WAS USED:\n{prompt}"})
    parts.append({"text": "\nGENERATED OUTPUT TO JUDGE:"})
    parts.append(image_part(output_path))
    # Retry transient failures (network, malformed JSON) rather than reporting them
    # as "QC error" — a dropped call says nothing about the image. A refusal is a
    # real signal and is never retried away.
    return call_vision_retry(parts, temperature=0.0)


def _hard_fail(reason: str) -> Dict[str, Any]:
    return {"overall_pass": False, "checks": [], "summary": reason, "confirmed": True}


def qc_check(reference_paths: List[str], output_path: str, prompt: Optional[str] = None,
             context_note: str = "") -> Dict[str, Any]:
    """Judge one output. On failure, re-judge once and keep only the criteria that
    failed BOTH times; if nothing fails twice, the first verdict was noise and the
    image passes."""
    try:
        first = _judge_once(reference_paths, output_path, prompt, context_note)
    except VisionBlocked as e:
        # A refusal is a real signal about the image, never a pass.
        return _hard_fail(f"QC model refused to process this image ({e}). "
                          f"An unjudgeable image is treated as a hard failure.")
    except VisionError as e:
        return {"overall_pass": None, "checks": [], "error": str(e), "confirmed": False}

    if first.get("overall_pass"):
        first["confirmed"] = True
        return first

    failed_first = {c["criterion"] for c in first.get("checks", []) if not c.get("pass")}

    try:
        second = _judge_once(reference_paths, output_path, prompt, context_note)
    except VisionError:
        # Could not corroborate; report the failure but mark it unconfirmed so the
        # UI can say so rather than presenting a maybe-glitch as a defect.
        first["confirmed"] = False
        return first

    failed_second = {c["criterion"] for c in second.get("checks", []) if not c.get("pass")}
    repeated = failed_first & failed_second

    if not repeated:
        second["confirmed"] = True
        second["overall_pass"] = True
        second["summary"] = (
            "First QC pass reported a failure that did not reproduce on re-check; treated as a "
            "transient misread. " + (second.get("summary") or ""))
        return second

    checks = []
    reasons_second = {c["criterion"]: c.get("reason", "") for c in second.get("checks", [])}
    for c in first.get("checks", []):
        crit = c["criterion"]
        passed = crit not in repeated
        checks.append({"criterion": crit, "pass": passed,
                       "reason": c.get("reason", "") if not passed else
                       (c.get("reason") or reasons_second.get(crit, ""))})
    return {"checks": checks, "overall_pass": False, "confirmed": True,
            "summary": first.get("summary", ""),
            "corroborated_failures": sorted(repeated)}


# --------------------------------------------------------------------------
# Failure -> fix. Every mapping below is a repair we worked out by hand during
# the first catalogue run; encoding them is what stops the same diagnosis being
# re-derived by a human every time.
# --------------------------------------------------------------------------
def suggest_repair(qc_result: Dict[str, Any], analysis: Dict[str, Any],
                   look_text: str, has_detail_refs: bool,
                   avatar_ref_count: int = 1,
                   has_colour_ref: bool = False) -> Optional[Dict[str, Any]]:
    """Return a concrete, user-approvable repair for the first corroborated failure,
    or None if there is nothing actionable. Never applied automatically — regeneration
    costs credits, so the user approves it."""
    if qc_result.get("overall_pass") is not False:
        return None

    failed = [c for c in qc_result.get("checks", []) if not c.get("pass")]
    if not failed:
        return None

    order = {c: i for i, c in enumerate(CRITERIA)}
    failed.sort(key=lambda c: order.get(c["criterion"], 99))
    top = failed[0]
    crit, reason = top["criterion"], top.get("reason", "")
    desc = analysis.get("garment_desc", "")
    seated = any(w in look_text.lower() for w in ("seated", "sitting", "sits", "cross-legged", "crossed"))
    flowy = any(w in desc.lower() for w in ("slit", "sheer", "a-line", "flowing", "chiffon", "wrap"))

    if crit == "garment_color":
        # Wording alone is a weak lever here: "match the exact shade" corrected a
        # grey dress once and then failed twice on a beige, because an adjective
        # cannot specify a colour precisely. Sending a cropped patch of the actual
        # fabric gives the model pixels to match instead of a word to interpret.
        if not has_colour_ref:
            return {
                "criterion": crit, "reason": reason,
                "action": "add_colour_reference",
                "label": "Send a close-up of the fabric colour",
                "detail": "Colour drifted. A colour name is an approximation — a cropped patch of "
                          "the actual fabric is the ground truth, and it works where stronger "
                          "wording does not.",
                "extra_direction": "CRITICAL COLOUR ACCURACY: the garment's colour must match the "
                                   "fabric close-up exactly, including its warmth or coolness. Do "
                                   "not shift it warmer or cooler and do not substitute a "
                                   "neighbouring shade.",
            }
        return {
            "criterion": crit, "reason": reason,
            "action": "amend_prompt",
            "label": "Pin the colour temperature explicitly",
            "detail": "Colour still drifted despite a fabric close-up. Naming the shade's "
                      "temperature and what it must NOT drift toward is the remaining lever.",
            "extra_direction": "CRITICAL COLOUR ACCURACY: match the garment's exact shade and its "
                               "temperature to the reference. Do not shift it warmer or cooler, and "
                               "do not substitute a neighbouring shade.",
        }

    if crit in ("garment_pattern", "garment_structure"):
        if not has_detail_refs:
            return {
                "criterion": crit, "reason": reason,
                "action": "add_detail_crop",
                "label": "Add a close-up reference of this detail",
                "detail": "No close-up was sent. A full-garment shot does not resolve fine print or "
                          "fine construction at generation resolution — a tight crop is what fixes this.",
            }
        return {
            "criterion": crit, "reason": reason,
            "action": "tighten_detail_crop",
            "label": "Tighten the close-up and state the detail geometrically",
            "detail": "A close-up was already sent but the detail still drifted. Crop tighter on the "
                      "feature and describe it in explicit geometric terms rather than by name.",
            "extra_direction": f"Pay particular attention to this, which was previously rendered "
                               f"incorrectly: {reason}",
        }

    if crit == "coverage_and_pieces":
        if seated and flowy:
            return {
                "criterion": crit, "reason": reason,
                "action": "change_pose_standing",
                "label": "Switch to a standing pose",
                "detail": "This garment's coverage depends on fabric hanging straight down, and the "
                          "look is seated. Crossing or bending the legs pulls it open — strengthening "
                          "the wording does not fix this, changing the pose does.",
            }
        return {
            "criterion": crit, "reason": reason,
            "action": "amend_prompt",
            "label": "Require unbroken coverage explicitly",
            "detail": "A piece was missing or coverage broke down.",
            "extra_direction": "COVERAGE: every piece listed must be present and worn. Any opaque "
                               "layer or lining must render as one continuous, unbroken panel with no "
                               "gaps, no thinner strips and no exposed skin where it should cover.",
        }

    if crit == "avatar_identity":
        if avatar_ref_count < 2:
            return {
                "criterion": crit, "reason": reason,
                "action": "add_avatar_ref",
                "label": "Send a second angle of the model",
                "detail": "Identity drifted from a single reference. A second angle of the same person "
                          "gives the model more to anchor her face to.",
            }
        return {
            "criterion": crit, "reason": reason,
            "action": "amend_prompt",
            "label": "Strengthen the identity lock",
            "detail": "Identity drifted despite two references.",
            "extra_direction": "IDENTITY LOCK: the model's face, bone structure and skin tone must "
                               "match the model reference images exactly. Keep her front-facing or at "
                               "most a gentle three-quarter turn, and keep the light on her face soft "
                               "and even regardless of the scene's mood.",
        }

    if crit == "presence":
        return {
            "criterion": crit, "reason": reason,
            "action": "amend_prompt",
            "label": "Ask for a warmer, more relaxed presence",
            "detail": "She reads as stiff or switched-off — a defect in a branding shot.",
            "extra_direction": "PRESENCE: she should look comfortable and approachable — relaxed "
                               "shoulders, weight settled on one hip, hands doing something natural, "
                               "warm engaged eyes and a soft closed-lip smile. Not posed, not frozen, "
                               "not blank.",
        }

    if crit == "photorealism":
        return {
            "criterion": crit, "reason": reason,
            "action": "regenerate",
            "label": "Regenerate (rendering artefact)",
            "detail": "Artefacts like malformed hands are stochastic rather than caused by the prompt; "
                      "the reliable fix is another sample of the same prompt.",
        }

    return None
