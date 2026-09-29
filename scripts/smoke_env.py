#!/usr/bin/env python3
"""Write a shell script that starts Hermes exactly like RuntimeEnv.kt does (CI smoke test only)."""
from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--root", required=True, help="payload root as seen by the runtime")
    ap.add_argument("--files", required=True, help="the app's filesDir as seen by the runtime")
    ap.add_argument("--port", type=int, default=9119)
    ap.add_argument("--token", default="smoke-token")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    m = json.loads(args.manifest.read_text())
    r = args.root
    launcher = m["launcher"]
    prefix = f"{r}/{m['placeholders'].get('prefix_dir', 'prefix')}"
    home = f"{args.files}/home"
    python = f"{r}/{launcher['python']}"
    repo = f"{r}/{launcher['repo']}"
    site = f"{r}/{launcher['site']}"
    env = {
        "HOME": home,
        "HERMES_HOME": f"{home}/.hermes",
        "PREFIX": prefix,
        "TMPDIR": f"{args.files}/cache/tmp",
        "LD_LIBRARY_PATH": ":".join(f"{r}/{d}" for d in m["ld_library_path"]),
        "PATH": ":".join([f"{r}/{d}" for d in m["path"]] + ["/system/bin", "/system/xbin", "/vendor/bin"]),
        "SHELL": "/system/bin/sh",
        "TERM": "xterm-256color",
        "LANG": "en_US.UTF-8",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": f"{repo}:{site}",
        "PYTHONPYCACHEPREFIX": f"{home}/.cache/hermes-pycache",
        "HERMES_SITE": site,
        "HERMES_PYTHON": python,
        "HERMES_PYTHON_SRC_ROOT": repo,
        "HERMES_RUNTIME_DIR": f"{r}/tools",
        "HERMES_DASHBOARD_SESSION_TOKEN": args.token,
        "HERMES_ANDROID_APP": "smoke",
    }
    if m.get("node"):
        env["HERMES_NODE"] = f"{r}/{m['node']}"
    if m.get("web_dist"):
        env["HERMES_WEB_DIST"] = f"{r}/{m['web_dist']}"
    if m.get("cert_file"):
        env["SSL_CERT_FILE"] = env["REQUESTS_CA_BUNDLE"] = f"{r}/{m['cert_file']}"

    code = ("import os, site, sys; sys.argv[0]='hermes'; "
            "site.addsitedir(os.environ['HERMES_SITE']); "
            "from hermes_cli.main import main; sys.exit(main())")
    lines = ["#!/system/bin/sh", "unset PYTHONHOME PYTHONSTARTUP LD_PRELOAD"]
    lines += [f"export {k}={shlex.quote(v)}" for k, v in env.items()]
    lines += [f"mkdir -p {shlex.quote(env['HERMES_HOME'])} {shlex.quote(env['TMPDIR'])}", f"cd {shlex.quote(home)}"]
    lines.append('if [ "$1" = "--py" ]; then shift; exec ' + shlex.quote(python) + ' "$@"; fi')
    lines.append('if [ "$1" = "--version" ]; then exec ' + shlex.quote(python) + " -P -c " + shlex.quote(code) + " --version; fi")
    lines.append(f"exec {shlex.quote(python)} -P -c {shlex.quote(code)} dashboard --host 127.0.0.1 --port {args.port} --no-open")
    args.out.write_text("\n".join(lines) + "\n")
    args.out.chmod(0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
