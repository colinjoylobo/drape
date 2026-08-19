"""
Shared Gemini vision plumbing for the analyzer, art director and QC judge.

Centralises the two things that were previously duplicated (and drifted) across
three scripts: EXIF-correct image loading, and the block/finish-reason handling
that must never be mistaken for a successful empty answer.
"""
import base64
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from PIL import Image, ImageOps

from ..config import GEMINI_API_KEY, VISION_URL


class VisionError(Exception):
    """Raised when the vision call cannot produce a usable answer."""


class VisionBlocked(VisionError):
    """The model refused/stopped. Callers that are judging an image must treat
    this as a hard failure, never as 'no problems found'."""


def image_part(path: str | Path, max_dim: int = 1600) -> Dict[str, Any]:
    """EXIF-corrected, downscaled, JPEG-encoded inline image part.

    EXIF correction is not cosmetic: several real product photos carry a rotation
    tag, and an uncorrected sideways image makes the model misjudge silhouette
    and coverage.
    """
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    if max_dim and max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return {"inline_data": {"mime_type": "image/jpeg",
                            "data": base64.b64encode(buf.getvalue()).decode("ascii")}}


def call_vision(parts: List[Dict[str, Any]], temperature: float = 0.0,
                timeout: int = 180) -> Dict[str, Any]:
    """POST a parts list and return parsed JSON. Raises VisionError on any
    outcome that is not a clean JSON answer."""
    if not GEMINI_API_KEY:
        raise VisionError("GEMINI_API_KEY not set")

    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": temperature,
                             "response_mime_type": "application/json"},
    }
    try:
        resp = requests.post(f"{VISION_URL}?key={GEMINI_API_KEY}", json=payload, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise VisionError(f"vision request failed: {e}") from e

    if resp.status_code != 200:
        raise VisionError(f"vision call failed: {resp.status_code} - {resp.text[:400]}")

    data = resp.json()

    block_reason = (data.get("promptFeedback") or {}).get("blockReason")
    if block_reason:
        raise VisionBlocked(f"model refused to process (blockReason={block_reason})")

    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        finish = (data.get("candidates") or [{}])[0].get("finishReason")
        if finish and finish != "STOP":
            raise VisionBlocked(f"model stopped without a verdict (finishReason={finish})")
        raise VisionError(f"no text in response: {json.dumps(data)[:400]}")

    text = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise VisionError(f"response was not valid JSON: {text[:400]}") from e


def call_vision_retry(parts: List[Dict[str, Any]], temperature: float = 0.0,
                      attempts: int = 2) -> Dict[str, Any]:
    """Retry only the transient failures (network, malformed JSON). A refusal is
    a real signal about the image, so it is never retried away."""
    last: Optional[Exception] = None
    for _ in range(attempts):
        try:
            return call_vision(parts, temperature=temperature)
        except VisionBlocked:
            raise
        except VisionError as e:
            last = e
    raise last  # type: ignore[misc]
