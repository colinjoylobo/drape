#!/usr/bin/env python
"""
Point the bundled database at this checkout.

Image paths are stored absolute, which is correct for a working install and wrong
for a repository that someone else clones: every path names the machine it was
created on. This rewrites any path under a `backend/storage/` directory so it
points at *this* checkout's storage instead.

Run once after cloning:

    ./.venv/bin/python rebase_paths.py

Idempotent, and safe to run again after moving the checkout.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import STORAGE_DIR  # noqa: E402
from app.db import get_db  # noqa: E402

MARKER = "/storage/"


def rebase(path: str | None) -> str | None:
    """Re-root a stored path onto this checkout, or return it unchanged."""
    if not path or MARKER not in path:
        return path
    tail = path.split(MARKER, 1)[1]
    return str(STORAGE_DIR / tail)


def main():
    changed = 0
    with get_db() as conn:
        for table, col, key in (("garment_images", "path", "id"),
                                ("avatars", "front_path", "id"),
                                ("avatars", "back_path", "id"),
                                ("generations", "output_path", "id")):
            for row in conn.execute(f"SELECT {key}, {col} FROM {table}").fetchall():
                new = rebase(row[col])
                if new and new != row[col]:
                    conn.execute(f"UPDATE {table} SET {col}=? WHERE {key}=?", (new, row[key]))
                    changed += 1

        # detail_regions carries source paths of its own inside JSON.
        for row in conn.execute(
                "SELECT garment_id, detail_regions FROM garment_analysis "
                "WHERE detail_regions IS NOT NULL AND detail_regions != '[]'").fetchall():
            regions = json.loads(row["detail_regions"])
            touched = False
            for region in regions:
                new = rebase(region.get("source_path"))
                if new and new != region.get("source_path"):
                    region["source_path"] = new
                    touched = True
            if touched:
                conn.execute(
                    "UPDATE garment_analysis SET detail_regions=? WHERE garment_id=?",
                    (json.dumps(regions), row["garment_id"]))
                changed += 1

        # ref_paths records which files a past generation used. Rewritten so the
        # history stays readable, though it is a record rather than a live path.
        for row in conn.execute(
                "SELECT id, ref_paths FROM generations WHERE ref_paths IS NOT NULL").fetchall():
            refs = json.loads(row["ref_paths"])
            new_refs = [rebase(p) for p in refs]
            if new_refs != refs:
                conn.execute("UPDATE generations SET ref_paths=? WHERE id=?",
                             (json.dumps(new_refs), row["id"]))
                changed += 1

    print(f"rebased {changed} path(s) onto {STORAGE_DIR}")
    if not changed:
        print("nothing to do — paths already point at this checkout")


if __name__ == "__main__":
    main()
