"""
Seed Drape with a completed shoot as worked examples.

Imports an earlier pipeline run — its models, garments, analyses, looks, finished
shots and QC verdicts — so a new install opens onto real work instead of an empty
screen, and the Look Library starts with looks that actually passed.

Files are referenced where they already sit rather than copied; the source shoot
runs to hundreds of megabytes and duplicating it buys nothing on a local tool.

    ./.venv/bin/python import_examples.py [--manifest PATH] [--reset]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import get_db, init_db  # noqa: E402

# No default: the source shoot lives outside this repo and naming a client folder
# in committed source would leak whose it is.
DEFAULT_MANIFEST = Path(os.getenv("DRAPE_EXAMPLE_MANIFEST", ""))

SESSION_NAME = "Sample shoot"
IMPORTED_NOTE = "Imported with the sample shoot."

SESSION_NOTES = ("A completed shoot, imported as a worked example — every garment analysed, "
                 "shot twice and judged. Delete it whenever you like.")

# The source run used folder names as categories; "Random" was a catch-all.
CATEGORY_MAP = {"Random": "Other"}

# Readable names for the imported models, keyed by the avatar filename stem.
MODEL_NAMES = {
    "avatar_dresses": "Aria", "avatar_dresses_plussize": "Noor",
    "avatar_lingerie": "Elena", "avatar_nightwear": "Mira",
    "avatar_random": "Sana", "avatar_sportswear": "Tara", "avatar_tops": "Leila",
}


# The earliest runs stored the look only inside the prompt, before look_1/look_2
# existed as fields. Recover it so those garments import with their real direction
# rather than being dropped.
LOOK_IN_PROMPT = re.compile(
    r"Pose(?:, setting,? and lighting)?:\s*(.+?)\s*(?:Tasteful editorial|Editorial branding)",
    re.IGNORECASE | re.DOTALL)


def look_from_prompt(prompt: str) -> str:
    match = LOOK_IN_PROMPT.search(prompt or "")
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip().rstrip(".")


def model_name_for(front_path: Path) -> str:
    stem = front_path.stem.replace("_front", "")
    return MODEL_NAMES.get(stem, stem.replace("avatar_", "").replace("_", " ").title())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    ap.add_argument("--reset", action="store_true",
                    help="remove a previously imported example session first")
    args = ap.parse_args()

    if not args.manifest or not args.manifest.exists():
        sys.exit("pass --manifest /path/to/full_pipeline_manifest.json "
                 "(or set DRAPE_EXAMPLE_MANIFEST)")

    init_db()
    manifest = json.loads(args.manifest.read_text())

    with get_db() as conn:
        if args.reset:
            conn.execute("DELETE FROM sessions WHERE name=?", (SESSION_NAME,))
            conn.execute("DELETE FROM look_templates WHERE source_generation_id IS NOT NULL")
            # Models are persistent and survive a session delete, so without this a
            # re-import stacks a second copy of every imported model on top.
            conn.execute("DELETE FROM avatars WHERE notes=?", (IMPORTED_NOTE,))
        if conn.execute("SELECT 1 FROM sessions WHERE name=?", (SESSION_NAME,)).fetchone():
            sys.exit(f"'{SESSION_NAME}' already imported — pass --reset to replace it.")

        session_id = conn.execute(
            "INSERT INTO sessions (name, notes) VALUES (?,?)",
            (SESSION_NAME, SESSION_NOTES)).lastrowid

    avatars: dict = {}
    counts = {"garments": 0, "looks": 0, "gens": 0, "passed": 0, "templates": 0, "skipped": 0}

    for key, record in manifest.items():
        if record.get("error") or not record.get("analysis"):
            counts["skipped"] += 1
            continue

        analysis = record["analysis"]
        category = CATEGORY_MAP.get(record.get("category"), record.get("category"))
        size_variant = record.get("size_variant")
        avatar_front = record.get("avatar")

        if not avatar_front or not Path(avatar_front).exists():
            counts["skipped"] += 1
            continue

        with get_db() as conn:
            # --- model (created once, reused across every garment that used it) ---
            if avatar_front not in avatars:
                front = Path(avatar_front)
                back = front.parent / front.name.replace("_front", "_back")
                avatars[avatar_front] = conn.execute(
                    """INSERT INTO avatars (name, category, size_variant, front_path, back_path, notes)
                       VALUES (?,?,?,?,?,?)""",
                    (model_name_for(front), category, size_variant, str(front),
                     str(back) if back.exists() else None,
                     IMPORTED_NOTE)).lastrowid
            avatar_id = avatars[avatar_front]

            # --- garment ---
            garment_id = conn.execute(
                """INSERT INTO garments (session_id, name, category, size_variant, avatar_id, status)
                   VALUES (?,?,?,?,?,'generated')""",
                (session_id, record["subcategory"], category, size_variant, avatar_id)).lastrowid
            counts["garments"] += 1

            # --- product photos, with the roles the original run resolved ---
            image_files = analysis.get("image_files", [])
            roles = {c.get("image"): c.get("role")
                     for c in analysis.get("image_classification", [])}
            for i, path in enumerate(image_files):
                conn.execute(
                    """INSERT INTO garment_images (garment_id, path, filename, role, sort_order)
                       VALUES (?,?,?,?,?)""",
                    (garment_id, path, Path(path).name, roles.get(f"IMAGE {i + 1}"), i))

            # --- analysis ---
            # The old format had a boolean needs_detail_crop and a source image but no
            # coordinates, so there are no regions to carry over. Re-analysing an
            # imported garment produces real ones.
            conn.execute(
                """INSERT INTO garment_analysis
                     (garment_id, garment_desc, pieces, coverage_risk, pairing_note,
                      back_has_structure, detail_regions, raw)
                   VALUES (?,?,?,?,?,0,'[]',?)""",
                (garment_id, analysis.get("garment_desc"),
                 json.dumps(analysis.get("pieces", [])),
                 int(bool(analysis.get("coverage_risk"))),
                 analysis.get("pairing_note", ""), json.dumps(analysis)))

            # --- looks, shots and verdicts ---
            for i, (pose_key, pose) in enumerate(sorted(record.get("poses", {}).items())):
                look_text = (analysis.get("look_1") if pose_key in ("lookA", "default")
                             else analysis.get("look_2")) or ""
                if not look_text:
                    look_text = look_from_prompt(pose.get("prompt", ""))
                if not look_text:
                    counts["skipped"] += 1
                    continue

                look_id = conn.execute(
                    """INSERT INTO looks (garment_id, label, text, source, sort_order)
                       VALUES (?,?,?,'ai',?)""",
                    (garment_id, f"Look {i + 1}", look_text, i)).lastrowid
                counts["looks"] += 1

                output = pose.get("output")
                if not output or not Path(output).exists():
                    continue

                gen_id = conn.execute(
                    """INSERT INTO generations
                         (look_id, garment_id, attempt_no, prompt, ref_paths, image_size,
                          avatar_id, output_path, status)
                       VALUES (?,?,1,?,?,'portrait_4_3',?,?,'done')""",
                    (look_id, garment_id, pose.get("prompt", ""),
                     json.dumps([avatar_front] + (record.get("refs") or [])),
                     avatar_id, output)).lastrowid
                counts["gens"] += 1

                qc = pose.get("qc") or {}
                if not qc.get("checks") and qc.get("overall_pass") is None:
                    continue

                passed = bool(qc.get("overall_pass"))
                conn.execute(
                    """INSERT INTO qc_results
                         (generation_id, overall_pass, checks, summary, confirmed)
                       VALUES (?,?,?,?,1)""",
                    (gen_id, int(passed), json.dumps(qc.get("checks", [])),
                     qc.get("summary", "")))
                if passed:
                    counts["passed"] += 1
                    # Seed the library from looks that actually worked — the same bar
                    # the app applies when you promote one by hand.
                    if category and not conn.execute(
                            "SELECT 1 FROM look_templates WHERE category=? AND text=?",
                            (category, look_text)).fetchone():
                        conn.execute(
                            """INSERT INTO look_templates (category, text, source_generation_id)
                               VALUES (?,?,?)""", (category, look_text, gen_id))
                        counts["templates"] += 1

    print(f"Imported '{SESSION_NAME}':")
    print(f"  models            {len(avatars)}")
    print(f"  garments          {counts['garments']}")
    print(f"  looks             {counts['looks']}")
    print(f"  shots             {counts['gens']} ({counts['passed']} passed QC)")
    print(f"  library templates {counts['templates']}")
    if counts["skipped"]:
        print(f"  skipped           {counts['skipped']} (no analysis or missing model)")


if __name__ == "__main__":
    main()
