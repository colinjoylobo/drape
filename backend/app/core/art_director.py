"""
Art direction — proposes the pose / setting / lighting for each shot.

Split out of the analyzer on purpose. Extraction wants temperature 0 (it is
reading facts off a photograph); art direction at temperature 0 collapses onto
the same few backdrops for every garment, which is what made a whole catalogue
of shots feel like one shoot. This runs hot, and is explicitly told what has
already been used so it stops repeating itself.

It is also where the "approachable, not rigid" correction lives. The original
rubric fixed identity drift by banning smiles and mandating flat even light —
which fixed drift by removing life. The constraints that actually protect
identity are about head GEOMETRY and light QUALITY, not about warmth, so warmth
is now requested rather than suppressed.
"""
from typing import Any, Dict, List

from .shoot_style import DEFAULT_PROFILE, art_direction_brief
from .vision import call_vision_retry

BRIEF = """You are the art director for a fashion BRANDING shoot. You will be given one garment's
factual description and must propose {n} complete, distinct looks for it — the shots a brand would
actually want to publish.

Each look is ONE sentence covering pose, location/setting, and lighting together.

WHAT MAKES A GOOD LOOK
* Chosen for THIS garment. The pose, backdrop and light should be picked because they suit this
  piece's own colour, formality, silhouette and character — never from a template.
* Pose is NOT tied to category. Sitting, standing, walking, leaning, mid-turn — all fair game for any
  category if it is what shows this specific garment off best. A flowing dress may want movement to
  catch the fabric; a fitted piece may want a still, confident stance; loungewear may want to be sat in.
* The looks must be genuinely different from each other — different pose AND different setting AND
  different light. Two angles of the same idea is a failure.
* EVERY SHOT IN A COLLECTION MUST BE ITS OWN PHOTOGRAPH. These images are published together, so
  repetition is immediately obvious and reads as templated rather than art-directed. Vary the
  setting, the time of day, the framing (full length, three-quarter, waist-up), the distance, and
  what she is doing. A location you have already used is off limits unless you are explicitly told
  to match an existing set.
* Real places over voids. A styled interior, a location with depth and texture, a considered studio
  set. "Plain seamless backdrop" is a last resort, not a default.

SHE MUST LOOK COMFORTABLE AND APPROACHABLE — THIS IS THE POINT
The single most common failure in this pipeline is a model who looks posed, stiff and switched-off.
Every look you write must describe a person at ease and present: relaxed shoulders and hands (doing
something natural — resting, adjusting a sleeve, in a pocket, holding a coffee — not hanging limp or
locked in a rigid stance), weight settled naturally on one hip rather than squared-up and symmetrical,
warm engaged eyes, and a soft closed-lip smile or genuinely relaxed expression. Write the energy
explicitly ("easy, unforced", "quietly amused", "mid-conversation warmth"), do not leave it implied.

HARD CONSTRAINTS — these protect a fixed avatar identity that must stay recognisable across every
shot, so never override them for creative effect:
* Camera angle: front-facing, or at most a gentle three-quarter turn. Never a full back-turn, a side
  profile, or an over-the-shoulder look away from camera — those change too much visible face geometry.
* Expression: warm and relaxed, but NOT a wide open-mouth laugh or an exaggerated expression, which
  visibly distorts the lower face. A soft closed-lip or barely-parted smile is exactly right.
* Light on her FACE: always soft, even and flattering. The environment may be as moody, dim, warm or
  dramatic as the garment deserves — a night street, a candlelit room, deep golden hour — but the light
  actually falling on her face stays soft and clean. Describe the scene's mood and her face light
  separately when they differ.
* POSE VS SILHOUETTE: if the garment has a high leg slit, or a flowing/sheer skirt whose coverage
  depends on fabric hanging straight down, do NOT propose seated, cross-legged or legs-crossed poses.
  Bending or crossing the legs pulls such a skirt open and reliably causes exposure that no amount of
  prompt wording prevents. Use a standing pose instead (relaxed and asymmetric is still fine). Seated
  poses are good for tops, sets with shorts or trousers, and bottoms without a slit.

PROPS AND STYLING
For each look also propose `props`: the styling and objects that make the frame feel like a real
photograph rather than a product cut-out — what she wears alongside the garment (shoes, a jacket
over the shoulders, jewellery, a belt), what she holds or interacts with (a coffee cup, sunglasses,
a tote, a book, a railing, a doorway), and any set dressing that belongs in the scene.

Rules for props:
* They support the product, never compete with it. Nothing large, loud, or brightly patterned in
  front of the garment, and nothing that covers the parts of it a buyer needs to see.
* They must suit the garment's own formality and palette — quiet, coordinated, believable.
* Keep it to two or three specific things. A list of ten reads as clutter and the generator drops
  most of them anyway.
* Never propose props that obscure her face, since her identity has to stay recognisable.
* Write them as a short phrase, not a sentence: "tan leather tote, fine gold hoops, espresso cup".

Return ONLY valid JSON, no markdown fences:
{{"looks": [{{"text": "...", "props": "...", "scene_tag": "two or three words, e.g. 'sunlit apartment'"}}]}}"""


def propose_looks(garment_desc: str, category: str | None, pieces: List[str],
                  n: int = 2, library: List[Dict[str, Any]] | None = None,
                  used_scene_tags: List[str] | None = None,
                  used_look_texts: List[str] | None = None,
                  match_existing: bool = False,
                  user_direction: str | None = None,
                  profile: str = DEFAULT_PROFILE) -> List[Dict[str, str]]:
    """Propose `n` looks.

    library — proven looks for this category from previous sessions, offered as
      grounding for what has worked. Treated as inspiration, not as a template to
      copy, so the library raises the floor without flattening variety.
    used_scene_tags / used_look_texts — what this shoot has already used. The
      strongest lever against a catalogue where every frame is the same photograph
      with a different garment in it.
    match_existing — invert that: deliberately stay in the same world as the
      existing looks, for a coherent set shot in one place.
    """
    prompt = BRIEF.format(n=n) + art_direction_brief(category, profile)
    parts: List[Dict[str, Any]] = [{"text": prompt}]

    context = [f"\nGARMENT: {garment_desc}"]
    if category:
        context.append(f"CATEGORY: {category}")
    if pieces:
        context.append(f"PIECES: {', '.join(pieces)}")

    if library:
        lines = "\n".join(f"  - {t['text']}" for t in library[:6])
        context.append(
            "\nLooks that have previously worked well for this category. Use them to judge the level "
            "and quality expected — do NOT copy them, and do not reuse their settings:\n" + lines)

    if match_existing and (used_scene_tags or used_look_texts):
        # The deliberate opposite of the default: a coherent set, one place.
        context.append(
            "\nMATCH THE EXISTING SHOOT. The brand wants this garment photographed as part of the "
            "same coherent set as the shots below, not as a new location. Stay in the same world — "
            "same kind of setting, same light, same mood — and vary only the pose and framing enough "
            "that it is a different photograph rather than a duplicate.")
        if used_scene_tags:
            context.append("Settings in use: " + ", ".join(sorted(set(used_scene_tags))))
        if used_look_texts:
            context.append("Existing shots:\n" + "\n".join(f"  - {t}" for t in used_look_texts[:6]))
    else:
        if used_scene_tags:
            context.append(
                "\nSettings already used elsewhere in this shoot. Your proposals must NOT land in any "
                "of these: " + ", ".join(sorted(set(used_scene_tags))))
        if used_look_texts:
            context.append(
                "\nShots already in this collection. EVERY look you propose must be clearly "
                "distinguishable from all of them — a different setting, a different pose and a "
                "different quality of light. Someone scrolling the finished collection must never "
                "feel they are seeing the same photograph twice with a different garment in it. "
                "Reusing a location with a slightly altered pose counts as a duplicate:\n"
                + "\n".join(f"  - {t}" for t in used_look_texts[:10]))

    if user_direction:
        context.append(
            f"\nDIRECTION FROM THE BRAND — this overrides your own preferences, follow it closely: "
            f"{user_direction}")

    parts.append({"text": "\n".join(context)})

    # Hot: variety is the entire point of this call.
    result = call_vision_retry(parts, temperature=0.95)

    looks = []
    for item in (result.get("looks") or [])[:n]:
        text = (item.get("text") or "").strip()
        if text:
            looks.append({"text": text,
                          "props": (item.get("props") or "").strip(),
                          "scene_tag": (item.get("scene_tag") or "").strip()})
    return looks


BACK_BRIEF = """Rewrite this fashion shot as a BACK VIEW of the same look, so the pair reads as
two frames from one sitting rather than two unrelated shots.

FORMAT — this is the single most important rule. Write ONE sentence describing only POSE, SETTING
and LIGHTING, exactly like the front shot you are given. Do NOT describe the garment, its colour or
its print — that is supplied separately and repeating it causes conflicts. Do NOT write "a fashion
shot of a woman in a ..." or otherwise describe the photograph from the outside. Write the direction
itself, e.g. "walking away along a sun-dappled garden path, glancing back over one shoulder with an
easy smile, soft natural daylight".

Keep the same location, the same lighting and the same mood as the front shot. Change only what has
to change: she is now seen from BEHIND, so the back of the garment is the subject of the frame.

* Give her something natural to be doing that reads well from behind — walking away, looking out of
  a window, glancing back over one shoulder, reaching for something, hands in pockets.
* She must still look relaxed and at ease, not stiff or posed.
* Her back must be unobstructed: do not put her hair, a bag, a jacket or an arm across the garment's
  back, since the back of the garment is the whole point of this shot.
* A soft over-the-shoulder glance toward camera is good — it keeps her human — but her back stays
  to the camera.

PROPS — same rules as any shot: two or three specific styling objects she wears, holds or stands
near, quiet and coordinated, never covering the garment. A short phrase, not a sentence:
"tan leather sandals, small woven basket". This is NOT a place to list the garment, the location or
the lighting. If the front shot's props still work, keep them. If nothing suits, return "".

Return ONLY valid JSON, no markdown fences:
{"text": "...", "props": "...", "scene_tag": "two or three words"}"""


def back_view_variant(look_text: str, garment_desc: str,
                      props: str | None = None) -> Dict[str, str]:
    """Rewrite a front look as its back-view counterpart, holding the scene constant
    so the two shots belong together."""
    parts = [{"text": BACK_BRIEF},
             {"text": f"\nGARMENT: {garment_desc}\n\nTHE FRONT SHOT TO MATCH: {look_text}"
                      + (f"\nITS PROPS: {props}" if props else "")}]
    result = call_vision_retry(parts, temperature=0.8)
    return {"text": (result.get("text") or "").strip(),
            "props": (result.get("props") or props or "").strip(),
            "scene_tag": (result.get("scene_tag") or "").strip()}
