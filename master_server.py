#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Neon Defender public server directory.

Run on a VPS:
  python master_server.py --host 0.0.0.0 --port 8080 --data servers.json

GET  /api/servers                 -> {"servers":[...]}
POST /api/register                -> register/update a server
POST /api/heartbeat               -> refresh a server
POST /api/unregister              -> remove a server

This is deliberately stdlib-only so it can run on a small VPS without
FastAPI/Flask. The game itself only needs the GET endpoint.
"""
import argparse, json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOCK = threading.Lock()
SERVERS = {}
TTL = 45.0
DATA = "servers.json"


def load():
    global SERVERS
    try:
        with open(DATA, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            SERVERS = raw
    except Exception:
        SERVERS = {}


def save():
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(SERVERS, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA)


def clean():
    now = time.time()
    with LOCK:
        dead = [k for k, v in SERVERS.items() if now - float(v.get("last_seen", 0)) > TTL]
        for k in dead:
            SERVERS.pop(k, None)
        if dead:
            save()


def normalize(d):
    if not isinstance(d, dict):
        raise ValueError("BAD_JSON")
    address = str(d.get("address") or d.get("ip") or d.get("host") or "").strip()
    port = int(d.get("port", 5000))
    if not address or len(address) > 255 or not 1 <= port <= 65535:
        raise ValueError("BAD_ADDRESS")
    return {
        "name": str(d.get("name") or "Neon Defender")[:40],
        "address": address,
        "port": port,
        "players": max(0, int(d.get("players", 0))),
        "max_players": max(1, int(d.get("max_players", 2))),
        "version": str(d.get("version", ""))[:16],
        "mode": str(d.get("mode", "coop"))[:16],
        "map": str(d.get("map", "default"))[:24],
        "description": str(d.get("description", ""))[:120],
        "country": str(d.get("country", ""))[:3],
        "online": True,
        "last_seen": time.time(),
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/api/servers":
            return self._json(404, {"error": "NOT_FOUND"})
        clean()
        with LOCK:
            items = []
            for v in SERVERS.values():
                x = dict(v)
                x.pop("last_seen", None)
                items.append(x)
        items.sort(key=lambda x: (x.get("name", "").lower(), x.get("address", "")))
        self._json(200, {"servers": items[:200]})

    def _body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0 or n > 16384:
            raise ValueError("BAD_BODY")
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if route not in ("/api/register", "/api/heartbeat", "/api/unregister"):
            return self._json(404, {"error": "NOT_FOUND"})
        try:
            d = normalize(self._body())
            key = "%s:%d" % (d["address"], d["port"])
        except Exception as e:
            return self._json(400, {"error": str(e)})
        with LOCK:
            if route == "/api/unregister":
                SERVERS.pop(key, None)
            else:
                SERVERS[key] = d
            save()
        self._json(200, {"ok": True})

    def log_message(self, fmt, *args):
        print("[%s] %s" % (time.strftime("%H:%M:%S"), fmt % args))


def main():
    global DATA
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--data", default="servers.json")
    args = ap.parse_args()
    DATA = args.data
    load()
    print("Neon Defender master server: http://%s:%d/api/servers" % (args.host, args.port))
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
