"""
Drape configuration.

Generation routing is fixed, not a request parameter. The provider account and
session a generation runs under are set once here and read from the environment,
so a request cannot redirect where work is billed.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
STORAGE_DIR = BASE_DIR / "backend" / "storage"
DB_PATH = STORAGE_DIR / "drape.db"

# --- Environment -------------------------------------------------------------
# Load before anything derives a value from it, or every os.getenv below reads
# an empty environment.
DRAPE_ENV = BASE_DIR / "backend" / ".env"
load_dotenv(DRAPE_ENV, override=True)

# An empty assignment in a .env file is a placeholder, not a value. Left in place
# it blocks a fallback file from supplying the real thing, and the failure only
# shows up much later as "Missing credentials" at generation time.
for _name in ("base_url", "access_token", "refresh_token", "access_key"):
    if os.environ.get(_name) == "":
        del os.environ[_name]

# Optional second credentials file, for setups where generation credentials are
# managed outside this repo. It fills gaps only — Drape's own .env always wins.
_extra_env = os.getenv("DRAPE_EXTRA_ENV")
if _extra_env:
    load_dotenv(Path(_extra_env).expanduser(), override=False)

# --- External paths ----------------------------------------------------------
# Both optional and both from the environment. A hardcoded absolute path makes the
# app work on exactly one machine and bakes the author's directory layout into a
# shared repository.
#
# DRAPE_SHARED_UTILS holds the shared generation client module this app builds on.
# DRAPE_ALLOWED_ROOT is an extra directory the file server may read images from, so
# garments can be referenced where they already sit rather than copied into the app.
API_UTILS = Path(os.environ["DRAPE_SHARED_UTILS"]).expanduser() \
    if os.getenv("DRAPE_SHARED_UTILS") else None
ALLOWED_ROOT = Path(os.environ["DRAPE_ALLOWED_ROOT"]).expanduser() \
    if os.getenv("DRAPE_ALLOWED_ROOT") else None

# --- Generation provider ------------------------------------------------------
# Read from the environment, never committed. The session id is a capability
# rather than a label — anyone holding it can spend the associated account's
# credits — so it belongs in backend/.env (gitignored). See .env.example.
PROVIDER_ORG_ID = os.getenv("DRAPE_PROVIDER_ORG_ID", "")
PROVIDER_PROJECT_ID = os.getenv("DRAPE_PROVIDER_PROJECT_ID", "")
PROVIDER_ENTITY_ID = os.getenv("DRAPE_PROVIDER_ENTITY_ID", "")
PROVIDER_SESSION_ID = os.getenv("DRAPE_PROVIDER_SESSION_ID", "")

# --- Vision (analysis + QC) ---------------------------------------------------
# GEMINI_API_KEY_AISTUDIO first: the plain key is on a free tier that rate-limits
# at ~20 req/min, which a batch analysis run hits almost immediately.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY_AISTUDIO") or os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY_PRESENT = bool(GEMINI_API_KEY)
VISION_MODEL = "gemini-2.5-flash"
VISION_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{VISION_MODEL}:generateContent"

# --- Generation defaults ----------------------------------------------------
# Both set from the A/B run in docs/ab_findings.md, which found neither a second
# avatar reference nor auto_2K improved the QC pass rate (9/9 passed either way).
# portrait_4_3 wins on a defect QC cannot see: auto_* picks its own aspect ratio and
# returned a LANDSCAPE frame on one of three garments, which is unusable in a
# catalogue where every shot must share a frame. Both are overridable per generation.
DEFAULT_IMAGE_SIZE = "portrait_4_3"
DEFAULT_AVATAR_REF_COUNT = 1

# Shoot-craft profile for new work. v1 is the original prompt behaviour, kept so
# existing shots stay reproducible; v2 adds the campaign craft layer.
DEFAULT_PROMPT_PROFILE = "v2"

CATEGORIES = ["Dresses", "Lingerie", "Nightwear", "Sportswear", "Tops", "Outerwear", "Other"]

for _d in (STORAGE_DIR, STORAGE_DIR / "uploads", STORAGE_DIR / "generations",
           STORAGE_DIR / "avatars", STORAGE_DIR / "crops"):
    _d.mkdir(parents=True, exist_ok=True)


def missing_credentials() -> list:
    """Names of settings required for generation that are absent. Checked at
    startup so a misconfiguration is visible immediately rather than after a user
    has set up a whole session and paid for the discovery."""
    required = {
        "access_token": os.getenv("access_token"),
        "refresh_token": os.getenv("refresh_token"),
        "access_key": os.getenv("access_key"),
        "DRAPE_PROVIDER_ORG_ID": PROVIDER_ORG_ID,
        "DRAPE_PROVIDER_SESSION_ID": PROVIDER_SESSION_ID,
    }
    missing = [name for name, value in required.items() if not value]
    if not GEMINI_API_KEY_PRESENT:
        missing.append("GEMINI_API_KEY_AISTUDIO or GEMINI_API_KEY")
    if API_UTILS is None or not API_UTILS.exists():
        missing.append("DRAPE_SHARED_UTILS")
    return missing
