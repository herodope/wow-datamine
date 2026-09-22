#!/usr/bin/env python3
"""Add any build -- Forever or not -- to out/<version>/ as a queryable wow.db.

One command for the whole sequence that used to be four:

    switch WTL to the product  ->  export DB2s (plain + hotfixed)
      ->  build out/<version>/wow.db  ->  switch WTL back

Builds are namespaced by directory, which is already how this repo works:
`out/<version>/wow.db`, reached with `query.py --build <version>`. Nothing is
shared between builds and nothing here can overwrite another build's data --
the output directory is derived from the version string, so a second build is
purely additive.

WTL serves exactly ONE build at a time, so ingesting a second product means
switching what it has loaded. That is done over HTTP (`/casc/switchProduct`,
`/casc/switchConfigs`), which is in-memory only: `SaveSettings()` is called
from SettingsController alone, never from CASCController, so config.json keeps
naming whatever product it named before and a WTL restart returns to it. The
switch is still a visible change to a running service, so it happens only when
you pass --switch-product, and --restore-product puts it back afterwards.

Usage:
    # a new Forever build, every table (the patch-day path)
    python scripts/add_build.py 1.60.1.70000

    # a second product, just the spell tables
    python scripts/add_build.py 1.15.9.69722 \\
        --switch-product wow_classic_era --restore-product wow_classic_beta \\
        --tables SpellEffect,SpellName,SpellMisc

    python scripts/add_build.py --print-loaded    # what is WTL serving?
"""

import argparse
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import config

SWITCH_TIMEOUT = 1800   # a cold CDN load indexes the whole root; minutes, not seconds
PROBE_TIMEOUT = 30


def log(msg=""):
    print(msg, file=sys.stderr, flush=True)


def wtl_get(path, params=None, timeout=PROBE_TIMEOUT):
    url = config.WTL_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "wow-datamine/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace").strip()


def loaded_build():
    try:
        status, body = wtl_get("/casc/buildname")
    except (urllib.error.URLError, OSError) as exc:
        log(f"WTL is not reachable at {config.WTL_URL}: {exc}")
        log("  Start it first:  .\\scripts\\run-wtl.ps1")
        raise SystemExit(2)
    if status != 200 or not body:
        log(f"/casc/buildname answered HTTP {status} with {body!r}")
        raise SystemExit(2)
    return body


def switch(product, buildconfig=None, cdnconfig=None):
    """Point WTL at another product. Returns the build it ends up serving.

    With no configs this is /casc/switchProduct, which resolves the CURRENT
    live build for config.DEFAULT_REGION off the version service -- so it lands
    on whatever that product is shipping today. Pin an older build by passing
    its buildConfig/cdnConfig, which routes to /casc/switchConfigs instead.

    Both spin server-side until the build is loaded (CASCController's
    `while (true)` on IsTACTSharpInit), so a slow first load blocks the
    response rather than returning early with nothing loaded. A build that
    fails to load never sets the flag and the request never returns, which is
    why this has a timeout and reports the loaded build afterwards rather than
    trusting the `true` in the body.
    """
    before = loaded_build()
    if buildconfig and cdnconfig:
        log(f"switching WTL: {product} @ {buildconfig[:8]}../{cdnconfig[:8]}.. (was {before})")
        path, params = "/casc/switchConfigs", {
            "product": product, "buildconfig": buildconfig, "cdnconfig": cdnconfig}
    else:
        log(f"switching WTL to {product}, current live build (was {before})")
        path, params = "/casc/switchProduct", {"product": product}

    started = time.monotonic()
    try:
        status, body = wtl_get(path, params, timeout=SWITCH_TIMEOUT)
    except (urllib.error.URLError, OSError) as exc:
        log(f"  switch request failed after {time.monotonic() - started:.0f}s: {exc}")
        log(f"  WTL is now serving: {loaded_build()}")
        raise SystemExit(2)

    now = loaded_build()
    log(f"  HTTP {status} {body!r} in {time.monotonic() - started:.0f}s -> serving {now}")
    if body.lower() != "true":
        # readOnly=true in config.json makes both routes a no-op returning false.
        log("  switch returned false -- is readOnly set in WTL's config.json?")
        raise SystemExit(2)
    return now


def run(argv, label):
    log("")
    log(f"--- {label}")
    log(f"    {' '.join(argv)}")
    rc = subprocess.call([sys.executable] + argv)
    if rc != 0:
        log(f"{label} exited {rc}")
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build", nargs="?", help="version string, e.g. 1.15.9.69722")
    ap.add_argument("--product", help="TACT product; only used with --buildconfig/--cdnconfig")
    ap.add_argument("--switch-product", metavar="PRODUCT",
                    help="point WTL at this product before extracting")
    ap.add_argument("--buildconfig", help="pin a specific build (needs --cdnconfig)")
    ap.add_argument("--cdnconfig", help="pin a specific build (needs --buildconfig)")
    ap.add_argument("--restore-product", metavar="PRODUCT",
                    help="switch WTL back to this product when done, pass or fail")
    ap.add_argument("--tables", help="comma-separated subset; default is every table")
    ap.add_argument("--workers", type=int, default=config.MAX_WORKERS)
    ap.add_argument("--no-gametables", action="store_true")
    ap.add_argument("--restart", action="store_true", help="ignore the extract checkpoint")
    ap.add_argument("--print-loaded", action="store_true", help="print WTL's build and exit")
    args = ap.parse_args(argv)

    if args.print_loaded:
        print(loaded_build())
        return 0
    if not args.build and not args.switch_product:
        ap.error("give a build version, or --switch-product to take whatever it loads")
    if bool(args.buildconfig) != bool(args.cdnconfig):
        ap.error("--buildconfig and --cdnconfig go together")

    started = time.monotonic()
    rc = 0
    try:
        if args.switch_product:
            served = switch(args.switch_product, args.buildconfig, args.cdnconfig)
            if args.build and args.build != served:
                # Extraction would read from disk for a build WTL does not have
                # loaded, and for a product just switched to there is nothing on
                # disk -- so this mismatch yields 404s, not data.
                log("")
                log(f"asked for {args.build} but WTL loaded {served}.")
                log("  Pin the build with --buildconfig/--cdnconfig, or drop the")
                log("  version argument to take whatever the product is serving.")
                return 2
            args.build = served
        else:
            served = loaded_build()
            if args.build != served:
                log(f"NOTE: WTL has {served} loaded; {args.build} will be read from disk")

        extract = ["scripts/extract_db2.py", "--build", args.build,
                   "--workers", str(args.workers), "--allow-non-forever"]
        if args.tables:
            extract += ["--tables", args.tables]
        if args.restart:
            extract.append("--restart")
        rc = run(extract, "extract_db2")
        if rc:
            return rc

        if not args.tables and not args.no_gametables:
            # A table subset is a targeted pull; dragging every scaling curve
            # along with it is not what was asked for.
            run(["scripts/extract_gametables.py", "--build", args.build], "extract_gametables")

        build = ["scripts/build_db.py", "--build", args.build]
        if args.no_gametables or args.tables:
            build.append("--no-gametables")
        rc = run(build, "build_db")
        if rc:
            return rc
    finally:
        if args.restore_product:
            log("")
            try:
                switch(args.restore_product)
            except SystemExit:
                log("RESTORE FAILED -- WTL is left on the other product.")
                log(f"  Put it back with: curl {config.WTL_URL}"
                    f"/casc/switchProduct?product={args.restore_product}")
                raise

    log("")
    log(f"  {args.build} -> {config.build_out_dir(args.build) / 'wow.db'}")
    log(f"  {time.monotonic() - started:.0f}s total")
    log(f"  query it with:  python scripts/query.py --build {args.build} \"SELECT ...\"")
    return rc


if __name__ == "__main__":
    sys.exit(main())
