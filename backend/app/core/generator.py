"""
Image generation, pinned to the Servicing org.

Routing is fixed here rather than passed in per request. The org header alone is
not sufficient: the session must be bound to a Labs project entity, which is what
satisfies the backend's tool-access check for an org whose standalone Model Garden
toggle is off. Splitting those two apart would produce confusing 403s, so they are
set together and are not parameters.
"""
import io
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image, ImageOps

from ..config import (API_UTILS, DEFAULT_IMAGE_SIZE, SERVICING_ORG_ID,
                      SERVICING_TASK_SESSION_ID)

if API_UTILS:
    sys.path.insert(0, str(API_UTILS))
try:
    from nanobanana_fallback import NanoBananaFallback  # noqa: E402
except ImportError as e:  # pragma: no cover - configuration error, not a code path
    raise ImportError(
        "Could not import nanobanana_fallback, which provides the model-garden "
        "client. Set DRAPE_SHARED_UTILS to the directory containing it "
        "(see backend/.env.example)."
    ) from e

SEEDREAM_MODEL = "bytedance/seedream-v5-pro-edit"
IMAGE_SIZES = ["portrait_4_3", "auto_1K", "auto_2K", "square_hd"]


class DrapeClient(NanoBananaFallback):
    """Seedream multi-reference edit, always through the Servicing org."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._org_headers = {"X-Organization-Id": SERVICING_ORG_ID}
        self._session_id = SERVICING_TASK_SESSION_ID

        # The inherited client builds its own requests too; wrap it so the org
        # header cannot be lost on a code path we did not override.
        original = self.token_manager.make_request

        def with_org(method, url, **kw):
            kw["headers"] = {**self._org_headers, **(kw.get("headers") or {})}
            return original(method, url, **kw)

        self.token_manager.make_request = with_org

    def upload_file(self, file_path: str) -> Optional[str]:
        """Upload via {base_url}/api/v1/uploads — the inherited upload_to_s3()
        points at a host that now 403s. EXIF-corrects first, so the reference the
        model actually receives is right-side-up rather than relying on the model
        to compensate."""
        path = Path(file_path)
        img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        file_bytes = buf.getvalue()

        url = f"{self.base_url}/api/v1/uploads"
        data = {"session_id": self._session_id}

        def post():
            return self.token_manager.session.post(
                url,
                files={"file": (path.name, file_bytes, "image/jpeg")},
                data=data,
                headers={"Authorization": self.token_manager.get_auth_header(), **self._org_headers},
                timeout=120,
            )

        resp = post()
        if resp.status_code == 401 and self.token_manager.refresh_access_token():
            resp = post()
        if resp.status_code != 200:
            return None

        result = resp.json()
        if isinstance(result, list) and result:
            result = result[0]
        return result.get("file_url") or result.get("url") or result.get("s3_url")

    def generate_seedream(self, prompt: str, image_urls: List[str], output_path: str,
                          image_size: str = DEFAULT_IMAGE_SIZE) -> Dict[str, Any]:
        """Named distinctly from the inherited generate(), which is Nano Banana and
        is still used for avatar creation."""
        payload = {
            "model_path": SEEDREAM_MODEL,
            "session_id": self._session_id,
            "form_fields": {
                "prompt": prompt,
                "image_urls": image_urls,
                "image_size": image_size,
                "num_images": 1,
                "output_format": "png",
                # The provider's checker refuses ordinary lingerie and nightwear
                # product photography; QC is what actually gates output here.
                "enable_safety_checker": False,
            },
        }
        resp = self.token_manager.make_request("POST", self.submit_url, json=payload, timeout=120)
        if resp.status_code != 200:
            return {"success": False, "error": f"submit failed: {resp.status_code} - {resp.text[:300]}"}

        data = resp.json()
        prediction_id = (data.get("prediction_id") or data.get("jobrouter_job_id")
                         or data.get("id") or data.get("job_id"))
        if not prediction_id:
            return {"success": False, "error": f"no prediction id in response: {str(data)[:300]}"}

        result = self._poll_for_result(prediction_id, "seedream")
        if result.get("success") and output_path:
            if self.download_image(result["image_url"], output_path):
                result["output_path"] = output_path
        return result

    def generate_with_refs(self, prompt: str, reference_paths: List[str], output_path: str,
                           image_size: str = DEFAULT_IMAGE_SIZE) -> Dict[str, Any]:
        urls = []
        for p in reference_paths:
            url = self.upload_file(p)
            if not url:
                return {"success": False, "error": f"failed to upload {Path(p).name}"}
            urls.append(url)
        return self.generate_seedream(prompt, urls, output_path, image_size)


def get_client() -> DrapeClient:
    return DrapeClient()
