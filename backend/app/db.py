"""
SQLite storage for Drape.

Two-tier data model:
  PERSISTENT (survives every session) — avatars, look_templates
  SESSION-SCOPED (one batch of clothes)  — garments and everything hanging off them

Generations are append-only: a regeneration writes a NEW row pointing at its
parent rather than overwriting, so the full retry history of a look is inspectable.
"""
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from .config import DB_PATH

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ---------- persistent ----------
CREATE TABLE IF NOT EXISTS avatars (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    category      TEXT,
    size_variant  TEXT,
    front_path    TEXT NOT NULL,
    back_path     TEXT,
    prompt        TEXT,
    styling       TEXT,
    notes         TEXT,
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS look_templates (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    category             TEXT NOT NULL,
    text                 TEXT NOT NULL,
    -- free-text tags so the art director can be told "give me something that is
    -- NOT another minimal studio wall"
    scene_tag            TEXT,
    source_generation_id INTEGER,
    times_used           INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- session-scoped ----------
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    notes       TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS garments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    category      TEXT,
    size_variant  TEXT,
    avatar_id     INTEGER REFERENCES avatars(id),
    status        TEXT NOT NULL DEFAULT 'uploaded',
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS garment_images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    garment_id   INTEGER NOT NULL REFERENCES garments(id) ON DELETE CASCADE,
    path         TEXT NOT NULL,
    filename     TEXT NOT NULL,
    role         TEXT,
    role_locked  INTEGER NOT NULL DEFAULT 0,   -- 1 once a user reclassifies it
    -- A product photo shot ON a person leaks that person's face/hair/styling into
    -- the output, overriding the chosen model. garment_box lets us send only the
    -- garment. See core/garment_crop.py.
    contains_person INTEGER NOT NULL DEFAULT 0,
    garment_box  TEXT,                          -- json [ymin,xmin,ymax,xmax] 0-1000
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS garment_analysis (
    garment_id         INTEGER PRIMARY KEY REFERENCES garments(id) ON DELETE CASCADE,
    garment_desc       TEXT,
    pieces             TEXT,     -- json array
    coverage_risk      INTEGER NOT NULL DEFAULT 0,
    pairing_note       TEXT,
    back_has_structure INTEGER NOT NULL DEFAULT 0,
    detail_regions     TEXT,     -- json array of {image_id, box_2d, why}
    edited_by_user     INTEGER NOT NULL DEFAULT 0,
    raw                TEXT,
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS looks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    garment_id   INTEGER NOT NULL REFERENCES garments(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    text         TEXT NOT NULL,
    source       TEXT NOT NULL DEFAULT 'ai',   -- ai | library | user
    props        TEXT,                         -- styling/props for this shot
    scene_tag    TEXT,                         -- short setting label, for variety checks
    view         TEXT NOT NULL DEFAULT 'front',-- front | back
    template_id  INTEGER REFERENCES look_templates(id),
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    look_id              INTEGER NOT NULL REFERENCES looks(id) ON DELETE CASCADE,
    garment_id           INTEGER NOT NULL REFERENCES garments(id) ON DELETE CASCADE,
    parent_generation_id INTEGER REFERENCES generations(id),
    attempt_no           INTEGER NOT NULL DEFAULT 1,
    prompt               TEXT NOT NULL,
    ref_paths            TEXT,   -- json array, in the order sent
    image_size           TEXT,
    avatar_id            INTEGER REFERENCES avatars(id),
    output_path          TEXT,
    status               TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|error
    error                TEXT,
    repair_applied       TEXT,   -- what repair suggestion produced this attempt
    prompt_profile       TEXT NOT NULL DEFAULT 'v1',  -- shoot-craft profile used
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS qc_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id  INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
    overall_pass   INTEGER,
    checks         TEXT,   -- json array
    summary        TEXT,
    confirmed      INTEGER NOT NULL DEFAULT 0,  -- 1 = failure survived the re-check
    repair         TEXT,   -- json suggested fix
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The learning loop. One row per (category, criterion); see core/lessons.py for
-- why only repair-verified lessons are ever fed back into generation.
CREATE TABLE IF NOT EXISTS qc_lessons (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category      TEXT NOT NULL,
    criterion     TEXT NOT NULL,
    guidance      TEXT,          -- the correction that demonstrably worked
    repair_label  TEXT,
    last_reason   TEXT,
    times_seen    INTEGER NOT NULL DEFAULT 0,
    times_proven  INTEGER NOT NULL DEFAULT 0,
    scope         TEXT NOT NULL DEFAULT 'category',  -- category | global
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(category, criterion)
);

CREATE INDEX IF NOT EXISTS idx_garments_session ON garments(session_id);
CREATE INDEX IF NOT EXISTS idx_images_garment   ON garment_images(garment_id);
CREATE INDEX IF NOT EXISTS idx_looks_garment    ON looks(garment_id);
CREATE INDEX IF NOT EXISTS idx_gens_look        ON generations(look_id);
CREATE INDEX IF NOT EXISTS idx_qc_gen           ON qc_results(generation_id);
CREATE INDEX IF NOT EXISTS idx_templates_cat    ON look_templates(category);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columns added after the first release. Applied idempotently on startup so an
# existing database picks them up without being rebuilt (which would throw away
# real work). Keep entries here forever — they are cheap and removing one breaks
# anyone still on an older database.
MIGRATIONS = [
    # Styling and props: worn/held/scene extras, kept separate from the look's
    # pose-and-lighting text so they can be edited and reused independently.
    ("looks", "props", "TEXT"),
    ("avatars", "styling", "TEXT"),
    ("garment_images", "contains_person", "INTEGER NOT NULL DEFAULT 0"),
    ("garment_images", "garment_box", "TEXT"),
    # Back-view shots: a look declares which side of the garment it photographs.
    ("looks", "view", "TEXT NOT NULL DEFAULT 'front'"),
    # Which shoot-craft profile produced a shot. Existing rows predate v2, so they
    # are correctly labelled v1 by the default.
    ("generations", "prompt_profile", "TEXT NOT NULL DEFAULT 'v1'"),
    # The art director returns a scene tag for every look, but it used to be
    # discarded — which left the "don't reuse a setting" guard reading only looks
    # taken from the library, i.e. almost never firing.
    ("looks", "scene_tag", "TEXT"),
    # Set once a generation's verdict has been fed to the learning loop, so
    # re-running QC on the same shot cannot credit the same lesson twice.
    ("generations", "lesson_recorded", "INTEGER NOT NULL DEFAULT 0"),
    # 'category' (default) or 'global'. Some defects belong to the generator, not
    # the garment type — colour drift is the clearest example — and those should
    # not have to be relearned category by category.
    ("qc_lessons", "scope", "TEXT NOT NULL DEFAULT 'category'"),
]


def _apply_migrations(conn: sqlite3.Connection):
    for table, column, decl in MIGRATIONS:
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


# ---------- small helpers ----------
def row_to_dict(row: Optional[sqlite3.Row], json_cols: tuple = ()) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    d = dict(row)
    for c in json_cols:
        if d.get(c):
            try:
                d[c] = json.loads(d[c])
            except (json.JSONDecodeError, TypeError):
                pass
        elif c in d:
            d[c] = None
    return d


def rows_to_dicts(rows: List[sqlite3.Row], json_cols: tuple = ()) -> List[Dict[str, Any]]:
    return [row_to_dict(r, json_cols) for r in rows]
