"""
The learning loop: QC failures become standing guardrails.

Until this existed, a QC failure was repaired for one shot and the lesson was
discarded — so the same defect recurred on the next garment, forever, and the only
thing that ever generalised a failure into a rule was a human editing the rubric.

How a lesson is earned, and why the bar is set here:

  * OBSERVED — a confirmed failure with no successful repair yet. Counted so a
    recurring problem becomes visible, but never fed back into generation: acting
    on an unverified guess is how a pipeline teaches itself a superstition.
  * PROVEN — a shot failed a criterion, a repair was applied, and the next attempt
    PASSED that same criterion. That pair is evidence the fix works, not merely
    that someone believed it would. Only proven lessons are injected.

Lessons start scoped to one category, because many pitfalls genuinely are
category-specific — sheer skirts open up when seated, activewear goes flat under
frontal light. But some defects belong to the GENERATOR rather than the garment
type, and colour drift is the clearest case: it turned up independently in
dresses, nightwear and tops, and each category had to rediscover it from its own
failures. Once the same criterion has been proven in PROMOTE_AT distinct
categories, it is promoted to global scope and applies everywhere.

Nothing here mutates a prompt template. Lessons are appended as an explicit,
readable block the user can inspect, edit or switch off, so output stays
explicable. A silently self-tuning prompt would make results unreproducible and
would happily learn from a single fluke.
"""
from typing import Any, Dict, List, Optional

from ..db import get_db, rows_to_dicts

# Pseudo-category holding lessons that apply to every garment.
GLOBAL = "*"

# A lesson must have worked this many times before it is trusted enough to be
# injected. One success can be luck.
MIN_PROVEN = 1

# Proven in this many DISTINCT categories and the defect is the generator's, not
# the garment type's. Two is deliberately low: the cost of applying a sound
# correction too widely is a slightly longer prompt, while the cost of not
# generalising is every category relearning the same failure at credit prices.
PROMOTE_AT = 2

# Cap what reaches the prompt. A wall of accumulated caveats would drown the
# garment description, which is the one thing that must never lose priority.
MAX_INJECTED = 5


def record_failure(category: Optional[str], criterion: str, reason: str) -> None:
    """Note a confirmed QC failure. Observed only — not yet fed back."""
    if not category or not criterion:
        return
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM qc_lessons WHERE category=? AND criterion=?",
            (category, criterion)).fetchone()
        if row:
            conn.execute(
                "UPDATE qc_lessons SET times_seen = times_seen + 1, "
                "last_reason=?, updated_at=datetime('now') WHERE id=?",
                (reason[:500], row["id"]))
        else:
            conn.execute(
                """INSERT INTO qc_lessons (category, criterion, last_reason, times_seen)
                   VALUES (?,?,?,1)""", (category, criterion, reason[:500]))


def record_fix_worked(category: Optional[str], criterion: str,
                      repair_label: Optional[str], guidance: Optional[str]) -> None:
    """A repair was applied and the criterion passed on the next attempt.

    This is the only path that promotes a lesson to PROVEN, because it is the only
    one carrying evidence that the fix changed the outcome.
    """
    if not category or not criterion or not guidance:
        return
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM qc_lessons WHERE category=? AND criterion=?",
            (category, criterion)).fetchone()
        if row:
            conn.execute(
                """UPDATE qc_lessons
                   SET times_proven = times_proven + 1, guidance=?, repair_label=?,
                       updated_at=datetime('now')
                   WHERE id=?""", (guidance, repair_label, row["id"]))
        else:
            conn.execute(
                """INSERT INTO qc_lessons
                     (category, criterion, guidance, repair_label, times_seen, times_proven)
                   VALUES (?,?,?,?,1,1)""", (category, criterion, guidance, repair_label))

    _maybe_promote(criterion)


def _maybe_promote(criterion: str) -> None:
    """Promote a criterion to global once it has been proven in enough distinct
    categories to show the defect is not category-specific."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT category, guidance, repair_label, times_proven
               FROM qc_lessons
               WHERE criterion=? AND category!=? AND times_proven>0 AND guidance IS NOT NULL
               ORDER BY times_proven DESC""", (criterion, GLOBAL)).fetchall()
        if len({r["category"] for r in rows}) < PROMOTE_AT:
            return

        best = rows[0]                     # the guidance with the most evidence
        total = sum(r["times_proven"] for r in rows)
        existing = conn.execute(
            "SELECT id FROM qc_lessons WHERE category=? AND criterion=?",
            (GLOBAL, criterion)).fetchone()
        if existing:
            conn.execute(
                """UPDATE qc_lessons SET guidance=?, repair_label=?, times_proven=?,
                       scope='global', updated_at=datetime('now') WHERE id=?""",
                (best["guidance"], best["repair_label"], total, existing["id"]))
        else:
            conn.execute(
                """INSERT INTO qc_lessons
                     (category, criterion, guidance, repair_label, times_seen,
                      times_proven, scope)
                   VALUES (?,?,?,?,?,?,'global')""",
                (GLOBAL, criterion, best["guidance"], best["repair_label"], total, total))


def active_lessons(category: Optional[str]) -> List[Dict[str, Any]]:
    """Proven, enabled lessons that apply to this category — its own plus anything
    promoted to global. A category-specific lesson wins over a global one for the
    same criterion, since the more specific guidance is the better guidance."""
    with get_db() as conn:
        rows = rows_to_dicts(conn.execute(
            """SELECT * FROM qc_lessons
               WHERE category IN (?, ?) AND enabled=1 AND times_proven >= ?
                 AND guidance IS NOT NULL AND guidance != ''
               ORDER BY times_proven DESC, times_seen DESC""",
            (category or "", GLOBAL, MIN_PROVEN)).fetchall())

    seen: set = set()
    specific = [r for r in rows if r["category"] != GLOBAL]
    globals_ = [r for r in rows if r["category"] == GLOBAL]
    out = []
    for r in specific + globals_:
        if r["criterion"] in seen:
            continue
        seen.add(r["criterion"])
        out.append(r)
    return out[:MAX_INJECTED]


def lessons_block(category: Optional[str]) -> str:
    """The prompt block. Empty when nothing has been proven that applies here."""
    lessons = active_lessons(category)
    if not lessons:
        return ""
    lines = []
    for lesson in lessons:
        n = lesson["times_proven"]
        where = ("across several garment types" if lesson["category"] == GLOBAL
                 else f"on {lesson['category']}")
        lines.append(f"* [{lesson['criterion']}] {lesson['guidance'].strip()} "
                     f"(this has gone wrong before {where} and this fix corrected it "
                     f"{n} time{'s' if n != 1 else ''})")
    return ("LEARNED FROM PREVIOUS SHOOTS — these are mistakes this pipeline has actually made, "
            "together with the correction that fixed them. Apply them:\n" + "\n".join(lines))


def all_lessons() -> List[Dict[str, Any]]:
    with get_db() as conn:
        return rows_to_dicts(conn.execute(
            "SELECT * FROM qc_lessons ORDER BY scope DESC, category, times_proven DESC"
        ).fetchall())
