"""
Isolate the garment from a product photo that was shot on a person.

Why this exists: a reference photo of a model wearing the garment carries that
person's face, hair, body and styling. A multi-reference editor treats every
reference as instruction, so the person in the reference competes with — and
frequently overrides — the model you actually chose, and their backdrop leaks in
with them. Cropping to the garment removes the competing identity at source, which
works far better than asking the model in words to ignore a face it can see.

The crop is generous rather than tight: unlike a detail close-up, this reference
still has to communicate the garment's whole silhouette.
"""
import hashlib
from pathlib import Path
from typing import List, Optional

from PIL import Image, ImageOps

from ..config import STORAGE_DIR

CROP_DIR = STORAGE_DIR / "crops"
# Keep the whole garment readable; this is the silhouette reference, not a detail.
MAX_LONG_EDGE = 1800
PAD_FRACTION = 0.04


def _out_path(source: Path, box: List[int]) -> Path:
    key = hashlib.sha1(f"garment:{source}:{box}".encode()).hexdigest()[:16]
    return CROP_DIR / f"{source.stem}_garment_{key}.jpg"


def crop_garment(source_path: str | Path, box_2d: List[int]) -> Optional[Path]:
    """Crop `source_path` to a normalized [ymin, xmin, ymax, xmax] 0-1000 garment box.

    Returns None rather than raising when the box is unusable — a failed crop must
    fall back to the original photo, never block the garment from being generated.
    """
    source = Path(source_path)
    if not source.exists():
        return None
    out = _out_path(source, box_2d)
    if out.exists():
        return out

    try:
        img = ImageOps.exif_transpose(Image.open(source)).convert("RGB")
    except (OSError, ValueError):
        return None

    w, h = img.size
    ymin, xmin, ymax, xmax = box_2d
    pad_x = (xmax - xmin) * PAD_FRACTION
    pad_y = (ymax - ymin) * PAD_FRACTION
    left = max(0, (xmin - pad_x) / 1000 * w)
    right = min(w, (xmax + pad_x) / 1000 * w)
    # No padding upward: the top edge is deliberately placed below the person's
    # head, and padding it back up is exactly how a face creeps into the crop.
    top = max(0, ymin / 1000 * h)
    bottom = min(h, (ymax + pad_y) / 1000 * h)

    if right - left < 40 or bottom - top < 40:
        return None

    crop = img.crop((int(left), int(top), int(right), int(bottom)))
    if max(crop.size) > MAX_LONG_EDGE:
        crop.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE), Image.LANCZOS)

    CROP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        crop.save(out, format="JPEG", quality=95)
    except OSError:
        return None
    return out


def resolve_garment_ref(image_row: dict) -> tuple[str, bool]:
    """Pick the file to send for a garment reference.

    Returns (path, was_cropped). Falls back to the original whenever the photo has
    no person in it, carries no box, or the crop fails.
    """
    path = image_row.get("path")
    if not image_row.get("contains_person"):
        return path, False

    box = image_row.get("garment_box")
    if not (isinstance(box, list) and len(box) == 4):
        return path, False

    cropped = crop_garment(path, box)
    return (str(cropped), True) if cropped else (path, False)
