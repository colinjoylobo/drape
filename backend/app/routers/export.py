"""Export a session as a self-contained catalogue.

Everything is inlined as base64. A previous version of this catalogue linked to
local files, which worked perfectly on the machine that made it and showed nothing
but broken images the moment it was shared — which is the only situation the file
exists for.
"""
import base64
import io
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from PIL import Image, ImageOps

from ..db import get_db, row_to_dict, rows_to_dicts

router = APIRouter(prefix="/export", tags=["export"])


def data_uri(path: str, max_dim: int = 900, quality: int = 78) -> Optional[str]:
    try:
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    except (OSError, ValueError):
        return None
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


CSS = """
:root{--bg:#faf9f7;--card:#fff;--ink:#1a1a1a;--muted:#6b6b6b;--line:#e6e3de;--ok:#2d7a4f;--bad:#b3402f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
     font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:48px 24px 80px}
h1{font-size:34px;letter-spacing:-.02em;margin:0 0 4px}
.sub{color:var(--muted);margin:0 0 40px}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.1em;color:var(--muted);
   margin:48px 0 16px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.garment{background:var(--card);border:1px solid var(--line);border-radius:12px;
         padding:24px;margin-bottom:24px}
.gname{font-weight:600;font-size:17px;margin-bottom:2px}
.gmeta{color:var(--muted);font-size:13px;margin-bottom:16px}
.row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.row img{height:130px;border-radius:8px;border:1px solid var(--line);cursor:pointer;
         object-fit:cover;background:#f0efec}
.outs{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:16px}
.out img{width:100%;border-radius:10px;border:1px solid var(--line);cursor:pointer;display:block}
.look{color:var(--muted);font-size:13px;margin:8px 0 6px}
.badge{display:inline-block;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;
       text-transform:uppercase;letter-spacing:.05em}
.pass{background:#e6f2ea;color:var(--ok)}.fail{background:#fbeae7;color:var(--bad)}
.lbl{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px}
#lb{display:none;position:fixed;inset:0;background:rgba(20,20,20,.92);z-index:99;
    align-items:center;justify-content:center;flex-direction:column;gap:16px;padding:32px}
#lb.on{display:flex}
#lb img{max-width:92vw;max-height:80vh;border-radius:8px}
#lb a{background:#fff;color:#1a1a1a;padding:10px 20px;border-radius:8px;
      text-decoration:none;font-weight:600;font-size:14px}
"""

JS = """
function openLb(el){var b=document.getElementById('lb');
  document.getElementById('lbi').src=el.src;
  var d=document.getElementById('lbd');d.href=el.src;d.download=(el.dataset.name||'image')+'.jpg';
  b.classList.add('on');}
function closeLb(e){if(e.target.id==='lb'||e.target.id==='lbc')document.getElementById('lb').classList.remove('on');}
document.addEventListener('keydown',function(e){
  if(e.key==='Escape')document.getElementById('lb').classList.remove('on');});
"""


@router.get("/session/{session_id}", response_class=HTMLResponse)
def export_session(session_id: int, max_dim: int = 900):
    with get_db() as conn:
        session = row_to_dict(conn.execute("SELECT * FROM sessions WHERE id=?",
                                           (session_id,)).fetchone())
        if not session:
            raise HTTPException(404, "session not found")
        garments = rows_to_dicts(conn.execute(
            "SELECT * FROM garments WHERE session_id=? ORDER BY category, name",
            (session_id,)).fetchall())

        for g in garments:
            g["images"] = rows_to_dicts(conn.execute(
                "SELECT * FROM garment_images WHERE garment_id=? AND role != 'irrelevant' "
                "ORDER BY sort_order", (g["id"],)).fetchall())
            g["avatar"] = row_to_dict(conn.execute(
                "SELECT * FROM avatars WHERE id=?", (g["avatar_id"],)).fetchone()) \
                if g.get("avatar_id") else None
            looks = rows_to_dicts(conn.execute(
                "SELECT * FROM looks WHERE garment_id=? ORDER BY sort_order", (g["id"],)).fetchall())
            for look in looks:
                # Latest completed attempt only — the catalogue is the finished
                # work, not the working history.
                look["gen"] = row_to_dict(conn.execute(
                    "SELECT * FROM generations WHERE look_id=? AND status='done' "
                    "ORDER BY attempt_no DESC LIMIT 1", (look["id"],)).fetchone())
                if look["gen"]:
                    look["qc"] = row_to_dict(conn.execute(
                        "SELECT * FROM qc_results WHERE generation_id=? ORDER BY id DESC LIMIT 1",
                        (look["gen"]["id"],)).fetchone())
            g["looks"] = looks

    parts = [f"<title>{session['name']} — Drape</title><style>{CSS}</style>",
             "<div class='wrap'>",
             f"<h1>{session['name']}</h1>",
             f"<p class='sub'>{len(garments)} garments · generated with Drape</p>"]

    by_category: dict = {}
    for g in garments:
        by_category.setdefault(g.get("category") or "Uncategorised", []).append(g)

    for category, items in by_category.items():
        parts.append(f"<h2>{category}</h2>")
        for g in items:
            parts.append("<div class='garment'>")
            parts.append(f"<div class='gname'>{g['name']}</div>")
            meta = " · ".join(x for x in [g.get("size_variant"),
                                          (g["avatar"] or {}).get("name")] if x)
            if meta:
                parts.append(f"<div class='gmeta'>{meta}</div>")

            parts.append("<div class='lbl'>Product photos</div><div class='row'>")
            for img in g["images"][:6]:
                uri = data_uri(img["path"], 400, 72)
                if uri:
                    parts.append(f"<img src='{uri}' data-name='{g['name']}' onclick='openLb(this)'>")
            parts.append("</div>")

            gens = [look for look in g["looks"] if look.get("gen")]
            if gens:
                parts.append("<div class='lbl'>Shots</div><div class='outs'>")
                for look in gens:
                    uri = data_uri(look["gen"]["output_path"], max_dim, 80)
                    if not uri:
                        continue
                    qc = look.get("qc") or {}
                    passed = qc.get("overall_pass")
                    badge = ("<span class='badge pass'>QC pass</span>" if passed
                             else "<span class='badge fail'>QC fail</span>" if passed == 0
                             else "")
                    parts.append(
                        f"<div class='out'><img src='{uri}' data-name='{g['name']}' "
                        f"onclick='openLb(this)'>"
                        f"<div class='look'>{look['text']}</div>{badge}</div>")
                parts.append("</div>")
            parts.append("</div>")

    parts.append("</div>")
    parts.append("<div id='lb' onclick='closeLb(event)'><img id='lbi'>"
                 "<a id='lbd' download>Download</a>"
                 "<a id='lbc' href='javascript:void(0)'>Close</a></div>")
    parts.append(f"<script>{JS}</script>")
    return HTMLResponse("".join(parts))
