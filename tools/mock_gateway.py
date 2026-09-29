#!/usr/bin/env python3
"""Mock of the on-device Hermes dashboard + gateway, for testing the mobile shell in a browser.

Serves assets/ui under /__android/, a fake status.json, a placeholder dashboard at /,
and a hand-rolled WebSocket at /api/ws that speaks the gateway's JSON-RPC dialect
(events, requests, server->client approval/clarify requests).
"""
import asyncio
import base64
import hashlib
import json
import os
import struct
import sys
import time

UI = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 9119
TOKEN = "test-token"
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MIME = {"html": "text/html", "js": "text/javascript", "css": "text/css", "json": "application/json"}

SESSIONS = {"stored-1": {"title": "旧会话：安装 Python 包", "messages": [
    {"role": "user", "text": "帮我看看磁盘空间"},
    {"role": "assistant", "text": "我来检查一下。", "reasoning": "用户想知道磁盘空间，调用 df。"},
    {"role": "tool", "name": "terminal", "context": "df -h", "text": "Filesystem  Size  Used\n/data  110G  52G"},
    {"role": "assistant", "text": "**/data** 分区共 110G，已用 52G。"},
]}}


async def http_response(writer, code, ctype, body, extra=""):
    if isinstance(body, str):
        body = body.encode()
    writer.write(f"HTTP/1.1 {code} X\r\nContent-Type: {ctype}\r\nContent-Length: {len(body)}\r\n"
                 f"Cache-Control: no-store\r\n{extra}\r\n".encode() + body)
    await writer.drain()


async def ws_send(writer, obj):
    data = json.dumps(obj, ensure_ascii=False).encode()
    header = bytearray([0x81])
    n = len(data)
    if n < 126:
        header.append(n)
    elif n < 65536:
        header += bytes([126]) + struct.pack(">H", n)
    else:
        header += bytes([127]) + struct.pack(">Q", n)
    writer.write(bytes(header) + data)
    await writer.drain()


async def ws_recv(reader):
    b1, b2 = await reader.readexactly(2)
    op = b1 & 0x0F
    n = b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", await reader.readexactly(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if b2 & 0x80 else b"\0\0\0\0"
    data = bytearray(await reader.readexactly(n))
    for i in range(n):
        data[i] ^= mask[i % 4]
    return op, bytes(data)


def event(etype, sid=None, payload=None):
    p = {"type": etype}
    if sid:
        p["session_id"] = sid
    if payload is not None:
        p["payload"] = payload
    return {"jsonrpc": "2.0", "method": "event", "params": p}


async def gateway(reader, writer):
    await ws_send(writer, event("gateway.ready", payload={"skin": {}, "change_events": True, "replay_epoch": "e1", "heartbeat": True}))
    counter = {"n": 0}
    waiting = {}

    async def turn(sid, text):
        await asyncio.sleep(0.3)
        await ws_send(writer, event("message.start", sid))
        for chunk in ["让我", "想一想……", "先查一下。"]:
            await ws_send(writer, event("reasoning.delta", sid, {"text": chunk}))
            await asyncio.sleep(0.05)
        await ws_send(writer, event("tool.start", sid, {"tool_id": "t1", "name": "web_search", "context": "今日 科技 新闻"}))
        await asyncio.sleep(0.4)
        await ws_send(writer, event("tool.complete", sid, {"tool_id": "t1", "name": "web_search", "summary": "找到 8 条结果", "duration_s": 0.4}))
        # ask for approval before running a command
        rid = "srv-1"
        fut = asyncio.get_event_loop().create_future()
        waiting[rid] = fut
        await ws_send(writer, {"jsonrpc": "2.0", "id": rid, "method": "approval", "params": {
            "session_id": sid, "request_id": "a1", "command": "rm -rf ~/tmp/cache", "description": "删除缓存目录",
            "choices": ["once", "session", "always", "deny"]}})
        answer = await fut
        await ws_send(writer, event("tool.start", sid, {"tool_id": "t2", "name": "terminal", "context": "rm -rf ~/tmp/cache"}))
        await ws_send(writer, event("tool.complete", sid, {"tool_id": "t2", "name": "terminal", "result_text": f"choice={answer.get('choice')}"}))
        reply = ("## 今日要点\n\n1. **芯片**：新一代手机 SoC 发布\n2. 开源模型更新\n\n| 项目 | 状态 |\n|---|---|\n| A | ✅ |\n\n"
                 "```python\nprint('hello')\n```\n来源：https://example.com/news")
        for i in range(0, len(reply), 12):
            await ws_send(writer, event("message.delta", sid, {"text": reply[i:i + 12]}))
            await asyncio.sleep(0.02)
        await ws_send(writer, event("session.title", sid, {"session_id": "stored-new", "title": "今日科技新闻"}))
        await ws_send(writer, event("message.complete", sid, {"text": reply, "status": "complete",
                                                               "usage": {"input_tokens": 1200, "output_tokens": 340, "total_tokens": 1540}}))

    while True:
        try:
            op, data = await ws_recv(reader)
        except (asyncio.IncompleteReadError, ConnectionError):
            return
        if op == 8:
            return
        if op != 1:
            continue
        req = json.loads(data)
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        if method is None and rid in waiting:
            waiting.pop(rid).set_result(req.get("result") or {})
            continue
        result = {}
        if method == "gateway.ping":
            result = {"ok": True}
        elif method == "setup.status":
            result = {"provider_configured": False}
        elif method == "session.create":
            counter["n"] += 1
            result = {"session_id": f"live-{counter['n']}", "stored_session_id": "stored-new", "message_count": 0,
                      "messages": [], "info": {"model": "deepseek-chat", "provider": "deepseek", "title": ""}}
        elif method == "session.list":
            result = {"sessions": [{"id": k, "title": v["title"], "preview": "磁盘空间", "started_at": time.time() - 7200}
                                   for k, v in SESSIONS.items()]}
        elif method == "session.resume":
            s = SESSIONS.get(params.get("session_id"))
            if not s:
                await ws_send(writer, {"jsonrpc": "2.0", "id": rid, "error": {"code": 4004, "message": "not found"}})
                continue
            result = {"session_id": "live-r", "stored_session_id": params["session_id"], "message_count": len(s["messages"]),
                      "messages": s["messages"], "info": {"model": "deepseek-chat", "title": s["title"]}, "running": False}
        elif method == "prompt.submit":
            result = {"status": "streaming"}
            asyncio.ensure_future(turn(params["session_id"], params.get("text")))
        elif method == "session.close" or method == "session.interrupt":
            result = {}
        await ws_send(writer, {"jsonrpc": "2.0", "id": rid, "result": result})


async def handle(reader, writer):
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except Exception:
        writer.close()
        return
    lines = head.decode().split("\r\n")
    method, target, _ = lines[0].split(" ", 2)
    headers = {l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l}
    path = target.split("?", 1)[0]
    if path == "/api/ws":
        if f"token={TOKEN}" not in target:
            await http_response(writer, 403, "text/plain", "bad token")
            writer.close()
            return
        accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + GUID).encode()).digest()).decode()
        writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        await writer.drain()
        await gateway(reader, writer)
        writer.close()
        return
    if path == "/__android/status.json":
        await http_response(writer, 200, "application/json", json.dumps({
            "phase": os.environ.get("MOCK_PHASE", "running"), "message": "运行中", "progress": 0.42, "port": PORT,
            "pid": 4242, "startedAt": 0, "payloadId": "2026.9.28-abcdef", "exitCode": None,
            "appVersion": "0.1.0", "token": TOKEN}))
    elif path == "/__android/log.json":
        await http_response(writer, 200, "application/json", json.dumps({"lines": ["[app] 启动", "INFO uvicorn running"]}))
    elif path.startswith("/__android/"):
        rel = path[len("/__android/"):] or "index.html"
        f = os.path.join(UI, rel)
        if os.path.isfile(f):
            await http_response(writer, 200, MIME.get(rel.rsplit(".", 1)[-1], "application/octet-stream"), open(f, "rb").read())
        else:
            await http_response(writer, 404, "text/plain", "nf")
    else:
        await http_response(writer, 200, "text/html", "<html><body style='background:#222;color:#eee;font-family:sans-serif'><h3>Dashboard " + path + "</h3></body></html>")
    writer.close()


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", PORT)
    print(f"mock on {PORT}", flush=True)
    async with server:
        await server.serve_forever()

asyncio.run(main())
