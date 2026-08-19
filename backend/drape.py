#!/usr/bin/env python
"""
Drape command line — the same engine the UI drives, without the UI.

Everything here calls straight into app/core, so a scripted bulk run and a click
in the browser share one brain: the same analysis, the same craft profile, the
same QC, and the same learning loop. A lesson learned during an overnight batch is
waiting for you in the app the next morning, and vice versa.

    ./.venv/bin/python drape.py sessions
    ./.venv/bin/python drape.py import   --session "August drop" --root ~/shoots/aug --category Tops
    ./.venv/bin/python drape.py analyze  --session "August drop"
    ./.venv/bin/python drape.py assign   --session "August drop" --model Leila
    ./.venv/bin/python drape.py generate --session "August drop" [--limit N] [--dry-run]
    ./.venv/bin/python drape.py repair   --session "August drop" [--limit N] [--dry-run]
    ./.venv/bin/python drape.py lessons
    ./.venv/bin/python drape.py status   --session "August drop"

Generation costs credits, so `generate` and `repair` never touch work that already
succeeded, and `--dry-run` prints exactly what would be spent before anything is.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import DEFAULT_IMAGE_SIZE, DEFAULT_PROMPT_PROFILE  # noqa: E402
from app.core import lessons as lessons_mod  # noqa: E402
from app.core import pipeline  # noqa: E402
from app.core.extractor import IMAGE_EXTS, SKIP_PATTERNS  # noqa: E402
from app.core.vision import VisionError  # noqa: E402
from app.db import get_db, init_db, row_to_dict, rows_to_dicts  # noqa: E402


def _session(name: str, create: bool = False):
    with get_db() as conn:
        row = row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE name=?", (name,)).fetchone())
        if row:
            return row
        if not create:
            sys.exit(f"no session named {name!r} — run `drape.py sessions` to list them")
        cur = conn.execute("INSERT INTO sessions (name) VALUES (?)", (name,))
        return row_to_dict(conn.execute(
            "SELECT * FROM sessions WHERE id=?", (cur.lastrowid,)).fetchone())


def cmd_sessions(_args):
    with get_db() as conn:
        rows = rows_to_dicts(conn.execute(
            """SELECT s.id, s.name,
                      (SELECT COUNT(*) FROM garments WHERE session_id=s.id) garments,
                      (SELECT COUNT(*) FROM generations g JOIN garments gr ON gr.id=g.garment_id
                        WHERE gr.session_id=s.id AND g.status='done') shots
               FROM sessions s ORDER BY s.id DESC""").fetchall())
    if not rows:
        print("no sessions yet")
    for r in rows:
        print(f"  [{r['id']:>3}] {r['name']:<32} {r['garments']:>3} garments  {r['shots']:>3} shots")


def cmd_import(args):
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")
    session = _session(args.session, create=True)

    created = 0
    with get_db() as conn:
        for sub in sorted(p for p in root.iterdir() if p.is_dir()):
            images = [p for p in sorted(sub.iterdir())
                      if p.suffix.lower() in IMAGE_EXTS and not SKIP_PATTERNS.search(p.name)]
            if not images:
                continue
            if conn.execute("SELECT 1 FROM garments WHERE session_id=? AND name=?",
                            (session["id"], sub.name)).fetchone():
                continue  # already imported; re-running import must be safe
            gid = conn.execute(
                "INSERT INTO garments (session_id, name, category) VALUES (?,?,?)",
                (session["id"], sub.name, args.category)).lastrowid
            for order, img in enumerate(images):
                conn.execute(
                    "INSERT INTO garment_images (garment_id, path, filename, sort_order) "
                    "VALUES (?,?,?,?)", (gid, str(img), img.name, order))
            created += 1
            print(f"  + {sub.name} ({len(images)} photos)")
    print(f"imported {created} garments into '{session['name']}'")


def cmd_analyze(args):
    session = _session(args.session)
    with get_db() as conn:
        garments = rows_to_dicts(conn.execute(
            """SELECT * FROM garments WHERE session_id=?
               AND (? OR status='uploaded') ORDER BY id""",
            (session["id"], 1 if args.all else 0)).fetchall())
    if not garments:
        print("nothing to analyse")
        return
    for g in garments:
        try:
            pipeline.analyze_garment(g["id"], propose_looks=True, n_looks=args.looks)
            print(f"  ok   {g['name']}")
        except (ValueError, VisionError) as e:
            print(f"  FAIL {g['name']}: {e}")


def cmd_assign(args):
    session = _session(args.session)
    with get_db() as conn:
        avatar = row_to_dict(conn.execute(
            "SELECT * FROM avatars WHERE name=? AND archived=0", (args.model,)).fetchone())
        if not avatar:
            sys.exit(f"no model named {args.model!r}")
        sql = "UPDATE garments SET avatar_id=? WHERE session_id=?"
        params = [avatar["id"], session["id"]]
        if args.category:
            sql += " AND category=?"
            params.append(args.category)
        if not args.overwrite:
            sql += " AND avatar_id IS NULL"
        n = conn.execute(sql, params).rowcount
    print(f"assigned {avatar['name']} to {n} garment(s)")


def _pending_looks(session_id: int):
    with get_db() as conn:
        return rows_to_dicts(conn.execute(
            """SELECT l.id, l.label, l.view, g.name garment, g.category
               FROM looks l JOIN garments g ON g.id = l.garment_id
               WHERE g.session_id=? AND g.avatar_id IS NOT NULL
                 AND l.id NOT IN (SELECT look_id FROM generations WHERE status='done')
               ORDER BY g.id, l.sort_order""", (session_id,)).fetchall())


def cmd_generate(args):
    session = _session(args.session)
    looks = _pending_looks(session["id"])
    if args.limit:
        looks = looks[:args.limit]

    if not looks:
        print("nothing to generate — every look already has a shot, or garments need a model")
        return

    print(f"{len(looks)} shot(s) to generate at {args.size} on profile {args.profile}:")
    for l in looks:
        print(f"  {l['garment']:<28} {l['label']}{' (back)' if l['view'] == 'back' else ''}")
    if args.dry_run:
        print("\ndry run — nothing generated, nothing spent")
        return

    ok = failed = errored = 0
    for l in looks:
        try:
            r = pipeline.generate_for_look(look_id=l["id"], image_size=args.size,
                                           profile=args.profile)
        except (ValueError, VisionError) as e:
            print(f"  ERROR {l['garment']} {l['label']}: {e}")
            errored += 1
            continue
        if r.get("status") == "error":
            print(f"  ERROR {l['garment']} {l['label']}: {r.get('error')}")
            errored += 1
            continue
        passed = (r.get("qc") or {}).get("overall_pass")
        mark = "ok  " if passed else ("FAIL" if passed is False else "?   ")
        if passed:
            ok += 1
        elif passed is False:
            failed += 1
        print(f"  {mark} {l['garment']:<28} {l['label']}")
        if passed is False:
            for c in (r.get("qc") or {}).get("checks", []):
                if not c.get("pass"):
                    print(f"         - {c['criterion']}: {c['reason'][:90]}")
    print(f"\n{ok} passed, {failed} failed QC, {errored} errored")
    if failed:
        print("run `drape.py repair` to apply the suggested fixes")


def cmd_repair(args):
    """Regenerate QC failures using their suggested fix — the same one-click
    repair the UI offers, applied across a whole session."""
    session = _session(args.session)
    with get_db() as conn:
        rows = rows_to_dicts(conn.execute(
            """SELECT g.id, gar.name garment, l.label, q.repair
               FROM generations g
               JOIN garments gar ON gar.id = g.garment_id
               JOIN looks l ON l.id = g.look_id
               JOIN qc_results q ON q.id = (
                   SELECT id FROM qc_results WHERE generation_id=g.id ORDER BY id DESC LIMIT 1)
               WHERE gar.session_id=? AND q.overall_pass=0 AND q.confirmed=1
                 AND q.repair IS NOT NULL
                 AND g.attempt_no = (SELECT MAX(attempt_no) FROM generations
                                     WHERE look_id=g.look_id)
               ORDER BY g.id""", (session["id"],)).fetchall(), json_cols=("repair",))
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        print("no confirmed failures with a suggested repair")
        return

    print(f"{len(rows)} repair(s):")
    for r in rows:
        print(f"  {r['garment']:<28} {r['label']:<12} {(r['repair'] or {}).get('label')}")
    if args.dry_run:
        print("\ndry run — nothing generated, nothing spent")
        return

    # Route through the same function the API calls, so repair bookkeeping and the
    # learning loop behave identically whether driven from here or the browser.
    from app.routers.generations import RepairRequest, apply_repair

    for r in rows:
        try:
            res = apply_repair(RepairRequest(generation_id=r["id"]))
        except Exception as e:  # noqa: BLE001 - report and carry on with the batch
            print(f"  ERROR {r['garment']}: {e}")
            continue
        passed = (res.get("qc") or {}).get("overall_pass")
        print(f"  {'ok  ' if passed else 'FAIL'} {r['garment']:<28} {r['label']}")


def cmd_lessons(_args):
    rows = lessons_mod.all_lessons()
    if not rows:
        print("nothing learned yet — lessons come from a QC failure whose repair worked")
        return
    for r in rows:
        state = "on " if r["enabled"] else "off"
        print(f"  [{state}] {r['category']:<12} {r['criterion']:<20} "
              f"seen={r['times_seen']} proven={r['times_proven']}")
        if r["guidance"]:
            print(f"          {r['guidance'][:110]}")


def cmd_status(args):
    session = _session(args.session)
    with get_db() as conn:
        row = conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM garments WHERE session_id=?) garments,
                 (SELECT COUNT(*) FROM garments WHERE session_id=? AND status='uploaded') unanalysed,
                 (SELECT COUNT(*) FROM garments WHERE session_id=? AND avatar_id IS NULL) no_model""",
            (session["id"],) * 3).fetchone()
    pending = len(_pending_looks(session["id"]))
    with get_db() as conn:
        passed = conn.execute(
            """SELECT COUNT(*) n FROM qc_results q
               JOIN generations g ON g.id=q.generation_id
               JOIN garments gar ON gar.id=g.garment_id
               WHERE gar.session_id=? AND q.overall_pass=1""", (session["id"],)).fetchone()["n"]
    print(f"{session['name']}")
    print(f"  garments        {row['garments']}")
    print(f"  not analysed    {row['unanalysed']}")
    print(f"  without a model {row['no_model']}")
    print(f"  shots to make   {pending}")
    print(f"  QC passed       {passed}")


def main():
    ap = argparse.ArgumentParser(description="Drape — branding shots for clothing")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sessions").set_defaults(fn=cmd_sessions)

    p = sub.add_parser("import"); p.set_defaults(fn=cmd_import)
    p.add_argument("--session", required=True)
    p.add_argument("--root", required=True, help="folder with one subfolder per garment")
    p.add_argument("--category")

    p = sub.add_parser("analyze"); p.set_defaults(fn=cmd_analyze)
    p.add_argument("--session", required=True)
    p.add_argument("--looks", type=int, default=2)
    p.add_argument("--all", action="store_true", help="re-analyse everything, not just new")

    p = sub.add_parser("assign"); p.set_defaults(fn=cmd_assign)
    p.add_argument("--session", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--category")
    p.add_argument("--overwrite", action="store_true")

    p = sub.add_parser("generate"); p.set_defaults(fn=cmd_generate)
    p.add_argument("--session", required=True)
    p.add_argument("--limit", type=int)
    p.add_argument("--size", default=DEFAULT_IMAGE_SIZE)
    p.add_argument("--profile", default=DEFAULT_PROMPT_PROFILE)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("repair"); p.set_defaults(fn=cmd_repair)
    p.add_argument("--session", required=True)
    p.add_argument("--limit", type=int)
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("lessons").set_defaults(fn=cmd_lessons)

    p = sub.add_parser("status"); p.set_defaults(fn=cmd_status)
    p.add_argument("--session", required=True)

    args = ap.parse_args()
    init_db()
    args.fn(args)


if __name__ == "__main__":
    main()
