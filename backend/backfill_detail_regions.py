"""
Backfill close-up crop regions onto garments that were imported without them.

The old pipeline format recorded only a boolean ("needs a close-up") and a source
image, never coordinates — so imported garments carry no regions and cannot show
what the close-up references actually do.

This runs the extractor purely to harvest `detail_regions`, and merges ONLY that
field. Descriptions, pieces, coverage flags and pairing notes are left exactly as
imported, because those are what produced the finished shots — replacing them
would leave the examples describing something other than what you can see.

Analysis-only: costs Gemini calls, never image generation.

    ./.venv/bin/python backfill_detail_regions.py [--session NAME] [--dry-run] [--force]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.detail_crop import build_detail_refs  # noqa: E402
from app.core.extractor import extract  # noqa: E402
from app.core.vision import VisionError  # noqa: E402
from app.db import get_db, row_to_dict, rows_to_dicts  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="Sample shoot",
                    help="session name to backfill (default: the imported examples)")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--force", action="store_true",
                    help="also redo garments that already have regions")
    args = ap.parse_args()

    with get_db() as conn:
        session = row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE name=?", (args.session,)).fetchone())
        if not session:
            sys.exit(f"no session named {args.session!r}")
        garments = rows_to_dicts(conn.execute(
            "SELECT * FROM garments WHERE session_id=? ORDER BY id", (session["id"],)).fetchall())

    done = skipped = failed = crops = 0

    for garment in garments:
        with get_db() as conn:
            analysis = row_to_dict(conn.execute(
                "SELECT * FROM garment_analysis WHERE garment_id=?", (garment["id"],)).fetchone(),
                json_cols=("detail_regions",))
            images = rows_to_dicts(conn.execute(
                """SELECT * FROM garment_images WHERE garment_id=?
                   ORDER BY sort_order, id""", (garment["id"],)).fetchall())

        if not analysis or not images:
            skipped += 1
            continue
        if (analysis.get("detail_regions") or []) and not args.force:
            skipped += 1
            continue

        paths = [Path(i["path"]) for i in images if Path(i["path"]).exists()]
        if not paths:
            print(f"  {garment['name']}: source photos missing, skipped")
            skipped += 1
            continue

        try:
            result = extract(paths, category=garment.get("category"))
        except VisionError as e:
            print(f"  {garment['name']}: {e}")
            failed += 1
            continue

        regions = result.get("detail_regions") or []
        # back_has_structure is safe to adopt too: it is a fact about the garment
        # rather than creative direction, and imported rows all default to false.
        back_structure = bool(result.get("back_has_structure"))

        if not regions:
            print(f"  {garment['name']}: read as plain, no close-ups needed")
            done += 1
            if not args.dry_run:
                with get_db() as conn:
                    conn.execute(
                        "UPDATE garment_analysis SET back_has_structure=? WHERE garment_id=?",
                        (int(back_structure), garment["id"]))
            continue

        made = build_detail_refs(regions) if not args.dry_run else []
        crops += len(made)
        why = "; ".join(r.get("why", "") for r in regions)
        print(f"  {garment['name']}: {len(regions)} region(s) — {why}")

        if not args.dry_run:
            with get_db() as conn:
                conn.execute(
                    """UPDATE garment_analysis
                       SET detail_regions=?, back_has_structure=?, updated_at=datetime('now')
                       WHERE garment_id=?""",
                    (json.dumps(regions), int(back_structure), garment["id"]))
        done += 1

    print(f"\n{done} garments updated, {skipped} skipped, {failed} failed"
          f"{f', {crops} crops written' if crops else ''}"
          f"{' (dry run, nothing written)' if args.dry_run else ''}")


if __name__ == "__main__":
    main()
