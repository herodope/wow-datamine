"""Single source of truth for every literal shared across the pipeline.

Nothing else in scripts/ should contain a product code, an install path, a
CDN host or a build-filter rule. If Blizzard moves Forever to a dedicated
product code, that is a one-line change here.
"""

import re
import sys as _sys
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

# THE DISCRIMINATOR IS THE VERSION REGEX. The build-ID gate is a sanity check
# with a LOUD failure mode, not a second filter.
#
# Measured 2026-09-20 against WTL's /build/list: the version lines that have
# ever lived on this recycled product code are 1.13, 1.60, 2.5, 3.4, 4.4 and
# 5.5. Only 1.60 is Forever, and nothing else comes near it, so
# FOREVER_VERSION_PATTERN discriminates completely on its own.
#
# Do not filter on date: wago.tools backfills and re-indexes older builds, so
# dates are not a reliable discriminator.
FOREVER_VERSION_PATTERN = r"^1\.6\d\."
FOREVER_VERSION_RE = re.compile(FOREVER_VERSION_PATTERN)

# Oldest Forever build ever observed (2026-09-16). This exists to catch a
# future 1.6x collision on this product code -- a build that looks like Forever
# by version but predates it. It is NOT how Forever is identified.
#
# It was 69900 until 2026-09-20, a round number picked below the only build
# then known. That threshold silently discarded builds 69876 and 69893, which
# are real Forever builds, for four days -- and with them the possibility of
# producing the first diff, which is this project's deliverable. The defect was
# not the number. It was that rejection was SILENT: a near miss and a
# different game entirely took the same code path and made the same amount of
# noise, which is none.
#
# If this number ever needs raising, something is wrong. Lower it to match
# reality instead.
FOREVER_MIN_BUILD_ID = 69876

# Builds seen this process that matched the version pattern but failed the ID
# gate. Callers may report these at the end of a run; warn_near_miss() also
# prints each one once, the first time it is seen.
NEAR_MISSES: list = []
_warned_near_misses: set = set()


def classify_build(version: str, build_id: int) -> tuple:
    """(verdict, detail). Verdict is 'forever', 'near_miss' or 'foreign'.

    'near_miss' means the version says Forever but the build ID predates
    anything we have seen. That is the interesting case and the one that must
    never pass quietly -- see FOREVER_MIN_BUILD_ID above.
    """
    if not FOREVER_VERSION_RE.match(version or ""):
        return "foreign", "version does not match " + FOREVER_VERSION_PATTERN
    try:
        bid = int(build_id)
    except (TypeError, ValueError):
        return "near_miss", f"build id {build_id!r} is not an integer"
    if bid < FOREVER_MIN_BUILD_ID:
        return "near_miss", f"buildId {bid} < FOREVER_MIN_BUILD_ID {FOREVER_MIN_BUILD_ID}"
    return "forever", None


def warn_near_miss(version: str, build_id, detail: str) -> None:
    """Print a banner naming the build. Once per build per process."""
    key = (str(version), str(build_id))
    NEAR_MISSES.append({"version": version, "buildId": build_id, "detail": detail})
    if key in _warned_near_misses:
        return
    _warned_near_misses.add(key)

    bar = "!" * 78
    for line in (
        "",
        bar,
        f"  NEAR MISS: {version} build {build_id} looks like Forever but was REJECTED",
        f"  {detail}",
        "",
        "  The version pattern is what identifies Forever; the build-ID gate is only",
        "  a sanity check. A build reaching here is either a real Forever build older",
        f"  than any yet seen -- in which case LOWER FOREVER_MIN_BUILD_ID to {build_id}",
        "  in scripts/config.py and capture its buildConfig/cdnConfig before Blizzard",
        "  rotates them off the version list -- or a genuine 1.6x collision on this",
        "  recycled product code, which would be the first ever and is worth a look.",
        "",
        "  Do not ignore this. A silent version of this message hid builds 69876 and",
        "  69893 for four days.",
        bar,
        "",
    ):
        print(line, file=_sys.stderr, flush=True)


def is_forever_build(version: str, build_id: int, warn: bool = True) -> bool:
    """True if this build is Forever.

    A near miss -- matches the version pattern, fails the ID gate -- is warned
    about loudly before returning False, because that combination is either a
    build we should be extracting or a collision worth knowing about. Pass
    warn=False only where the caller prints its own equivalent message.
    """
    verdict, detail = classify_build(version, build_id)
    if verdict == "near_miss" and warn:
        warn_near_miss(version, build_id, detail)
    return verdict == "forever"


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


# --- Reference data (vendor/) ---------------------------------------------

# Third-party repos cloned into VENDOR_DIR by sync_refs.py. Read-only mirrors:
# they are refreshed to match upstream exactly, never committed to.
REF_REPOS = {
    "WoWDBDefs": "https://github.com/wowdev/WoWDBDefs.git",
    "wow-listfile": "https://github.com/wowdev/wow-listfile.git",
    "TACTKeys": "https://github.com/wowdev/TACTKeys.git",
}

WOWDBDEFS_DIR = VENDOR_DIR / "WoWDBDefs"
DEFINITIONS_DIR = WOWDBDEFS_DIR / "definitions"   # WTL's definitionDir
LISTFILE_REPO_DIR = VENDOR_DIR / "wow-listfile"
TACTKEYS_DIR = VENDOR_DIR / "TACTKeys"
TACTKEYS_FILE = TACTKEYS_DIR / "WoW.txt"

# The prebuilt listfile release, not the repo's split "parts" directory. This
# is the same file WTL downloads by default.
LISTFILE_URL = "https://github.com/wowdev/wow-listfile/releases/latest/download/community-listfile-withcapitals.csv"
LISTFILE_CSV = VENDOR_DIR / "community-listfile-withcapitals.csv"

WTL_DIR = VENDOR_DIR / "wow.tools.local"
WTL_URL = "http://localhost:5080"


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
