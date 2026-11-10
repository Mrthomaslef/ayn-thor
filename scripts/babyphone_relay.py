#!/usr/bin/env python3
"""Relais BabyPhone (Mac mini) — utilisé quand la liaison directe WebRTC échoue (4G/CGNAT).

- Serveur WebSocket local (port 8797) : salons identifiés par un hash du code d'appairage.
  Tout ce qu'envoie le « bébé » (AYN) part vers les « parents » du salon, et inversement.
- Tunnel Cloudflare rapide vers ce port ; l'URL publique est publiée dans
  babyphone/relay.json (GitHub Pages) pour que l'appli la retrouve.

Lancé en continu par launchd : com.thomas.babyphone-relay
"""
import asyncio
import glob
import json
import os
import re
import subprocess
import threading
import time
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

import websockets
from websockets.asyncio.server import broadcast, serve

PORT = 8797
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELAY_JSON = os.path.join(ROOT_DIR, "babyphone", "relay.json")
ROOM_RE = re.compile(r"^[a-f0-9]{16,64}$")

rooms: dict[str, dict[str, set]] = {}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def health(connection, request):
    if request.headers.get("Upgrade", "").lower() != "websocket":
        return connection.respond(HTTPStatus.OK, "babyphone relay ok\n")
    return None


async def notify_counts(room: str):
    r = rooms.get(room)
    if not r:
        return
    msg = json.dumps({"t": "relay", "parents": len(r["parent"]), "baby": len(r["baby"]) > 0})
    broadcast(r["baby"] | r["parent"], msg)


async def handler(ws):
    q = parse_qs(urlparse(ws.request.path).query)
    room = (q.get("room") or [""])[0]
    role = (q.get("role") or [""])[0]
    if not ROOM_RE.match(room) or role not in ("baby", "parent"):
        await ws.close(1008, "bad room/role")
        return
    r = rooms.setdefault(room, {"baby": set(), "parent": set()})
    r[role].add(ws)
    log(f"+ {role} {room[:6]} (bébé={len(r['baby'])} parents={len(r['parent'])})")
    await notify_counts(room)
    other = "parent" if role == "baby" else "baby"
    try:
        async for msg in ws:
            broadcast(r[other], msg)
    except websockets.ConnectionClosed:
        pass
    finally:
        r[role].discard(ws)
        log(f"- {role} {room[:6]}")
        if not r["baby"] and not r["parent"]:
            rooms.pop(room, None)
        else:
            await notify_counts(room)


def publish(url: str):
    wss = url.replace("https://", "wss://")
    try:
        with open(RELAY_JSON, encoding="utf-8") as f:
            if json.load(f).get("url") == wss:
                return
    except Exception:
        pass
    with open(RELAY_JSON, "w", encoding="utf-8") as f:
        json.dump({"url": wss, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, f)
        f.write("\n")
    rel = os.path.relpath(RELAY_JSON, ROOT_DIR)
    try:
        subprocess.run(["git", "add", rel], cwd=ROOT_DIR, check=True)
        subprocess.run(["git", "commit", "-m", "chore(babyphone): URL du relais", "--", rel], cwd=ROOT_DIR, check=True)
        subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], cwd=ROOT_DIR)
        subprocess.run(["git", "push", "origin", "main"], cwd=ROOT_DIR, check=True)
        log("relay.json publié :", wss)
    except Exception as e:
        log("git error:", e)


def tunnel_forever():
    cands = sorted(glob.glob(os.path.expanduser("~/.t3/tools/cloudflared/*/*/cloudflared")))
    if not cands:
        log("cloudflared introuvable")
        return
    while True:
        try:
            p = subprocess.Popen([cands[-1], "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in p.stdout:
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if m:
                    log("tunnel :", m.group(0))
                    threading.Thread(target=publish, args=(m.group(0),), daemon=True).start()
            p.wait()
        except Exception as e:
            log("tunnel error:", e)
        time.sleep(5)


async def main():
    if os.environ.get("BABYRELAY_NO_TUNNEL") != "1":
        threading.Thread(target=tunnel_forever, daemon=True).start()
    async with serve(handler, "0.0.0.0", PORT, process_request=health, max_size=2**16, ping_interval=20, ping_timeout=20):
        log(f"relais BabyPhone en écoute sur :{PORT}")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
