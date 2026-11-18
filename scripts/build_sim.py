#!/usr/bin/env python3
"""Build wowsims/forever and the bisrun harness into bin/.

    python scripts/build_sim.py --setup-toolchains   # first time: Go, protoc, protoc-gen-go
    python scripts/build_sim.py                      # check out the pin, build what is stale
    python scripts/build_sim.py --check              # report state, build nothing
    python scripts/build_sim.py --force              # rebuild everything

Toolchains live in vendor/toolchains/ (Go, protoc, protoc-gen-go, GOPATH,
GOCACHE). Nothing is installed system-wide and nothing outside vendor/ is
written except bin/. --setup-toolchains downloads the pinned zips from
config.py and refuses any file whose sha256 differs from the pinned hash.

The sim checkout is vendor/forever at config.SIM_PIN. It is never patched:
protoc writes its generated sim/core/proto/*.pb.go there, which upstream does
not track. The harness is its own Go module (bisrun/) that points at the
checkout with a replace directive.

bin/build_info.json records the sim sha and the sha256 of the sim's item
database. When either changes, the cached item pool under sim_out/pool/ is
deleted, and findings simmed on the old values read as stale.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config  # noqa: E402

EXE = ".exe" if os.name == "nt" else ""
TC = config.TOOLCHAIN_DIR
GO = TC / "go" / "bin" / f"go{EXE}"
PROTOC = TC / "protoc" / "bin" / f"protoc{EXE}"
PROTOC_GEN_GO = TC / "bin" / f"protoc-gen-go{EXE}"
DL = TC / "_dl"

WOWSIMCLI = config.SIM_BIN_DIR / f"wowsimcli{EXE}"
BISRUN = config.SIM_BIN_DIR / f"bisrun{EXE}"
BUILD_INFO = config.SIM_BIN_DIR / "build_info.json"


def log(msg):
    print(msg, flush=True)


def die(msg, code=1):
    print(f"build_sim: {msg}", file=sys.stderr)
    sys.exit(code)


# --- toolchains ---------------------------------------------------------------


def go_env():
    """Environment for every go/protoc call: repo-local paths, no user config."""
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(GO.parent), str(PROTOC_GEN_GO.parent),
                                   str(PROTOC.parent), env.get("PATH", "")])
    env.update({
        "GOPATH": str(TC / "gopath"),
        "GOCACHE": str(TC / "gocache"),
        "GOBIN": str(PROTOC_GEN_GO.parent),
        "GOENV": str(TC / "goenv"),
        "GOTOOLCHAIN": "local",     # the pinned Go, never a silent download
        "GOTELEMETRY": "off",
        "CGO_ENABLED": "0",
    })
    return env


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(spec):
    DL.mkdir(parents=True, exist_ok=True)
    path = DL / spec["name"]
    if not path.exists() or sha256(path) != spec["sha256"]:
        log(f"  downloading {spec['url']}")
        tmp = path.with_suffix(".part")
        with urllib.request.urlopen(spec["url"], timeout=600) as r, open(tmp, "wb") as fh:
            shutil.copyfileobj(r, fh)
        tmp.replace(path)
    got = sha256(path)
    if got != spec["sha256"]:
        path.unlink()
        die(f"{spec['name']}: sha256 {got} does not match the pinned "
            f"{spec['sha256']}; refusing it")
    return path


def unzip(zpath, dest, strip_top):
    """Extract zpath into dest. strip_top drops the archive's single top folder."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    root = dest.resolve()
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = info.filename
            if strip_top:
                name = name.split("/", 1)[1] if "/" in name else ""
            if not name:
                continue
            target = (dest / name).resolve()
            if root not in target.parents and target != root:
                die(f"{zpath.name}: entry {info.filename!r} escapes the target folder")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def setup_toolchains():
    if os.name != "nt":
        die("--setup-toolchains pins the Windows zips; on another OS install "
            f"Go {config.GO_VERSION}, protoc {config.PROTOC_VERSION} and "
            f"protoc-gen-go {config.PROTOC_GEN_GO_VERSION} onto PATH")
    if go_version(GO) != config.GO_VERSION:
        log(f"Go {config.GO_VERSION} -> {GO.parent.parent}")
        unzip(fetch(config.GO_ZIP), TC / "go", strip_top=True)
    if not PROTOC.exists():
        log(f"protoc {config.PROTOC_VERSION} -> {PROTOC.parent.parent}")
        unzip(fetch(config.PROTOC_ZIP), TC / "protoc", strip_top=False)
    if not PROTOC_GEN_GO.exists():
        log(f"protoc-gen-go {config.PROTOC_GEN_GO_VERSION} -> {PROTOC_GEN_GO.parent}")
        run([str(GO), "install",
             f"google.golang.org/protobuf/cmd/protoc-gen-go@{config.PROTOC_GEN_GO_VERSION}"],
            cwd=TC)
    log("toolchains ready")


def go_version(go):
    try:
        out = subprocess.run([str(go), "version"], capture_output=True, text=True,
                             env=go_env(), timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"go(\d+\.\d+(?:\.\d+)?)", out)
    return m.group(1) if m else None


def vtuple(v):
    return tuple(int(x) for x in v.split("."))


def run(cmd, cwd, capture=False):
    log(f"  $ {' '.join(Path(c).name if i == 0 else c for i, c in enumerate(cmd))}  (in {Path(cwd).name})")
    r = subprocess.run(cmd, cwd=cwd, env=go_env(), text=True,
                       capture_output=capture)
    if r.returncode != 0:
        if capture:
            sys.stderr.write(r.stdout + r.stderr)
        die(f"{Path(cmd[0]).name} exited {r.returncode}")
    return r.stdout if capture else ""


# --- checkout -----------------------------------------------------------------


def git(*args, cwd=config.SIM_DIR):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def sim_head():
    return git("rev-parse", "HEAD") if (config.SIM_DIR / ".git").exists() else None


def ensure_checkout(ref):
    if not (config.SIM_DIR / ".git").exists():
        log(f"cloning {config.SIM_REPO_URL} -> vendor/{config.SIM_DIR.name}")
        git("clone", "--filter=blob:none", config.SIM_REPO_URL, str(config.SIM_DIR),
            cwd=config.VENDOR_DIR)
    if git("status", "--porcelain", "--untracked-files=no"):
        die(f"vendor/{config.SIM_DIR.name} has modified tracked files; the "
            "checkout must stay unpatched. Inspect it, then restore it with "
            "git checkout -- . inside that folder")
    want = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}") if ref else None
    if not want:
        git("fetch", "--quiet", "origin")
        want = git("rev-parse", "--verify", f"{ref}^{{commit}}")
    if sim_head() != want:
        log(f"checking out {want[:10]}")
        git("checkout", "--quiet", want)
    return want


def required_go():
    text = (config.SIM_DIR / "go.mod").read_text(encoding="utf-8")
    m = re.search(r"^go (\d+\.\d+(?:\.\d+)?)", text, re.M)
    return m.group(1) if m else "0"


# --- build --------------------------------------------------------------------


def newest_mtime(paths):
    return max((p.stat().st_mtime for p in paths), default=0)


def gen_protos(force):
    protos = sorted((config.SIM_DIR / "proto").glob("*.proto"))
    pb = config.SIM_DIR / "sim" / "core" / "proto" / "api.pb.go"
    if not force and pb.exists() and pb.stat().st_mtime >= newest_mtime(protos):
        log("protos: up to date")
        return
    log("protos: generating")
    protoc = str(PROTOC) if PROTOC.exists() else shutil.which("protoc")
    plugin = str(PROTOC_GEN_GO) if PROTOC_GEN_GO.exists() else shutil.which("protoc-gen-go")
    run([protoc, "-I=./proto", f"--plugin=protoc-gen-go={plugin}",
         "--go_opt=Mgoogle/protobuf/descriptor.proto=google.golang.org/protobuf/types/descriptorpb",
         "--go_out=./sim/core", *[f"./proto/{p.name}" for p in protos]],
        cwd=config.SIM_DIR)


def build(force, sha):
    config.SIM_BIN_DIR.mkdir(exist_ok=True)
    prev = read_build_info()
    stale = force or prev.get("sim_sha") != sha
    if stale or not WOWSIMCLI.exists():
        log("wowsimcli: building")
        run([str(GO), "build", "-tags=with_db", "-o", str(WOWSIMCLI), "./cmd/wowsimcli"],
            cwd=config.SIM_DIR)
    harness_src = list(config.HARNESS_DIR.glob("*.go")) + [config.HARNESS_DIR / "go.mod"]
    if stale or not BISRUN.exists() or BISRUN.stat().st_mtime < newest_mtime(harness_src):
        log("bisrun: building")
        if not (config.HARNESS_DIR / "go.sum").exists():
            run([str(GO), "mod", "tidy"], cwd=config.HARNESS_DIR)
        run([str(GO), "build", "-tags=with_db", "-o", str(BISRUN), "."],
            cwd=config.HARNESS_DIR)


def read_build_info():
    try:
        return json.loads(BUILD_INFO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_build_info(sha):
    info = {
        "sim_sha": sha,
        "go": go_version(GO) or go_version("go"),
        "db_json_sha256": sha256(config.SIM_DB_JSON),
        "db_bin_sha256": sha256(config.SIM_DB_BIN),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    prev = read_build_info()
    if (prev.get("sim_sha"), prev.get("db_json_sha256")) != (info["sim_sha"], info["db_json_sha256"]):
        if config.SIM_POOL_DIR.exists():
            shutil.rmtree(config.SIM_POOL_DIR)
            log("pool cache: invalidated (sim sha or db changed)")
    BUILD_INFO.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    return info


def check():
    info = read_build_info()
    head = sim_head()
    gv = go_version(GO) or go_version("go")
    rows = [
        ("go", gv or "MISSING", f"needs >= {required_go()}" if config.SIM_DIR.exists() else ""),
        ("protoc", "ok" if PROTOC.exists() or shutil.which("protoc") else "MISSING", ""),
        ("protoc-gen-go", "ok" if PROTOC_GEN_GO.exists() or shutil.which("protoc-gen-go") else "MISSING", ""),
        ("sim checkout", head[:10] if head else "MISSING", f"pin {config.SIM_PIN[:10]}"),
        ("wowsimcli", "ok" if WOWSIMCLI.exists() else "MISSING", ""),
        ("bisrun", "ok" if BISRUN.exists() else "MISSING", ""),
        ("built from", (info.get("sim_sha") or "-")[:10],
         "STALE: rebuild" if head and info.get("sim_sha") not in (None, head) else ""),
    ]
    for k, v, note in rows:
        log(f"{k:<14} {v:<12} {note}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--setup-toolchains", action="store_true",
                    help="download Go, protoc and protoc-gen-go into vendor/toolchains/")
    ap.add_argument("--ref", default=config.SIM_PIN,
                    help="commit to build (default: config.SIM_PIN)")
    ap.add_argument("--force", action="store_true", help="rebuild everything")
    ap.add_argument("--check", action="store_true", help="report state only")
    a = ap.parse_args()

    if a.check:
        check()
        return
    if a.setup_toolchains:
        setup_toolchains()

    sha = ensure_checkout(a.ref)
    need = required_go()
    have = go_version(GO) or go_version("go")
    if not have or vtuple(have) < vtuple(need):
        die(f"Go {have or 'not found'}; the sim's go.mod needs >= {need}. Run "
            "python scripts/build_sim.py --setup-toolchains, or install Go "
            f"{config.GO_VERSION} from https://go.dev/dl/", code=2)
    if not (PROTOC.exists() or shutil.which("protoc")) or not (
            PROTOC_GEN_GO.exists() or shutil.which("protoc-gen-go")):
        die("protoc or protoc-gen-go not found. Run python scripts/build_sim.py "
            "--setup-toolchains", code=2)

    gen_protos(a.force)
    build(a.force, sha)
    info = write_build_info(sha)
    log(f"built sim {sha[:10]} with go {info['go']}: {BISRUN.relative_to(config.REPO_ROOT)}, "
        f"{WOWSIMCLI.relative_to(config.REPO_ROOT)}")


if __name__ == "__main__":
    main()
