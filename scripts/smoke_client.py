#!/usr/bin/env python3
"""Talk to a running on-device Hermes like the mobile shell does (CI smoke test only).

Runs under the bundled interpreter, so it can use the bundled `websockets` package.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9119
TOKEN = sys.argv[2] if len(sys.argv) > 2 else "smoke-token"
BASE = f"http://127.0.0.1:{PORT}"


def wait_http(deadline: float) -> None:
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/api/status", timeout=3) as r:
                print("GET /api/status ->", r.status, r.read(300).decode(errors="replace"))
                return
        except urllib.error.HTTPError as e:
            print("GET /api/status ->", e.code)
            return
        except Exception as e:  # server not up yet
            last = e
            time.sleep(2)
    raise SystemExit(f"dashboard did not come up: {last}")


def main() -> int:
    wait_http(time.time() + 600)
    for path in ("/",):
        try:
            with urllib.request.urlopen(BASE + path, timeout=10) as r:
                body = r.read(2000).decode(errors="replace")
                print(f"GET {path} ->", r.status, "token-injected" if "__HERMES_SESSION_TOKEN__" in body else "")
        except urllib.error.HTTPError as e:
            print(f"GET {path} ->", e.code)

    # HTTPS through the bundled stack, the way model API calls go out. The Termux
    # prefix (and its CA bundle) is hidden, so this proves our cert settings work.
    import ssl
    for url in ("https://api.github.com/zen",):
        try:
            with urllib.request.urlopen(url, timeout=30, context=ssl.create_default_context()) as r:
                print("HTTPS urllib ->", r.status)
        except urllib.error.HTTPError as e:
            print("HTTPS urllib ->", e.code)
        import httpx
        print("HTTPS httpx ->", httpx.get(url, timeout=30).status_code)
        try:
            import truststore
            ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            with urllib.request.urlopen(url, timeout=30, context=ctx) as r:
                print("HTTPS truststore ->", r.status)
        except ImportError:
            print("truststore not bundled")

    from websockets.sync.client import connect

    ws = connect(f"ws://127.0.0.1:{PORT}/api/ws?token={TOKEN}",
                 additional_headers={"Origin": BASE}, open_timeout=30, max_size=None)
    ready = json.loads(ws.recv(timeout=60))
    print("first frame:", ready.get("params", {}).get("type"))
    n = 0

    def call(method, params=None, timeout=120):
        nonlocal n
        n += 1
        rid = f"s{n}"
        ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}))
        end = time.time() + timeout
        while time.time() < end:
            frame = json.loads(ws.recv(timeout=timeout))
            if frame.get("id") == rid:
                if "error" in frame:
                    raise SystemExit(f"{method} failed: {frame['error']}")
                return frame.get("result")
        raise SystemExit(f"{method} timed out")

    print("setup.status:", json.dumps(call("setup.status"))[:400])
    created = call("session.create", {}, timeout=300)
    print("session.create:", created.get("session_id"), json.dumps(created.get("info", {}))[:400])
    listed = call("session.list", {"limit": 5})
    print("session.list:", len(listed.get("sessions", [])), "rows")
    call("session.close", {"session_id": created["session_id"]})
    ws.close()
    print("SMOKE_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
