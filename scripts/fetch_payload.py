#!/usr/bin/env python3
"""Download and verify the official Hermes Agent Android (Termux) runtime package.

Nous Research publishes a signed APT repository with a self-contained
``hermes-agent`` .deb for aarch64 Android: bionic CPython 3.14, every native
wheel prebuilt for ``android_24_arm64_v8a``, Node.js, ripgrep, ffmpeg and the
Hermes source tree. This script:

1. downloads the repository key and checks its fingerprint against the one
   published in the upstream docs,
2. verifies ``InRelease`` with that key (gpgv),
3. checks the ``Packages`` index against the signed Release hashes,
4. downloads the chosen ``hermes-agent`` .deb and checks its SHA-256,
5. extracts it with ``dpkg-deb -x``.

Usage:
    fetch_payload.py --channel stable --out build/deb [--version X.Y.Z]
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

BASE = "https://hermes-assets.nousresearch.com/releases/termux"
# From website/docs/getting-started/termux.md in NousResearch/hermes-agent.
KEY_FINGERPRINT = "C572B5FDD1A29CCFA9A912B6840B0848E139156D"
ARCH = "aarch64"
COMPONENT = "main"
PACKAGE = "hermes-agent"


def log(msg: str) -> None:
    print(f"[fetch] {msg}", flush=True)


def fetch(url: str, dest: Path | None = None) -> bytes:
    log(f"GET {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "hermes-android-build/1"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        if dest is None:
            return resp.read()
        with open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh, length=1 << 20)
        return b""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_key(key_path: Path, gnupg_home: Path) -> Path:
    """Import the key into an isolated keyring and pin its fingerprint."""
    env = {**os.environ, "GNUPGHOME": str(gnupg_home)}
    out = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--show-keys", "--with-fingerprint", str(key_path)],
        check=True, capture_output=True, text=True, env=env,
    ).stdout
    fprs = [line.split(":")[9] for line in out.splitlines() if line.startswith("fpr:")]
    if not fprs or fprs[0].upper() != KEY_FINGERPRINT:
        raise SystemExit(f"repository key fingerprint mismatch: got {fprs[:1]}, want {KEY_FINGERPRINT}")
    keyring = gnupg_home / "hermes.gpg"
    subprocess.run(["gpg", "--batch", "--dearmor", "--output", str(keyring), str(key_path)],
                   check=True, env=env)
    log(f"repository key fingerprint OK ({KEY_FINGERPRINT})")
    return keyring


def verified_release(inrelease: Path, keyring: Path, gnupg_home: Path) -> str:
    env = {**os.environ, "GNUPGHOME": str(gnupg_home)}
    plain = gnupg_home / "Release"
    subprocess.run(["gpgv", "--keyring", str(keyring), "--output", str(plain), str(inrelease)],
                   check=True, env=env)
    log("InRelease signature OK")
    return plain.read_text(encoding="utf-8")


def release_hashes(release: str) -> dict[str, tuple[str, int]]:
    hashes: dict[str, tuple[str, int]] = {}
    in_sha = False
    for line in release.splitlines():
        if line.startswith("SHA256:"):
            in_sha = True
            continue
        if in_sha:
            if not line.startswith(" "):
                break
            digest, size, name = line.split()
            hashes[name] = (digest, int(size))
    if not hashes:
        raise SystemExit("Release has no SHA256 section")
    return hashes


def parse_packages(text: str) -> list[dict[str, str]]:
    stanzas, cur, last = [], {}, None
    for line in text.splitlines():
        if not line.strip():
            if cur:
                stanzas.append(cur)
            cur, last = {}, None
            continue
        if line.startswith((" ", "\t")) and last:
            cur[last] += "\n" + line.strip()
            continue
        key, _, value = line.partition(":")
        cur[key.strip()] = value.strip()
        last = key.strip()
    if cur:
        stanzas.append(cur)
    return stanzas


def newest(candidates: list[dict[str, str]]) -> dict[str, str]:
    best = candidates[0]
    for cand in candidates[1:]:
        newer = subprocess.run(
            ["dpkg", "--compare-versions", cand["Version"], "gt", best["Version"]]
        ).returncode == 0
        if newer:
            best = cand
    return best


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", default="stable", choices=["stable", "canary"])
    ap.add_argument("--version", default="", help="exact Debian version to pin (default: newest)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    suite = f"hermes-{args.channel}"
    repo = f"{BASE}/{args.channel}"
    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    with tempfile.TemporaryDirectory(prefix="hermes-gpg-") as tmp:
        gnupg_home = Path(tmp)
        os.chmod(gnupg_home, 0o700)
        key_path = gnupg_home / "key.asc"
        fetch(f"{repo}/key.asc", key_path)
        keyring = verify_key(key_path, gnupg_home)

        inrelease = gnupg_home / "InRelease"
        fetch(f"{repo}/dists/{suite}/InRelease", inrelease)
        hashes = release_hashes(verified_release(inrelease, keyring, gnupg_home))

    rel_gz = f"{COMPONENT}/binary-{ARCH}/Packages.gz"
    rel_plain = f"{COMPONENT}/binary-{ARCH}/Packages"
    index = rel_gz if rel_gz in hashes else rel_plain if rel_plain in hashes else ""
    if not index:
        raise SystemExit(f"no Packages index for {ARCH} in Release")
    raw = fetch(f"{repo}/dists/{suite}/{index}")
    want, size = hashes[index]
    if hashlib.sha256(raw).hexdigest() != want or len(raw) != size:
        raise SystemExit("Packages index does not match the signed Release")
    text_bytes = gzip.decompress(raw) if index.endswith(".gz") else raw
    log("Packages index hash OK")

    stanzas = [s for s in parse_packages(text_bytes.decode("utf-8")) if s.get("Package") == PACKAGE]
    if args.version:
        stanzas = [s for s in stanzas if s.get("Version") == args.version]
    if not stanzas:
        raise SystemExit(f"{PACKAGE} {args.version or ''} not found in {suite}")
    pkg = newest(stanzas)
    log(f"selected {PACKAGE} {pkg['Version']} ({pkg['Filename']})")

    deb = out / Path(pkg["Filename"]).name
    fetch(f"{repo}/{pkg['Filename']}", deb)
    got = sha256_file(deb)
    if got != pkg["SHA256"].lower() or deb.stat().st_size != int(pkg["Size"]):
        raise SystemExit(f".deb hash/size mismatch: {got}")
    log(".deb SHA-256 OK")

    root = out / "root"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(root)], check=True)
    meta = {"channel": args.channel, "version": pkg["Version"], "filename": pkg["Filename"],
            "sha256": got, "size": int(pkg["Size"])}
    (out / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    deb.unlink()
    log(f"extracted to {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
