"""
Produce real detail-crop references from the extractor's bounding boxes.

This closes the gap that silently degraded every run of the original pipeline:
it flagged `needs_detail_crop` and named a source image, then passed the WHOLE
source photo through as the "detail" reference — frequently the very same file
already sent as the front reference. The generator therefore never once received
the close-up it had asked for, and every fine-print / fine-strap failure had to be
repaired by hand.
"""
import hashlib
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image, ImageOps

from ..config import STORAGE_DIR

CROP_DIR = STORAGE_DIR / "crops"
# Generation-side references stop gaining detail past roughly this size, and
# upscaling beyond the source's real resolution only adds interpolation blur.
TARGET_LONG_EDGE = 1400
# Grow the model's tight box slightly so the feature is not cut flush at the edge;
# a little surrounding fabric helps the model read scale and context.
PAD_FRACTION = 0.08


def _crop_path(source: Path, box: List[int]) -> Path:
    key = hashlib.sha1(f"{source}:{box}".encode()).hexdigest()[:16]
    return CROP_DIR / f"{source.stem}_{key}.jpg"


def make_crop(source_path: str | Path, box_2d: List[int]) -> Path:
    """Crop `source_path` at a normalized [ymin, xmin, ymax, xmax] 0-1000 box and
    upscale toward TARGET_LONG_EDGE. Cached by (source, box)."""
    source = Path(source_path)
    out = _crop_path(source, box_2d)
    if out.exists():
        return out

    img = ImageOps.exif_transpose(Image.open(source)).convert("RGB")
    w, h = img.size
    ymin, xmin, ymax, xmax = box_2d

    pad_x = (xmax - xmin) * PAD_FRACTION
    pad_y = (ymax - ymin) * PAD_FRACTION
    left = max(0, (xmin - pad_x) / 1000 * w)
    right = min(w, (xmax + pad_x) / 1000 * w)
    top = max(0, (ymin - pad_y) / 1000 * h)
    bottom = min(h, (ymax + pad_y) / 1000 * h)

    if right - left < 8 or bottom - top < 8:
        raise ValueError(f"degenerate crop box {box_2d} on {source.name}")

    crop = img.crop((int(left), int(top), int(right), int(bottom)))

    long_edge = max(crop.size)
    if long_edge < TARGET_LONG_EDGE:
        scale = min(TARGET_LONG_EDGE / long_edge, 4.0)
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)

    CROP_DIR.mkdir(parents=True, exist_ok=True)
    crop.save(out, format="JPEG", quality=95)
    return out


def build_detail_refs(detail_regions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Turn normalized regions into concrete crop files, skipping any that fail
    rather than aborting the whole garment."""
    refs = []
    for region in detail_regions or []:
        source = region.get("source_path")
        box = region.get("box_2d")
        if not source or not box or not Path(source).exists():
            continue
        try:
            path = make_crop(source, box)
        except (ValueError, OSError):
            continue
        refs.append({"path": str(path), "why": region.get("why", ""),
                     "source_path": source, "box_2d": box})
    return refs
