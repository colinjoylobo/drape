"""
Avatar creation — the persistent models Drape dresses.

The avatar's own energy propagates into every try-on that references it, so
warmth has to be built into the source image, not bolted on later in the try-on
prompt. The first generation of avatars was described in terms of poise and
neutrality and produced exactly that: technically correct, emotionally switched
off. These prompts ask for a person instead.

Front and back are chained — the back is generated FROM the front so the two are
the same individual rather than two people who match a description.
"""
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import STORAGE_DIR
from .generator import get_client
from .shoot_style import AVATAR_CRAFT_V2, DEFAULT_PROFILE

AVATAR_DIR = STORAGE_DIR / "avatars"

BASE_QUALITIES = (
    "Photorealistic full-body fashion model reference photograph. "
    "She looks comfortable, warm and approachable — relaxed shoulders, weight settled naturally on "
    "one hip rather than squared up, hands resting easily, warm engaged eyes looking to camera, and a "
    "soft closed-lip smile. She reads as a real person at ease in front of a camera, NOT a stiff, "
    "posed, blank-faced mannequin. "
    "Front-facing, standing, full body from head to feet in frame. "
    "Plain light neutral studio backdrop, soft even flattering light on her face with no harsh "
    "shadows. Simple plain fitted neutral clothing so the body shape is clearly readable. "
    "Sharp focus, natural skin texture with visible pores and fine detail, no heavy retouching, "
    "no text, no watermark."
)

BACK_INSTRUCTION = (
    "Using image 1 as the EXACT same person — identical face, hair, body type, proportions and skin "
    "tone — show her from behind, standing in the same plain neutral studio setting under the same "
    "soft even lighting, wearing the same simple fitted neutral clothing. Same relaxed, natural "
    "posture. Full body from head to feet in frame. This must be unmistakably the same individual "
    "as image 1, photographed in the same session. Photorealistic, sharp focus, natural skin texture, "
    "no text, no watermark."
)


def build_avatar_prompt(description: str, category: Optional[str] = None,
                        profile: str = DEFAULT_PROFILE) -> str:
    """`description` is the user's brief for who she is — ethnicity, age, build,
    hair, energy. Everything about photographic treatment comes from BASE_QUALITIES
    so avatars stay consistent as references even as the person varies."""
    base = AVATAR_CRAFT_V2 if profile == "v2" else BASE_QUALITIES
    parts = [base, f"\n\nTHE MODEL: {description.strip()}"]
    if category:
        # Only her casting suits the category. Saying she "will be photographed
        # wearing dresses" put her IN a loose shift dress, which hides the body
        # shape this image exists to record.
        parts.append(
            f"\n\nCASTING NOTE ONLY: she will later be photographed modelling {category.lower()}, "
            f"so her look and build should suit that category. This does NOT change what she wears "
            f"in THIS reference image — she must still be in simple, well-fitted neutral clothing "
            f"(a plain fitted top and plain fitted trousers) so her body shape reads clearly. Do not "
            f"dress her in {category.lower()} here.")
    return "".join(parts)


def generate_avatar(description: str, name: str, category: Optional[str] = None,
                    with_back: bool = True, profile: str = DEFAULT_PROFILE) -> Dict[str, Any]:
    """Generate a front (and optionally a chained back) avatar reference.

    Returns {front_path, back_path, prompt, error}. A failed back is not fatal —
    the front alone is a usable avatar.
    """
    client = get_client()
    slug = f"{name.lower().replace(' ', '_')}_{uuid.uuid4().hex[:8]}"
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)

    prompt = build_avatar_prompt(description, category, profile)
    front_path = AVATAR_DIR / f"{slug}_front.png"

    result = client.generate(prompt=prompt, aspect_ratio="3:4", image_size="2K")
    if not result.get("success"):
        return {"error": result.get("error", "avatar generation failed"), "prompt": prompt}
    if not client.download_image(result["image_url"], str(front_path)):
        return {"error": "failed to download generated avatar", "prompt": prompt}

    out: Dict[str, Any] = {"front_path": str(front_path), "back_path": None, "prompt": prompt}
    if not with_back:
        return out

    front_url = client.upload_file(str(front_path))
    if not front_url:
        out["back_error"] = "could not upload front reference for the back view"
        return out

    back_path = AVATAR_DIR / f"{slug}_back.png"
    back_result = client.generate(prompt=BACK_INSTRUCTION, reference_images=[front_url],
                                  aspect_ratio="3:4", image_size="2K")
    if back_result.get("success") and client.download_image(back_result["image_url"], str(back_path)):
        out["back_path"] = str(back_path)
    else:
        out["back_error"] = back_result.get("error", "back view generation failed")
    return out
