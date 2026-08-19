"""
Factual garment extraction — deterministic (temperature 0).

Deliberately does NOT propose looks. Art direction is a creative task and was
previously fused into this same temperature-0 call, which is why proposed scenes
collapsed onto the same handful of backdrops. See art_director.py.

Two things this adds over the original analyzer:
  * detail_regions — actual normalized bounding boxes for the fine detail that a
    full-garment shot won't resolve, so the crop can be produced mechanically
    instead of by hand.
  * back_has_structure — lets the prompt builder drop the back reference when it
    contributes nothing to a front-facing render but still competes for attention.
"""
import re
from pathlib import Path
from typing import Any, Dict, List

from .vision import call_vision_retry, image_part

# Extra clearance (0-1000 units) dropped below the detected head before the garment
# crop starts, so a jaw or hair tail cannot survive into the reference.
HEAD_CLEARANCE = 60

SKIP_PATTERNS = re.compile(r"(wash\s*care|washtag|_tag\b|\btag\.|barcode)", re.IGNORECASE)
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

RUBRIC = """You are analyzing a fashion product photo shoot for ONE garment (or one matching
garment SET) to prepare it for an AI virtual try-on pipeline. You will be shown every image from
the garment's folder, labeled "IMAGE 1", "IMAGE 2", etc.

Report only what you can SEE. Do not invent, do not propose styling, do not describe poses or
locations — that is a separate step.

1. IMAGE CLASSIFICATION — for every image give:
   role: exactly one of "full_front", "full_back", "close_up_detail",
     "flat_lay_or_other_angle", "irrelevant" (swatch chip, size tag, wash-care label, barcode,
     packaging, or anything not showing the garment itself).
   contains_person: true if a HUMAN is wearing the garment in that image (a mannequin, dress form
     or flat-lay is NOT a person — set false for those).
   garment_box: when contains_person is true, a bounding box around the GARMENT ONLY —
     "box_2d": [ymin, xmin, ymax, xmax], each 0-1000 normalized to that image.
     Bound the garment itself from its topmost edge (shoulder seam, neckline or waistband) to its
     lowest hem, plus its sleeves. Exclude other garments they happen to be wearing.
     Omit garment_box when contains_person is false.
   head_box: when contains_person is true, a box around the person's HEAD AND HAIR —
     [ymin, xmin, ymax, xmax], 0-1000. Include all of their hair, down to where it ends.
     This is used to guarantee the face is cut out of the reference, so err on the side of a
     box that is slightly TOO LARGE at the bottom rather than too small.
     Omit head_box when contains_person is false.

2. GARMENT DESCRIPTION — one dense paragraph containing exactly what is needed to reproduce this
   garment: base colour(s), print/pattern TYPE and density, silhouette/cut, and construction
   (neckline, sleeve type, hem, straps, closures, hardware). This text is used verbatim in a
   generation prompt, so avoid vague words like "nice" or "stylish".

   COLOUR PRECISION — generation models drift warm/cool easily ("off-white" drifts to beige,
   "light grey" drifts to warm off-white). For any pale or neutral shade, name its temperature
   explicitly, e.g. "a COOL light grey, not warm off-white or beige".

   NEGATIVE DETAIL — if the garment plainly LACKS a feature a model tends to hallucinate onto this
   kind of piece (a drawstring on a plain elastic waistband, a keyhole cutout on a plain neckline,
   a belt on a plain waist), say so explicitly: "plain elastic waistband with NO drawstring".

   LAYERED SHEER + OPAQUE — if a sheer/mesh outer layer sits over an opaque inner layer or lining,
   state that the opaque layer must render as ONE CONTINUOUS UNBROKEN panel with no gaps, no
   thinner/more-translucent strips and no visible skin. Models tend to render such linings patchier
   than reference, and flowing A-line skirts in particular can appear to split down the centre.

3. PIECES — every distinct physical piece, e.g. ["bra_top","briefs"], ["dress"], ["leggings"].
   If you can only see one piece across all images, list only that one. Never assume a matching
   piece exists because the category usually has one.

4. COVERAGE RISK (safety-critical — conservative, but do not over-trigger)
   Set coverage_risk = true ONLY when a piece is genuinely MISSING relative to what the references
   show, such that generating the listed piece(s) alone would leave the torso or lower body
   inappropriately exposed — e.g. a bra with no bottom shown anywhere, a thong with no top shown
   anywhere, a sheer layer clearly meant to be worn over something else.

   Do NOT set coverage_risk = true for a COMPLETE matching set (e.g. bra AND briefs both shown)
   merely because it is revealing by design. That is the intended, complete product; adding
   something more covering would obscure the very thing being marketed. Coverage risk is about a
   MISSING piece, never about how much skin a complete intentional garment shows.

   BE CONSISTENT for tops-only and bottoms-only: if the references show ONLY a top (any length) with
   no bottom anywhere, OR ONLY a bottom with no top anywhere, that IS a missing piece and
   coverage_risk MUST be true every time — never judge two similar top-only garments differently.

   If true, write pairing_note: one sentence specifying a plain, unprinted, fully-covering
   complementary garment. This is a BRANDING shoot, so it must read as a deliberate outfit rather
   than filler — describe it as coordinating with the tested garment's own palette (e.g. "a simple
   fitted top in a shade that complements the navy skirt, such as cream or dusty blue") rather than
   defaulting to plain white. Allow a small coordinated range rather than one exact colour, since
   this note is reused across multiple looks of the same garment and the piece may vary between them.
   If coverage_risk is false, pairing_note must be an empty string.

5. DETAIL REGIONS — the reference photos will be cropped and upscaled at these coordinates to give
   the generator a legible close-up, so this must be precise. Return one entry for each fine
   feature that a full-garment shot will not resolve at generation resolution: a small/dense repeating
   print, thin strap routing, buckles or hardware, an unusual neckline or seam construction.
   For each, give the source image label, a tight bounding box, and why it matters.
   Box format: "box_2d": [ymin, xmin, ymax, xmax], each 0-1000 normalized to that image.
   Crop TIGHT around the feature — a box covering most of the frame is useless. Prefer a region from
   an image already classified close_up_detail; otherwise the sharpest full_front/full_back.
   Return 1-3 regions, or an empty list if the garment is genuinely plain (a solid-colour piece with
   simple construction needs none).

6. BACK STRUCTURE — set back_has_structure = true only if the BACK of the garment carries
   construction that a front-facing photograph would not reveal (strap crossing/racerback, a tie or
   lace-up, a keyhole, a distinct back print). If the back is simply the plain reverse of the front,
   set false.

Return ONLY valid JSON, no markdown fences:
{
  "image_classification": [
    {"image": "IMAGE 1", "role": "full_front", "contains_person": true,
     "garment_box": [300, 120, 880, 900], "head_box": [40, 330, 290, 680]}
  ],
  "garment_desc": "...",
  "pieces": ["..."],
  "coverage_risk": false,
  "pairing_note": "",
  "back_has_structure": false,
  "detail_regions": [{"image": "IMAGE 3", "box_2d": [120, 300, 380, 620], "why": "ditsy floral print scale"}]
}"""


def list_garment_images(folder: Path) -> List[Path]:
    return [p for p in sorted(folder.iterdir())
            if p.suffix.lower() in IMAGE_EXTS and not SKIP_PATTERNS.search(p.name)]


def extract(image_paths: List[Path], category: str | None = None) -> Dict[str, Any]:
    """Run factual extraction over an ordered list of image paths.

    The order of image_paths defines the IMAGE N labels, so callers must keep the
    same ordering when resolving labels back to files.
    """
    if not image_paths:
        raise ValueError("no images to analyze")

    parts: List[Dict[str, Any]] = [{"text": RUBRIC}]
    if category:
        parts.append({"text": f"\nProduct CATEGORY: {category}"})
    for i, path in enumerate(image_paths, 1):
        parts.append({"text": f"\nIMAGE {i} ({path.name}):"})
        parts.append(image_part(path))

    result = call_vision_retry(parts, temperature=0.0)
    result["image_files"] = [str(p) for p in image_paths]
    return _normalize(result, image_paths)


def label_to_index(label: str) -> int | None:
    m = re.search(r"\d+", label or "")
    return int(m.group()) - 1 if m else None


def _normalize(result: Dict[str, Any], image_paths: List[Path]) -> Dict[str, Any]:
    """Coerce the model's output into the shapes the rest of the pipeline assumes,
    and drop detail regions that point at a non-existent image or an untrustworthy
    box (inverted, or so large it isn't a crop at all)."""
    result.setdefault("pieces", [])
    result.setdefault("pairing_note", "")
    result["coverage_risk"] = bool(result.get("coverage_risk"))
    result["back_has_structure"] = bool(result.get("back_has_structure"))
    if not result["coverage_risk"]:
        result["pairing_note"] = ""

    for entry in result.get("image_classification") or []:
        entry["contains_person"] = bool(entry.get("contains_person"))
        box = entry.get("garment_box")
        if not entry["contains_person"] or not (isinstance(box, list) and len(box) == 4):
            entry["garment_box"] = None
            continue
        ymin, xmin, ymax, xmax = [max(0, min(1000, int(v))) for v in box]

        # Clamp the garment box to start below the head. Asking for a box that
        # "excludes the face" is not reliable — models routinely return a top edge
        # above the chin — but a separate head box plus this clamp is, and a face
        # surviving into the reference is the whole failure this prevents.
        head = entry.get("head_box")
        if isinstance(head, list) and len(head) == 4:
            head_bottom = max(0, min(1000, int(head[2])))
            entry["head_box"] = [max(0, min(1000, int(v))) for v in head]
            # Margin below the hairline: head boxes routinely stop at the chin and
            # clip the jaw, hair ends and neck back in.
            if head_bottom + HEAD_CLEARANCE > ymin:
                ymin = min(head_bottom + HEAD_CLEARANCE, ymax - 1)
        else:
            entry["head_box"] = None

        # A degenerate or near-full-frame box would keep the face in shot, which is
        # the exact thing this box exists to remove.
        if ymax - ymin < 60 or xmax - xmin < 60 or (ymax - ymin) * (xmax - xmin) > 940_000:
            entry["garment_box"] = None
        else:
            entry["garment_box"] = [ymin, xmin, ymax, xmax]

    clean_regions = []
    for region in result.get("detail_regions") or []:
        idx = label_to_index(region.get("image", ""))
        box = region.get("box_2d")
        if idx is None or not (0 <= idx < len(image_paths)):
            continue
        if not (isinstance(box, list) and len(box) == 4):
            continue
        ymin, xmin, ymax, xmax = [max(0, min(1000, int(v))) for v in box]
        if ymax <= ymin or xmax <= xmin:
            continue
        # A "crop" covering ~the whole frame carries no more information than the
        # original reference, so it is not worth an extra reference slot.
        if (ymax - ymin) * (xmax - xmin) > 810_000:  # >90% of each axis
            continue
        clean_regions.append({
            "image": region.get("image"),
            "image_index": idx,
            "source_path": str(image_paths[idx]),
            "box_2d": [ymin, xmin, ymax, xmax],
            "why": region.get("why", ""),
        })
    result["detail_regions"] = clean_regions[:3]
    return result


def resolve_references(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """Map classification back onto concrete paths."""
    image_files = analysis.get("image_files", [])
    front = back = None
    for entry in analysis.get("image_classification", []):
        idx = label_to_index(entry.get("image", ""))
        if idx is None or not (0 <= idx < len(image_files)):
            continue
        role = entry.get("role")
        if role == "full_front" and front is None:
            front = image_files[idx]
        elif role == "full_back" and back is None:
            back = image_files[idx]
    if front is None and image_files:
        front = image_files[0]
    return {"front": front, "back": back}
