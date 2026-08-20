#!/usr/bin/env python
"""
Bring externally-referenced images into the repo so the bundled data works on a
fresh clone.

Drape normally references garment photography where it already sits, which is
right for a working install and useless for shipped sample data: the database
stores absolute paths that exist on exactly one machine.

This copies every referenced source into `backend/storage/`, rewrites the
database to point at the copies, and downscales the originals on the way in.
Camera-resolution product photography is ~630 MB across this dataset, which is
more than a git repository should carry; 2000px on the long edge preserves
everything the sample is for — seeing the app work, and regenerating from it —
at a fraction of the size. Generated shots are left untouched, since they are the
work product.

    ./.venv/bin/python bundle_sample_data.py [--max-dim 2000] [--dry-run]

Idempotent: files already inside storage/ are left alone.
"""
import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import STORAGE_DIR  # noqa: E402
from app.db import get_db  # noqa: E402

SOURCES = STORAGE_DIR / "sources"


def _safe_name(path: Path, seen: dict) -> str:
    """Flat, collision-free filename. Product shoots reuse filenames across
    folders, so the parent directory is folded into the name."""
    base = f"{path.parent.name}__{path.name}".replace(" ", "_")
    if base in seen and seen[base] != str(path):
        stem, suffix = Path(base).stem, Path(base).suffix
        n = 2
        while f"{stem}_{n}{suffix}" in seen:
            n += 1
        base = f"{stem}_{n}{suffix}"
    seen[base] = str(path)
    return base


def _copy_in(src: Path, dest: Path, max_dim: int) -> bool:
    """Copy `src` to `dest` (always .jpg), downscaled to `max_dim` on the long edge."""
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        img = ImageOps.exif_transpose(Image.open(src)).convert("RGB")
    except (OSError, ValueError):
        shutil.copy2(src, dest)          # not an image we can process; keep as-is
        return True
    if max_dim and max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    # EXIF rotation is baked in above, so the copy carries no orientation tag.
    img.save(dest, format="JPEG", quality=88)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-dim", type=int, default=2000,
                    help="long edge for copied source photography (0 = keep full size)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    storage = STORAGE_DIR.resolve()
    seen: dict = {}
    moved = kept = 0
    bytes_before = bytes_after = 0

    with get_db() as conn:
        images = conn.execute("SELECT id, path FROM garment_images").fetchall()
        avatars = conn.execute(
            "SELECT id, front_path, back_path FROM avatars").fetchall()

    plan = []   # (table, id, column, old, new)

    for row in images:
        old = Path(row["path"])
        if str(old.resolve()).startswith(str(storage)):
            kept += 1
            continue
        new = SOURCES / _safe_name(old, seen)
        new = new.with_suffix(".jpg")
        plan.append(("garment_images", row["id"], "path", old, new))

    for row in avatars:
        for col in ("front_path", "back_path"):
            if not row[col]:
                continue
            old = Path(row[col])
            if str(old.resolve()).startswith(str(storage)):
                kept += 1
                continue
            new = STORAGE_DIR / "avatars" / _safe_name(old, seen)
            new = new.with_suffix(".jpg")
            plan.append(("avatars", row["id"], col, old, new))

    print(f"{len(plan)} file(s) to bring in, {kept} already inside storage/")
    if args.dry_run:
        for _, _, _, old, new in plan[:8]:
            print(f"  {old.name}  ->  storage/{new.relative_to(STORAGE_DIR)}")
        if len(plan) > 8:
            print(f"  … and {len(plan) - 8} more")
        return

    for table, row_id, col, old, new in plan:
        if not old.exists():
            print(f"  missing, skipped: {old}")
            continue
        bytes_before += old.stat().st_size
        _copy_in(old, new, args.max_dim)
        bytes_after += new.stat().st_size if new.exists() else 0
        with get_db() as conn:
            conn.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (str(new), row_id))
        moved += 1

    # detail_regions embeds source paths of its own; keep them consistent with the
    # rows above or the crops silently stop resolving.
    import json
    with get_db() as conn:
        rows = conn.execute(
            "SELECT garment_id, detail_regions FROM garment_analysis "
            "WHERE detail_regions IS NOT NULL AND detail_regions != '[]'").fetchall()
        remap = {str(old): str(new) for _, _, _, old, new in plan}
        for row in rows:
            regions = json.loads(row["detail_regions"])
            changed = False
            for region in regions:
                src = region.get("source_path")
                if src in remap:
                    region["source_path"] = remap[src]
                    changed = True
            if changed:
                conn.execute(
                    "UPDATE garment_analysis SET detail_regions=? WHERE garment_id=?",
                    (json.dumps(regions), row["garment_id"]))

    print(f"copied {moved} file(s): {bytes_before/1e6:.0f} MB -> {bytes_after/1e6:.0f} MB")
    print("database now points at storage/; run rebase_paths.py after cloning elsewhere")


if __name__ == "__main__":
    main()
