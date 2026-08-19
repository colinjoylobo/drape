"""
Shoot craft — the photographic direction that separates a catalogue snap from a
campaign frame.

Versioned on purpose. `v1` is the original prompt behaviour and is preserved
untouched so existing work stays reproducible; `v2` adds the craft layer below.
Every generation records the profile it used, so a shot can always be explained
by the rules that produced it.

The vocabulary here is drawn from how fashion campaigns are actually lit and
posed — key-light angles, modifiers, rim separation, negative fill, lens choice,
catchlights, contrapposto, gaze direction. It deliberately encodes TECHNIQUE
rather than brand names: naming a label in a prompt asks the model to imitate
that house's campaign identity, which is both a trade-dress problem and, in
practice, vaguer direction than "large softbox at 45°, gridded strip light behind
at one stop under key".

Sources for the craft vocabulary are listed in docs/shoot_craft_sources.md.
"""
from typing import Dict

PROFILES = ("v1", "v2")
DEFAULT_PROFILE = "v2"

# ---------------------------------------------------------------------------
# Craft that applies to every shot in v2, regardless of what the garment is.
# ---------------------------------------------------------------------------
UNIVERSAL_CRAFT = """PHOTOGRAPHIC CRAFT — this must read as a professionally lit, art-directed campaign
frame, not a snapshot and not a flat e-commerce cut-out:

* THE EYES CARRY THE FRAME. Focus must fall on her eyes, and each eye should carry a small, natural
  specular catchlight — the ordinary reflection of the light source. Her gaze should be present and
  directed, not distant or unfocused.
  Her eyes must remain a NORMAL, NATURAL HUMAN EYE COLOUR, matching the model reference. They must
  never glow, self-illuminate, or take on an unnatural amber, orange, yellow or otherwise luminous
  iris — that is a rendering artefact, not a catchlight, and it ruins the photograph.
* She must look COMFORTABLE IN HER OWN BODY and effortlessly stylish: weight settled on one leg with
  the hips and shoulders on opposing angles (contrapposto), a soft S-curve through the body rather
  than a squared-up stance, a visible gap between the arms and the torso so the silhouette reads,
  a long relaxed neck, and chin brought slightly forward and down. Hands stay soft and articulated —
  never clenched, splayed, stiff, or hanging dead at her sides.
* Nothing about her may look posed, frozen, awkward, doll-like or mannequin-like. She should look
  like a real, confident, attractive woman caught in a considered moment.
* LIGHTING: shaped, directional and intentional. A large soft key source about 45° off-axis and
  slightly above her eyeline for clean, sculpted skin; a subtle rim or edge light behind her at
  roughly one stop under the key so she separates from the background and the garment's outline
  reads; and enough negative fill on the shadow side to keep the image dimensional rather than flat.
  The light should skim the fabric so its weave, sheen and drape are visible.
* CAMERA: shot on a fast prime — around 85mm for a flattering, compressed portrait, or 35-50mm when
  the location deserves to be part of the story. Shallow depth of field so she separates cleanly
  from a softly rendered background, with the garment itself edge-to-edge sharp.
* FINISH: true-to-life colour, skin with visible natural texture and pores, no plastic
  over-retouching, no blown highlights, no muddy shadows. Editorial polish.
* GARMENT COLOUR IS INVARIANT. The scene's light may be any temperature the look calls for — warm
  lamplight, cool overcast, golden hour — but the GARMENT must still read as its true colour, exactly
  as the reference photographs show it. Do not let the scene's warmth or coolness tint the fabric,
  and do not apply any colour grade, filter or stylised wash that shifts it. If the garment is a warm
  beige it must stay a warm beige under cool light; if it is a cool grey it must stay a cool grey
  under warm light. This is a product photograph: the customer receives this exact colour."""


# ---------------------------------------------------------------------------
# Per-category direction. Each entry is appended to the universal craft.
# ---------------------------------------------------------------------------
CATEGORY_CRAFT: Dict[str, str] = {
    "Dresses": """CATEGORY DIRECTION — dresses: photograph this as a fashion campaign, not a product
listing. Let the garment move: a walking step, a turn that catches the skirt, fabric caught in air.
Full-length or three-quarter framing so the hemline and full silhouette read. Golden-hour or soft
directional daylight flatters drape; a location with real architectural or natural depth beats a
plain wall. Her gaze can meet the lens with quiet confidence or drift just off-camera for an
editorial, unposed feeling.""",

    "Lingerie": """CATEGORY DIRECTION — lingerie: this is an elegant, tasteful, high-end intimates
campaign. The mood is confident, warm and self-possessed — never clinical, never crude, never a
glamour shot. Light her with a large, soft, directional source (a big window with sheer diffusion,
or a big softbox feathered across her) so the skin is luminous and even and the shadows are gentle;
a soft backlight or rim gives that halo of glow along her shoulder and hip. Skin should look healthy
and radiant with natural texture, never oily or plastic. Lace, mesh, embroidery and trim must be
crisply resolved — light skimming across the piece is what reveals them. Posing is relaxed and
graceful: soft hands, an elongated line through the body, a natural recline or easy stand. Setting
is a beautifully styled bedroom, suite or minimal studio with warm, considered light. She looks
comfortable and quietly powerful.""",

    "Nightwear": """CATEGORY DIRECTION — nightwear and loungewear: intimate, warm and unhurried, like
a real morning. Soft window light, warm lamplight, or soft early daylight — whichever suits the
piece, but keep the light neutral enough that the garment's own colour stays true. A
beautifully styled bedroom or living space with texture — rumpled linen, a mug, an open book, a
plant. She is genuinely relaxed: curled on a bed, stretching, sitting on the floor against a sofa,
mid-laugh or mid-thought. The fabric should look soft and touchable. Nothing stiff or staged.""",

    "Sportswear": """CATEGORY DIRECTION — activewear: energy and capability. Favour dynamic, athletic
moments — mid-stride, a lunge, a reach, tying her hair back, resting between efforts with real
breath in her. Light with a punchier, more directional key plus a strong rim so muscle tone,
seam-lines and technical fabric panels are defined; avoid flat frontal light, which kills the
garment's construction. Compression fabrics must look taut and technical, not shiny or cheap —
never a ring-light reflection. Setting: a real gym, a studio floor, a track, a city street at
first light. Confident and capable, never coy.""",

    "Tops": """CATEGORY DIRECTION — tops: a modern, wearable street-style campaign. Three-quarter or
waist-up framing so the neckline, shoulder line and print sit large in frame. Real locations with
character — a sunlit street, a café, a doorway, a textured wall — with a shallow depth of field so
the background falls away. Natural, candid energy: adjusting a sleeve, hands in pockets, mid-turn,
caught laughing. Bright, clean, flattering light with a lifted, contemporary grade.""",

    "Outerwear": """CATEGORY DIRECTION — outerwear: structure and presence. Show the coat's full
silhouette, lapel roll, shoulder line and length — a slight low angle gives it authority. Movement
helps: a walking stride with the coat opening, collar turned up, hands in pockets. Cooler,
directional daylight or overcast light suits wool and leather and keeps the texture honest. Urban
architecture, wide streets, considered negative space.""",

    "Other": """CATEGORY DIRECTION: photograph this as a considered fashion campaign frame. Choose the
framing that best shows this specific piece, put her in a real location with depth and texture, and
keep the energy natural and confident.""",
}


def craft_block(category: str | None, profile: str = DEFAULT_PROFILE) -> str:
    """The craft direction injected into a generation prompt. Empty on v1."""
    if profile != "v2":
        return ""
    return UNIVERSAL_CRAFT + "\n\n" + CATEGORY_CRAFT.get(category or "Other", CATEGORY_CRAFT["Other"])


def art_direction_brief(category: str | None, profile: str = DEFAULT_PROFILE) -> str:
    """Craft context handed to the art director so the LOOK ITSELF is written at
    campaign level, rather than craft being bolted on afterwards."""
    if profile != "v2":
        return ""
    return (
        "\nHOUSE STANDARD — every look you propose must be worthy of a designer campaign. Specify the "
        "quality and direction of light (soft key off-axis, backlit glow, hard directional sun, warm "
        "lamplight), the character of the location, and where her eyes go. She must read as "
        "comfortable, fashionable and attractive — relaxed, confident, alive — never stiff, blank or "
        "doll-like. Give her a real moment, not a pose held for the camera.\n"
        + CATEGORY_CRAFT.get(category or "Other", CATEGORY_CRAFT["Other"])
    )


# ---------------------------------------------------------------------------
# Model references. A stiff model reference propagates into every shot that uses
# her, so v2 raises the bar at the source.
# ---------------------------------------------------------------------------
AVATAR_CRAFT_V2 = (
    "Photorealistic full-body fashion model portfolio reference, shot for a high-end agency card. "
    "She is strikingly attractive and effortlessly stylish, and — most importantly — she looks "
    "completely at ease: relaxed shoulders, weight settled on one leg in a natural contrapposto with "
    "a soft S-curve through the body, a long relaxed neck, chin slightly forward and down. "
    "Her arms hang naturally at her sides or one hand rests lightly on her hip — her hands must NOT "
    "be clasped or folded together in front of her body, which reads as stiff and formal. "
    "A warm, present gaze straight to camera with a small natural catchlight in each eye, and a soft "
    "closed-lip smile with genuine warmth behind it. Her eyes must be a normal natural human eye "
    "colour and must never glow or self-illuminate. "
    "She reads as a real, confident woman, NEVER stiff, blank, doll-like or mannequin-like. "
    "FRAMING IS CRITICAL: front-facing, standing, the COMPLETE body in frame from the top of her head "
    "to the soles of her feet, with clear empty space above her head and below her feet. Her feet "
    "must be fully visible and must not be cropped — this image is used as a body reference, so a "
    "cropped frame makes it unusable. "
    "Background: a completely plain, empty, evenly-toned neutral grey backdrop filling the whole "
    "frame edge to edge — no seams, no corners, no floor line, no walls, no props, no furniture. "
    "The frame contains NOTHING except her and that plain backdrop. Absolutely no lighting "
    "equipment, softboxes, umbrellas, reflectors, stands, flags, cables, backdrop edges or studio "
    "hardware anywhere in the frame, including the corners and edges. "
    "Lit with a large soft key about 45° off-axis and slightly above her eyeline plus a gentle rim "
    "light behind for separation — sculpted, flattering and dimensional, never flat. "
    "Simple, well-fitted neutral clothing so her body shape reads clearly. "
    "Shot on an 85mm prime at a wide aperture. Natural skin with visible pores and fine texture, no "
    "plastic retouching, no blown highlights. Sharp focus on the eyes. "
    "No text, no watermark."
)
