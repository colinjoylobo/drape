"""
Assembles the generation prompt and the ordered reference list.

Design notes that were each paid for in failed generations:

  * Every reference image is given an explicit ROLE in the prompt text. Naming only
    "image 1" and going silent leaves a grounded, region-precise editor guessing
    whether image 3 is the back of the garment or a print close-up.
  * A garment reference shot ON a person is cropped to the garment before it is
    sent, and the prompt says so. Otherwise that person's face and styling compete
    with — and often beat — the model that was actually chosen.
  * The back reference is included only when the extractor found back-visible
    construction, or when the shot itself is a back view.

The reference list and the prompt text are produced together by design — they must
never be able to disagree about what image N is.
"""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .lessons import lessons_block
from .shoot_style import DEFAULT_PROFILE, craft_block

CLOSING = ("Editorial branding photography, photorealistic, natural skin texture, sharp focus, "
           "high detail fabric texture, no text or watermarks.")

# Said once, near the top, where it governs every garment reference that follows.
IGNORE_PERSON = (
    "IMPORTANT: some garment references may show the garment worn by a different person, or "
    "cropped from such a photo. That person is NOT the model. Ignore their face, hair, body, skin "
    "tone, pose, styling and background completely — take ONLY the garment from those images. The "
    "model is defined solely by the model reference image."
)

BACK_VIEW = (
    "THIS IS A BACK VIEW SHOT. Photograph her from behind so the back of the garment is the subject "
    "of the frame and its full back construction is clearly visible. Her face may be turned away or "
    "only partially visible in a soft over-the-shoulder glance — that is expected here. Keep her "
    "hair, body type, proportions and skin tone exactly as the model reference shows, since those "
    "are what identify her in a shot where the face does not."
)


def build(
    analysis: Dict[str, Any],
    look_text: str,
    avatar_front: str,
    avatar_back: Optional[str] = None,
    garment_front: Optional[str] = None,
    garment_back: Optional[str] = None,
    detail_refs: Optional[List[Dict[str, Any]]] = None,
    avatar_ref_count: int = 1,
    extra_direction: Optional[str] = None,
    props: Optional[str] = None,
    model_styling: Optional[str] = None,
    view: str = "front",
    garment_front_cropped: bool = False,
    garment_back_cropped: bool = False,
    category: Optional[str] = None,
    profile: str = DEFAULT_PROFILE,
) -> Tuple[str, List[str], List[Dict[str, str]]]:
    """Returns (prompt, ref_paths, ref_manifest).

    ref_manifest describes what each slot is, for display in the UI and for storing
    alongside the generation so a past run can be understood later.
    """
    is_back = view == "back"
    refs: List[str] = []
    manifest: List[Dict[str, str]] = []
    lines: List[str] = []

    def add(path: str, kind: str, description: str):
        refs.append(path)
        manifest.append({"index": len(refs), "kind": kind, "path": path,
                         "description": description, "filename": Path(path).name})
        lines.append(f"IMAGE {len(refs)} = {description}")

    # For a back view the model's own back is the better identity anchor: it carries
    # her hair from behind, which is what the viewer actually reads.
    primary_avatar = (avatar_back or avatar_front) if is_back else avatar_front
    secondary_avatar = avatar_front if (is_back and avatar_back) else avatar_back

    add(primary_avatar, "avatar_back" if (is_back and avatar_back) else "avatar_front",
        "the MODEL. Preserve her face, bone structure, identity, body type, hair and skin tone "
        "exactly as shown. She is the person in every output; do not restyle her face or alter "
        "her proportions")

    if (avatar_ref_count >= 2 or is_back) and secondary_avatar:
        add(secondary_avatar, "avatar_second",
            "the SAME model from another angle, provided only as additional identity reference — "
            "match her to both images; do not copy this image's pose or framing")

    worn_note = (" (this image has been cropped from a photo of a different person wearing it — "
                 "use the garment only)")

    # On a back view the garment's back is the subject, so it leads.
    if is_back and garment_back:
        add(garment_back, "garment_back",
            "the GARMENT, back view — this is the product being sold and the subject of this shot. "
            "Reproduce its back colour, print and construction exactly"
            + (worn_note if garment_back_cropped else ""))
        if garment_front:
            add(garment_front, "garment_front",
                "the GARMENT, front view — for colour, fabric and overall silhouette reference"
                + (worn_note if garment_front_cropped else ""))
    else:
        if garment_front:
            add(garment_front, "garment_front",
                "the GARMENT, front view. This is the product being sold — reproduce its colour, "
                "print and construction exactly"
                + (worn_note if garment_front_cropped else ""))
        if garment_back and analysis.get("back_has_structure"):
            add(garment_back, "garment_back",
                "the GARMENT, back view — reproduce its back construction exactly where visible"
                + (worn_note if garment_back_cropped else ""))

    for ref in detail_refs or []:
        why = (ref.get("why") or "fine detail").strip().rstrip(".")
        add(ref["path"], "detail_crop",
            f"a CLOSE-UP of the garment showing {why} — match this at the correct scale and density, "
            f"this is the ground truth for that detail")

    # ---- prompt text ----
    prompt = (
        "Dress the model in the exact garment shown in the reference images.\n\n"
        "REFERENCE IMAGES:\n" + "\n".join(lines) + "\n\n"
    )

    if garment_front_cropped or garment_back_cropped:
        prompt += IGNORE_PERSON + "\n\n"

    prompt += (
        f"THE GARMENT: {analysis['garment_desc']}\n\n"
        "Reproduce the garment's colour, pattern type, pattern scale and construction EXACTLY as the "
        "references show. Do not simplify, restyle, or reinterpret it, and do not add details that are "
        "not present in the references.\n\n"
    )

    pieces = analysis.get("pieces") or []
    if len(pieces) > 1:
        prompt += (f"She must be wearing ALL of these pieces together as the matching set they are: "
                   f"{', '.join(pieces)}.\n\n")

    if analysis.get("coverage_risk") and analysis.get("pairing_note"):
        prompt += (f"{analysis['pairing_note']} This added piece is supporting styling — keep it plain "
                   f"and secondary so it never competes with the product being photographed.\n\n")

    if is_back:
        prompt += BACK_VIEW + "\n\n"

    prompt += f"POSE, SETTING AND LIGHTING: {look_text}\n\n"

    # Styling comes after the garment and carries an explicit subordination clause.
    # Props earn their place by making the frame look photographed rather than
    # composited, but every one of them is a chance to obscure the product.
    styling_bits = [b.strip() for b in (model_styling, props) if b and b.strip()]
    if styling_bits:
        prompt += (f"STYLING AND PROPS: {'; '.join(styling_bits)}. Keep all of this secondary to "
                   f"the garment — nothing may cover, overlap or draw the eye away from the product, "
                   f"and nothing may obscure her face.\n\n")

    craft = craft_block(category, profile)
    if craft:
        prompt += craft + "\n\n"

    # Hard-won corrections come last, closest to the instruction boundary, where
    # they read as overrides rather than background.
    learned = lessons_block(category) if profile == "v2" else ""
    if learned:
        prompt += learned + "\n\n"

    if extra_direction:
        prompt += f"{extra_direction.strip()}\n\n"

    prompt += CLOSING
    return prompt, refs, manifest
