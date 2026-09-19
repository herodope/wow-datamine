"""Single source of truth for every literal shared across the pipeline.

Nothing else in scripts/ should contain a product code, an install path, a
CDN host or a build-filter rule. If Blizzard moves Forever to a dedicated
product code, that is a one-line change here.
"""

import re
from pathlib import Path

# --- Target -----------------------------------------------------------------

# TACT product code. Recycled by Blizzard across many Classic betas — see
# FOREVER_* below for the filter that separates Forever from everything else
# that has lived on this product.
PRODUCT_CODE = "wow_classic_beta"

INSTALL_ROOT = Path(r"A:\World of Warcraft")
FLAVOR_DIR = "_classic_beta_"
GAME_DIR = INSTALL_ROOT / FLAVOR_DIR

CDN_PATH = "tpr/wow"

# --- Forever build filter ---------------------------------------------------

# A build on PRODUCT_CODE belongs to Forever if and only if BOTH hold:
#   version matches FOREVER_VERSION_PATTERN   (1.6x — tolerant of a future 1.61)
#   buildId >= FOREVER_MIN_BUILD_ID
# Anything else on this product is a different game. Discard it silently and
# never diff across the boundary.
#
# Do not filter on date: wago.tools backfills and re-indexes older builds, so
# dates are not a reliable discriminator. Build IDs are globally monotonic
# across all Blizzard products and are the reliable secondary check.
FOREVER_VERSION_PATTERN = r"^1\.6\d\."
FOREVER_VERSION_RE = re.compile(FOREVER_VERSION_PATTERN)
FOREVER_MIN_BUILD_ID = 69900


def is_forever_build(version: str, build_id: int) -> bool:
    """True if this build is Forever. Both halves of the rule, in one place."""
    return bool(FOREVER_VERSION_RE.match(version)) and int(build_id) >= FOREVER_MIN_BUILD_ID


# --- Endpoints --------------------------------------------------------------

DEFAULT_REGION = "us"
REGIONS = ("us", "eu", "kr", "tw", "cn")


def versions_url(region: str = DEFAULT_REGION) -> str:
    """Live version list. Pipe-delimited with a '## seqn' line, not JSON."""
    return f"https://{region}.version.battle.net/v2/products/{PRODUCT_CODE}/versions"


def cdns_url(region: str = DEFAULT_REGION) -> str:
    """CDN host list. Same pipe-delimited format as versions_url()."""
    return f"https://{region}.version.battle.net/v2/products/{PRODUCT_CODE}/cdns"


# --- Repo paths -------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

BUILDS_JSON = REPO_ROOT / "builds.json"   # committed — buildConfig/cdnConfig per build
SCRIPTS_DIR = REPO_ROOT / "scripts"
OUT_DIR = REPO_ROOT / "out"               # gitignored — extracted data
REPORTS_DIR = REPO_ROOT / "reports"       # committed — diff output
VENDOR_DIR = REPO_ROOT / "vendor"         # cloned third-party tools


def build_out_dir(version: str) -> Path:
    """out/<version>.<build>/ for one build, e.g. out/1.60.1.69913/."""
    return OUT_DIR / version


def report_path(from_version: str, to_version: str) -> Path:
    """reports/<from>_to_<to>.md"""
    return REPORTS_DIR / f"{from_version}_to_{to_version}.md"


# --- Extraction -------------------------------------------------------------

# CASC reads are IO-bound; oversubscribing thrashes the disk.
MAX_WORKERS = 8

# Reasons written to the failure log. An unreadable file is expected, not
# exceptional — log it with one of these and continue.
FAILURE_ENCRYPTED = "encrypted"
FAILURE_MISSING_KEY = "missing_key"
FAILURE_BAD_BLTE = "bad_blte"
FAILURE_UNKNOWN_FORMAT = "unknown_format"

FAILURE_REASONS = (
    FAILURE_ENCRYPTED,
    FAILURE_MISSING_KEY,
    FAILURE_BAD_BLTE,
    FAILURE_UNKNOWN_FORMAT,
)
