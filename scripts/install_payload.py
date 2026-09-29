#!/usr/bin/env python3
"""Install payload.zip the way the app's PayloadInstaller.kt does (CI smoke test only).

Extracts into --dest, but resolves placeholders against --final-root: the path the
tree will have when it is used (inside the Android test container).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import zipfile
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", type=Path, required=True)
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--dest", type=Path, required=True)
    ap.add_argument("--final-root", required=True)
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text())
    dest = args.dest
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(args.zip) as zf:
        for name in zf.namelist():
            if name.startswith("/") or ".." in name.split("/"):
                raise SystemExit(f"unsafe entry {name}")
        zf.extractall(dest)

    ph = m["placeholders"]
    final = args.final_root
    prefix = f"{final}/{ph.get('prefix_dir', 'prefix')}"

    def resolve(value: str) -> str:
        return value.replace(ph["root"], final).replace(ph["prefix"], prefix)

    for rel in m["rewrite"]:
        f = dest / rel
        if f.is_file():
            text = f.read_bytes().decode("latin-1")
            f.write_bytes(resolve(text).encode("latin-1"))
    for rel, target in m["symlinks"]:
        link = dest / rel
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        os.symlink(resolve(target), link)
    for rel in m["executables"]:
        f = dest / rel
        if f.is_file():
            f.chmod(0o755)
    (dest / ".payload_id").write_text(m["payload_id"])
    print(f"installed {m['payload_id']}: {len(m['rewrite'])} rewritten, "
          f"{len(m['symlinks'])} links, {len(m['executables'])} executables")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
