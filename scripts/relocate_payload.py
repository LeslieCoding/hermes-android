#!/usr/bin/env python3
"""Relocate the upstream Termux payload so it can run from an Android app's private dir.

The upstream ``hermes-agent`` .deb is laid out for the Termux prefix
``/data/data/com.termux/files/usr`` (payload at ``$PREFIX/lib/hermes-agent``),
and each bundled tool is a staged Termux tree
(``tools/<tool>/data/data/com.termux/files/usr/...``). The app cannot use that
path, so this script produces a relocatable archive:

* nested ``tools/<tool>/data/data/com.termux/files/usr`` trees are flattened to
  ``tools/<tool>``;
* in every text file (ELF and other binaries are left untouched; the app sets
  ``LD_LIBRARY_PATH`` instead of relying on RUNPATH) the Termux paths are
  rewritten to placeholders the app substitutes at install time:
  ``@@HERMES_ROOT@@`` (the payload root) and ``@@TERMUX_PREFIX@@`` (a small
  fake prefix inside it); ``$PREFIX/bin/sh`` and ``env`` point at /system;
* symlinks, executable bits and the rewrite list go into ``payload.json``
  because ``java.util.zip`` keeps neither;
* a dynamic-linker closure report lists NEEDED libraries the payload does not
  ship and Android does not provide.

Usage:
    relocate_payload.py --deb-root build/deb/root --web-dist build/web_dist \
        --meta build/deb/meta.json --out build/payload
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

TERMUX_PREFIX = "/data/data/com.termux/files/usr"
PAYLOAD_IN_PREFIX = "lib/hermes-agent"
ROOT_PH = "@@HERMES_ROOT@@"
PREFIX_PH = "@@TERMUX_PREFIX@@"
NESTED = "data/data/com.termux/files/usr"

# Ordered byte-level rewrites for text files and absolute symlink targets.
_PATH_CHAR = rb"[A-Za-z0-9_.+-]"
REWRITES: list[tuple[re.Pattern[bytes], bytes]] = [
    (re.compile(rb"tools/(" + _PATH_CHAR + rb"+)/data/data/com\.termux/files/usr(?=/|\b|$)"), rb"tools/\1"),
    (re.compile(re.escape(f"{TERMUX_PREFIX}/{PAYLOAD_IN_PREFIX}".encode())), ROOT_PH.encode()),
    (re.compile(re.escape(f"{TERMUX_PREFIX}/bin/sh".encode()) + rb"(?!" + _PATH_CHAR + rb")"), b"/system/bin/sh"),
    (re.compile(re.escape(f"{TERMUX_PREFIX}/bin/env".encode()) + rb"(?!" + _PATH_CHAR + rb")"), b"/system/bin/env"),
    (re.compile(re.escape(TERMUX_PREFIX.encode())), PREFIX_PH.encode()),
]
NEEDLE = b"com.termux/files/usr"

# Public NDK / platform libraries every Android device provides (API 24+).
ANDROID_SYSTEM_LIBS = {
    "libc.so", "libm.so", "libdl.so", "liblog.so", "libz.so", "libandroid.so", "libEGL.so",
    "libGLESv1_CM.so", "libGLESv2.so", "libGLESv3.so", "libjnigraphics.so", "libmediandk.so",
    "libOpenMAXAL.so", "libOpenSLES.so", "libvulkan.so", "libstdc++.so", "libcamera2ndk.so",
    "libnativewindow.so", "libaaudio.so", "libneuralnetworks.so", "libsync.so", "libamidi.so",
    "libbinder_ndk.so", "libicu.so", "ld-android.so", "libnativehelper.so",
}

BINARY_SUFFIXES = {
    ".pyc", ".so", ".whl", ".zip", ".gz", ".xz", ".bz2", ".zst", ".png", ".jpg", ".jpeg",
    ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".mp3", ".wav", ".ogg",
    ".node", ".a", ".o", ".db", ".sqlite", ".pdf", ".icns",
}
STORED_SUFFIXES = {".zip", ".gz", ".xz", ".bz2", ".zst", ".png", ".jpg", ".jpeg", ".gif",
                   ".webp", ".woff", ".woff2", ".mp3", ".ogg", ".whl"}


def log(msg: str) -> None:
    print(f"[relocate] {msg}", flush=True)


def rewrite_bytes(data: bytes) -> bytes:
    for pattern, repl in REWRITES:
        data = pattern.sub(repl, data)
    return data


def is_elf(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def looks_binary(path: Path, head: bytes) -> bool:
    return path.suffix.lower() in BINARY_SUFFIXES or b"\x00" in head[:8192]


def map_old_rel(rel: PurePosixPath) -> PurePosixPath:
    """Old payload-relative path -> flattened path (tools/<t>/data/data/.../usr/x -> tools/<t>/x)."""
    parts = rel.parts
    nested = tuple(NESTED.split("/"))
    for i in range(len(parts) - len(nested) + 1):
        if parts[i:i + len(nested)] == nested:
            return map_old_rel(PurePosixPath(*parts[:i], *parts[i + len(nested):]))
    return rel


def flatten(stage: Path) -> None:
    """Move every nested Termux prefix tree up to its owner directory."""
    moved = True
    while moved:
        moved = False
        for dirpath, dirnames, _ in os.walk(stage):
            here = Path(dirpath)
            nested = here / NESTED
            if "data" in dirnames and nested.is_dir() and not nested.is_symlink():
                for child in list(nested.iterdir()):
                    target = here / child.name
                    if target.exists() or target.is_symlink():
                        raise SystemExit(f"flatten clash: {target}")
                    child.rename(target)
                shutil.rmtree(here / "data")
                log(f"flattened {here.relative_to(stage)}/{NESTED}")
                moved = True
                break


def relink(stage: Path) -> list[list[str]]:
    """Record every symlink with a target valid for the flattened layout, then remove it.

    Must run BEFORE flatten(): relative targets are resolved against the link's
    original location, and both ends are mapped through map_old_rel().
    """
    links: list[list[str]] = []
    payload_abs = PurePosixPath(TERMUX_PREFIX) / PAYLOAD_IN_PREFIX
    for dirpath, dirnames, filenames in os.walk(stage):
        for name in dirnames + filenames:
            link = Path(dirpath) / name
            if not link.is_symlink():
                continue
            old_rel = PurePosixPath(link.relative_to(stage).as_posix())
            new_rel = map_old_rel(old_rel)
            target = os.readlink(link)
            if target.startswith("/"):
                new_target = rewrite_bytes(target.encode()).decode()
            else:
                resolved = PurePosixPath(os.path.normpath((old_rel.parent / target).as_posix()))
                if resolved.parts and resolved.parts[0] == "..":
                    # Escapes the payload: express it as an absolute Termux path and rewrite.
                    absolute = os.path.normpath((payload_abs / old_rel.parent / target).as_posix())
                    new_target = rewrite_bytes(absolute.encode()).decode()
                else:
                    new_target = os.path.relpath(map_old_rel(resolved).as_posix(), new_rel.parent.as_posix())
            links.append([new_rel.as_posix(), new_target])
            link.unlink()
    links.sort()
    return links


def rewrite_and_scan(stage: Path) -> tuple[list[str], list[str], list[Path]]:
    rewritten, executables, elves = [], [], []
    for dirpath, _, filenames in os.walk(stage):
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            rel = path.relative_to(stage).as_posix()
            mode = path.stat().st_mode
            if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                executables.append(rel)
            with open(path, "rb") as fh:
                head = fh.read(8192)
            if head[:4] == b"\x7fELF":
                elves.append(path)
                continue
            if looks_binary(path, head):
                continue
            data = path.read_bytes()
            if NEEDLE not in data:
                continue
            new = rewrite_bytes(data)
            if new != data:
                path.write_bytes(new)
                if ROOT_PH.encode() in new or PREFIX_PH.encode() in new:
                    rewritten.append(rel)
    rewritten.sort()
    executables.sort()
    return rewritten, executables, elves


def needed_libs(path: Path) -> list[str]:
    try:
        out = subprocess.run(["readelf", "-d", str(path)], capture_output=True, text=True, check=False).stdout
    except FileNotFoundError:
        return []
    return re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", out)


def closure_report(stage: Path, elves: list[Path]) -> dict[str, list[str]]:
    shipped = {p.name for p in elves}
    shipped |= {p.name for p in stage.rglob("*.so*") if p.is_file()}
    missing: dict[str, list[str]] = {}
    for elf in elves:
        for lib in needed_libs(elf):
            if lib in shipped or lib in ANDROID_SYSTEM_LIBS:
                continue
            missing.setdefault(lib, []).append(elf.relative_to(stage).as_posix())
    return missing


def parse_launcher(stage: Path) -> dict[str, str]:
    launcher = stage / "bin" / "hermes"
    info: dict[str, str] = {}
    if not launcher.is_file():
        return info
    text = launcher.read_text(encoding="utf-8", errors="replace")
    for key in ("PYTHON", "REPO", "SITE"):
        m = re.search(rf"^{key}=(.+)$", text, re.M)
        if not m:
            continue
        value = m.group(1).strip()
        value = value.replace('"$root"/', "").replace("$root/", "").strip("'\"")
        info[key.lower()] = value
    return info


def build_fake_prefix(stage: Path, links: list[list[str]]) -> str | None:
    prefix = stage / "prefix"
    for sub in ("bin", "tmp", "etc/tls", "var/run"):
        (prefix / sub).mkdir(parents=True, exist_ok=True)
    for name in ("sh", "env"):
        links.append([f"prefix/bin/{name}", f"/system/bin/{name}"])
    certs = sorted(stage.glob("venv/lib/python3*/site-packages/certifi/cacert.pem"))
    if certs:
        shutil.copyfile(certs[0], prefix / "etc/tls/cert.pem")
        return "prefix/etc/tls/cert.pem"
    log("warning: certifi/cacert.pem not found in the venv")
    return None


def tool_dirs(stage: Path) -> tuple[list[str], list[str]]:
    ld, path = [], []
    tools = stage / "tools"
    for tool in sorted(tools.iterdir()) if tools.is_dir() else []:
        rel = f"tools/{tool.name}"
        if (tool / "lib").is_dir():
            ld.append(f"{rel}/lib")
        if (tool / "bin").is_dir():
            path.append(f"{rel}/bin")
        elif any(tool.iterdir()):
            path.append(rel)  # e.g. tools/ripgrep holds the rg binary directly
    if (stage / "runtime-libs/lib").is_dir():
        ld.append("runtime-libs/lib")
    for extra in ("bin", "venv/bin", "prefix/bin"):
        if (stage / extra).is_dir():
            path.insert(0, extra)
    return ld, path


def write_zip(stage: Path, zip_path: Path) -> tuple[int, int]:
    files = total = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                         allowZip64=True) as zf:
        for dirpath, dirnames, filenames in os.walk(stage):
            dirnames.sort()
            here = Path(dirpath)
            rel_dir = here.relative_to(stage).as_posix()
            if rel_dir != "." and not filenames and not dirnames:
                zf.writestr(rel_dir + "/", b"")
            for name in sorted(filenames):
                path = here / name
                rel = path.relative_to(stage).as_posix()
                ctype = zipfile.ZIP_STORED if path.suffix.lower() in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
                zf.write(path, rel, compress_type=ctype)
                files += 1
                total += path.stat().st_size
    return files, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--deb-root", type=Path, required=True)
    ap.add_argument("--web-dist", type=Path, default=None)
    ap.add_argument("--meta", type=Path, default=None)
    ap.add_argument("--drop", action="append", default=[],
                    help="payload-relative path to leave out (repeatable)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    src = args.deb_root / TERMUX_PREFIX.lstrip("/") / PAYLOAD_IN_PREFIX
    if not src.is_dir():
        raise SystemExit(f"payload not found at {src}")
    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    stage = out / "stage"
    out.mkdir(parents=True)
    log(f"copying {src}")
    shutil.copytree(src, stage, symlinks=True)

    # Bundled bytecode is useless on-device: the launcher sets PYTHONPYCACHEPREFIX, and
    # extraction resets source mtimes anyway. Dropping it saves a lot of space.
    pycache = [Path(d) / n for d, dirs, _ in os.walk(stage) for n in dirs if n == "__pycache__"]
    for victim in pycache:
        shutil.rmtree(victim, ignore_errors=True)
    log(f"removed {len(pycache)} __pycache__ dirs")

    for rel in args.drop:
        victim = stage / rel
        if victim.is_symlink() or victim.is_file():
            victim.unlink()
        elif victim.is_dir():
            shutil.rmtree(victim)
        log(f"dropped {rel}")

    if args.web_dist:
        if not (args.web_dist / "index.html").is_file():
            raise SystemExit(f"web dist has no index.html: {args.web_dist}")
        shutil.copytree(args.web_dist, stage / "web_dist")
        log("added web_dist")

    links = relink(stage)
    flatten(stage)
    rewritten, executables, elves = rewrite_and_scan(stage)
    cert = build_fake_prefix(stage, links)
    launcher = parse_launcher(stage)
    ld, path = tool_dirs(stage)
    missing = closure_report(stage, elves)

    for need in ("python", "repo", "site"):
        if need not in launcher:
            raise SystemExit(f"could not read {need.upper()}= from bin/hermes")
    if not (stage / launcher["python"]).exists() and not any(l[0] == launcher["python"] for l in links):
        raise SystemExit(f"launcher python missing: {launcher['python']}")

    meta = json.loads(args.meta.read_text()) if args.meta else {}
    stamp = {}
    stamp_path = stage / "app" / "install-stamp.json"
    if stamp_path.is_file():
        try:
            stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
        except ValueError:
            stamp = {}

    zip_path = out / "payload.zip"
    files, total = write_zip(stage, zip_path)
    manifest = {
        "format": 1,
        "upstream": {"deb": meta, "stamp": {k: stamp.get(k) for k in ("commit", "base_version", "display_version")}},
        "placeholders": {"root": ROOT_PH, "prefix": PREFIX_PH, "prefix_dir": "prefix"},
        "launcher": launcher,
        "ld_library_path": ld,
        "path": path,
        "cert_file": cert,
        "web_dist": "web_dist" if args.web_dist else None,
        "node": next((p for p in ("tools/node/bin/node",) if (stage / p).exists()), None),
        "symlinks": links,
        "executables": executables,
        "rewrite": rewritten,
        "missing_libs": missing,
        "file_count": files,
        "total_bytes": total,
        "zip_bytes": zip_path.stat().st_size,
    }
    manifest["payload_id"] = f"{meta.get('version', 'dev')}-{meta.get('sha256', '')[:12]}"
    (out / "payload.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")

    log(f"{files} files, {total / 1e6:.1f} MB -> payload.zip {manifest['zip_bytes'] / 1e6:.1f} MB")
    log(f"{len(links)} symlinks, {len(executables)} executables, {len(rewritten)} rewritten text files")
    log(f"launcher: {launcher}")
    log(f"LD_LIBRARY_PATH dirs: {ld}")
    log(f"PATH dirs: {path}")
    if missing:
        log("NEEDED libraries not shipped and not provided by Android:")
        for lib, users in sorted(missing.items()):
            log(f"  {lib}  (e.g. {users[0]}, {len(users)} users)")
    else:
        log("dynamic-linker closure OK")
    shutil.rmtree(stage)
    return 0


if __name__ == "__main__":
    sys.exit(main())
