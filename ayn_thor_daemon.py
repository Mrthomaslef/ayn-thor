#!/usr/bin/env python3
"""
Daemon de contrôle et configuration à distance de l'AYN Thor branchée au Mac mini :
1. Serveur HTTP + API REST sur le port 8795 (CORS activé pour GitHub Pages / iPhone / Mac).
2. Détection temps réel USB IOKit (AYN Thor QCS_KALAMAP) + ADB USB & Wi-Fi.
3. Écran interactif à distance (/api/screen.jpg + /api/input tap/swipe/key/text/launch).
4. Synchronisation 1-clic des 23 dossiers de Sauvegardes, 18 systèmes ROMs (103 Go) et Drivers Turnip.
5. Watchdog USB -> ADB Wi-Fi automatique (active `adb tcpip 5555` dès que l'interface ADB apparaît).
6. Tunnel Cloudflare HTTPS permanent avec mise à jour automatique de `endpoint.json` sur GitHub Pages.
"""
import datetime
import glob
import http.server
import json
import mimetypes
import os
import re
import socket
import socketserver
import subprocess
import threading
import time
import urllib.parse

ROOT_DIR = "/Users/thomaslefevre/.t3/projects/ayn-thor"
DATA_DIR = os.path.join(ROOT_DIR, "data")
INVENTORY_JSON = os.path.join(DATA_DIR, "inventory.json")
ENDPOINT_JSON = os.path.join(ROOT_DIR, "endpoint.json")
SAVES_ROOT = "/Volumes/GamingSSD/Games/Sauvegardes_AYN_Thor"
ROMS_ROOT = "/Volumes/GamingSSD/Games/ROMs"
BONUS_ROOT = "/Volumes/GamingSSD/Games/Outils_Bonus"
ADB_BIN = "/Users/thomaslefevre/.local/bin/adb"
PORT = 8798
LAN_URL = f"http://192.168.1.111:{PORT}"

TRANSFER_JOBS = {}
JOBS_LOCK = threading.Lock()
ADB_LOCK = threading.Lock()
CURRENT_TUNNEL_URL = ""
LAST_STATUS_CACHE = None
LAST_STATUS_TS = 0.0


def run_cmd(args, timeout=8):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def get_live_status(force=False):
    global LAST_STATUS_CACHE, LAST_STATUS_TS
    now = time.time()
    if not force and LAST_STATUS_CACHE is not None and (now - LAST_STATUS_TS) < 8.0:
        return dict(LAST_STATUS_CACHE)
    status = {
        "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        "lanUrl": LAN_URL,
        "tunnelUrl": CURRENT_TUNNEL_URL,
        "usb": {
            "connected": False,
            "vendor": "AYN",
            "productName": "",
            "serial": "",
            "idVendor": 0,
            "idProduct": 0,
            "idProductHex": "",
            "usbMode": "disconnected",
            "usbModeLabel": "Non détectée sur le bus USB",
        },
        "adb": {
            "ready": False,
            "unauthorized": False,
            "devices": [],
            "serial": "",
            "transport": "none",
            "battery": None,
            "storage": None,
            "wifiIp": None,
            "model": "AYN Thor (QCS8550)",
            "androidVersion": "",
            "resolution": "1920x1080",
        },
    }

    # 1. Inspect USB via ioreg
    rc, out, _ = run_cmd(["/usr/sbin/ioreg", "-p", "IOUSB", "-l", "-w", "0"], timeout=4)
    if rc == 0 and ("QCS_KALAMAP" in out or '"USB Vendor Name" = "AYN"' in out):
        status["usb"]["connected"] = True
        idx = out.find("QCS_KALAMAP")
        if idx == -1:
            idx = out.find('"USB Vendor Name" = "AYN"')
        chunk = out[max(0, idx - 600) : min(len(out), idx + 1600)]
        m_prod = re.search(r'"USB Product Name" = "([^"]+)"', chunk)
        if m_prod:
            status["usb"]["productName"] = m_prod.group(1)
        m_ser = re.search(r'"USB Serial Number" = "([^"]+)"', chunk)
        if m_ser:
            status["usb"]["serial"] = m_ser.group(1)
        m_vid = re.search(r'"idVendor" = (\d+)', chunk)
        if m_vid:
            status["usb"]["idVendor"] = int(m_vid.group(1))
        m_pid = re.search(r'"idProduct" = (\d+)', chunk)
        if m_pid:
            pid = int(m_pid.group(1))
            status["usb"]["idProduct"] = pid
            status["usb"]["idProductHex"] = hex(pid)
            if pid == 20193:  # 0x4ee1
                status["usb"]["usbMode"] = "mtp_only_no_adb"
                status["usb"]["usbModeLabel"] = "Branchée en USB (MTP 0x4EE1) — Débogage USB inactif ou écran verrouillé"
            elif pid in (19985, 20194, 20199):  # 0x4e11, 0x4ee2, 0x4ee7
                status["usb"]["usbMode"] = "adb_enabled"
                status["usb"]["usbModeLabel"] = f"Branchée en USB — Interface ADB Active ({hex(pid)})"
            else:
                status["usb"]["usbMode"] = f"usb_{hex(pid)}"
                status["usb"]["usbModeLabel"] = f"Branchée en USB ({hex(pid)})"

    # 2. Inspect ADB
    if os.path.exists(ADB_BIN):
        with ADB_LOCK:
            rc, adb_out, _ = run_cmd([ADB_BIN, "devices", "-l"], timeout=6)
        if rc == 0:
            devs = []
            for line in adb_out.splitlines()[1:]:
                line = line.strip()
                if not line or line.startswith("*"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    devs.append({"id": parts[0], "state": parts[1], "raw": line})
            status["adb"]["devices"] = devs
            if any(d["state"] == "unauthorized" for d in devs):
                status["adb"]["unauthorized"] = True
                status["adb"]["serial"] = devs[0]["id"]
            ready_devs = [d for d in devs if d["state"] == "device"]
            if ready_devs:
                active = ready_devs[0]["id"]
                status["adb"]["ready"] = True
                status["adb"]["serial"] = active
                status["adb"]["transport"] = "wifi" if ":" in active else "usb"

                # Query battery, storage, wifi IP
                _, bat_out, _ = run_cmd([ADB_BIN, "-s", active, "shell", "dumpsys", "battery"], timeout=2)
                m_lvl = re.search(r"level:\s*(\d+)", bat_out)
                if m_lvl:
                    status["adb"]["battery"] = int(m_lvl.group(1))

                _, df_out, _ = run_cmd([ADB_BIN, "-s", active, "shell", "df", "-h", "/sdcard"], timeout=2)
                lines = [l.strip() for l in df_out.splitlines() if l.strip()]
                if len(lines) >= 2:
                    cols = lines[-1].split()
                    if len(cols) >= 5:
                        status["adb"]["storage"] = {
                            "total": cols[1],
                            "used": cols[2],
                            "avail": cols[3],
                            "percent": cols[4],
                        }

                _, sd_out, _ = run_cmd([ADB_BIN, "-s", active, "shell", "df", "-h", "/storage/6531-3035"], timeout=2)
                sd_lines = [l.strip() for l in sd_out.splitlines() if l.strip()]
                if len(sd_lines) >= 2:
                    scols = sd_lines[-1].split()
                    if len(scols) >= 5:
                        status["adb"]["sdCard"] = {
                            "mount": "/storage/6531-3035",
                            "total": scols[1],
                            "used": scols[2],
                            "avail": scols[3],
                            "percent": scols[4],
                        }

                _, ip_out, _ = run_cmd([ADB_BIN, "-s", active, "shell", "ip", "route"], timeout=2)
                m_ip = re.search(r"src\s+(\d+\.\d+\.\d+\.\d+)", ip_out)
                if m_ip:
                    status["adb"]["wifiIp"] = m_ip.group(1)

                _, ver_out, _ = run_cmd([ADB_BIN, "-s", active, "shell", "getprop", "ro.build.version.release"], timeout=2)
                if ver_out:
                    status["adb"]["androidVersion"] = ver_out

                status["adb"]["resolution"] = "1920x1080 (Haut) + 1240x1080 (Bas)"

    LAST_STATUS_CACHE = status
    LAST_STATUS_TS = time.time()
    return dict(status)


def start_transfer_job(title, cmd_args):
    job_id = f"job-{int(time.time() * 1000)}"
    job = {
        "id": job_id,
        "title": title,
        "status": "running",
        "startedAt": datetime.datetime.now().strftime("%H:%M:%S"),
        "output": "",
    }
    with JOBS_LOCK:
        TRANSFER_JOBS[job_id] = job

    def worker():
        rc, out, err = run_cmd(cmd_args, timeout=600)
        with JOBS_LOCK:
            job["status"] = "done" if rc == 0 else "error"
            job["output"] = (out + "\n" + err).strip()
            job["finishedAt"] = datetime.datetime.now().strftime("%H:%M:%S")

    threading.Thread(target=worker, daemon=True).start()
    return job


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class AynThorHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")

    def _send_json(self, payload, code=200):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/api/ping":
            return self._send_json({"ok": True, "lanUrl": LAN_URL, "tunnelUrl": CURRENT_TUNNEL_URL})

        if path == "/api/status":
            with JOBS_LOCK:
                jobs = list(TRANSFER_JOBS.values())[-10:]
            st = get_live_status()
            st["jobs"] = jobs
            sync_state_file = os.path.join(DATA_DIR, "sync_state.json")
            if os.path.exists(sync_state_file):
                try:
                    with open(sync_state_file, "r", encoding="utf-8") as sf:
                        st["syncBridge"] = json.load(sf)
                except Exception:
                    pass
            return self._send_json(st)

        if path == "/api/sync-bridge":
            sync_state_file = os.path.join(DATA_DIR, "sync_state.json")
            if os.path.exists(sync_state_file):
                try:
                    with open(sync_state_file, "r", encoding="utf-8") as sf:
                        return self._send_json({"ok": True, "sync": json.load(sf)})
                except Exception:
                    pass
            return self._send_json({"ok": True, "sync": None})

        if path == "/api/inventory":
            if os.path.exists(INVENTORY_JSON):
                with open(INVENTORY_JSON, "r", encoding="utf-8") as f:
                    inv = json.load(f)
            else:
                inv = {}
            inv["live"] = get_live_status()
            return self._send_json(inv)

        if path == "/api/fs":
            target = qs.get("path", ["/sdcard"])[0]
            st = get_live_status()
            if not st["adb"]["ready"]:
                return self._send_json(
                    {
                        "ok": False,
                        "error": "ADB non connecté. Activez le Débogage USB ou ADB Wi-Fi sur l'AYN Thor.",
                        "entries": [],
                    }
                )
            rc, out, err = run_cmd([ADB_BIN, "shell", "ls", "-la", target], timeout=5)
            entries = []
            for line in out.splitlines():
                line = line.strip()
                if not line or line.startswith("total "):
                    continue
                parts = line.split(None, 7)
                if len(parts) >= 8:
                    perms, _, owner, group, size, date_str, time_str, name = parts
                    if name in (".", ".."):
                        continue
                    is_dir = perms.startswith("d") or perms.startswith("l")
                    entries.append(
                        {
                            "name": name,
                            "isDir": is_dir,
                            "size": size,
                            "date": f"{date_str} {time_str}",
                            "perms": perms,
                        }
                    )
            return self._send_json({"ok": rc == 0, "path": target, "entries": entries, "error": err})

        if path == "/api/nxbrew-catalog":
            cat_file = os.path.join(DATA_DIR, "nxbrew_catalog.json")
            if not os.path.exists(cat_file):
                return self._send_json({"ok": False, "games": [], "total": 0})
            import unicodedata
            def _norm_txt(s):
                return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii").lower()
            q = _norm_txt(qs.get("q", [""])[0].strip())
            fmt = qs.get("format", ["all"])[0].strip().lower()
            recomp_filter = qs.get("recomp", ["all"])[0].strip().lower()
            limit = min(300, max(10, int(qs.get("limit", ["120"])[0])))
            with open(cat_file, "r", encoding="utf-8") as f:
                cat = json.load(f)
            all_games = cat.get("games", [])
            sw_dir = os.path.join(ROMS_ROOT, "Switch")
            installed_files = []
            try:
                if os.path.isdir(sw_dir):
                    installed_files = sorted(os.listdir(sw_dir))
            except Exception:
                # Fallback to inventory.json if LaunchAgent lacks TCC Removable Volumes permission
                if os.path.exists(INVENTORY_JSON):
                    try:
                        with open(INVENTORY_JSON, "r", encoding="utf-8") as inv_f:
                            inv_data = json.load(inv_f)
                        for r_sys in inv_data.get("roms", []):
                            if r_sys.get("id", "").lower() == "switch":
                                installed_files = [g.get("name", "") for g in r_sys.get("games", []) if g.get("name")]
                                break
                    except Exception:
                        pass
            installed_names = [fn.lower() for fn in installed_files if fn.lower().endswith((".nsp", ".xci", ".nsz"))]

            matched = []
            for g in all_games:
                rec = g.get("recomp")
                if recomp_filter == "both" and not (rec and rec.get("m4") and rec.get("ayn")):
                    continue
                if recomp_filter == "m4" and not (rec and rec.get("m4")):
                    continue
                if recomp_filter == "ayn" and not (rec and rec.get("ayn")):
                    continue
                if recomp_filter == "any" and not rec:
                    continue
                if fmt == "nsp" and not g.get("hasNsp"):
                    continue
                if fmt == "xci" and not g.get("hasXci"):
                    continue
                if fmt == "update" and not g.get("hasUpdate"):
                    continue
                if fmt == "dlc" and not g.get("hasDlc"):
                    continue
                if q and q not in _norm_txt(g["title"]) and q not in _norm_txt(g["shortName"]) and q not in _norm_txt(g["url"]):
                    continue
                sn_clean = re.sub(r"[^a-z0-9]+", "", _norm_txt(g["shortName"]))
                is_inst = any(sn_clean and len(sn_clean) >= 4 and sn_clean in re.sub(r"[^a-z0-9]+", "", _norm_txt(fn)) for fn in installed_names)
                item = dict(g)
                item["installed"] = is_inst
                matched.append(item)

            def _sort_key(x):
                r = x.get("recomp") or {}
                rank = 0 if (r.get("m4") and r.get("ayn")) else (1 if r.get("m4") else (2 if x.get("installed") else 3))
                return (rank, x.get("shortName", "").lower())

            matched.sort(key=_sort_key)
            results = matched[:limit]
            return self._send_json(
                {
                    "ok": True,
                    "totalCatalog": cat.get("count", len(all_games)),
                    "totalMatched": len(matched),
                    "recompStats": cat.get("recompStats", {}),
                    "installedCount": len(installed_names),
                    "installedFiles": installed_files,
                    "games": results,
                }
            )

        if path == "/api/nxbrew-info":
            game_url = qs.get("url", [""])[0].strip()
            if not game_url.startswith("https://nxbrew."):
                return self._send_json({"ok": False, "error": "URL NXBrew invalide."})
            script = os.path.join(ROOT_DIR, "scripts", "nxbrew_service.py")
            rc, out, err = run_cmd([script, "info", "--url", game_url], timeout=95)
            if rc == 0 and out:
                try:
                    info_data = json.loads(out)
                    cat_file = os.path.join(DATA_DIR, "nxbrew_catalog.json")
                    if os.path.exists(cat_file):
                        with open(cat_file, "r", encoding="utf-8") as cf:
                            cat = json.load(cf)
                        slug = game_url.rstrip("/").split("/")[-1]
                        for cg in cat.get("games", []):
                            if cg.get("url", "").rstrip("/").split("/")[-1] == slug:
                                info_data["recomp"] = cg.get("recomp")
                                break
                    return self._send_json(info_data)
                except Exception:
                    pass
            return self._send_json({"ok": False, "error": err or out or "Impossible de charger la fiche du jeu."})

        if path == "/api/nxbrew-jobs":
            jobs_dir = os.path.join(DATA_DIR, "nxbrew_jobs")
            os.makedirs(jobs_dir, exist_ok=True)
            jobs = []
            for fn in sorted(os.listdir(jobs_dir), reverse=True)[:15]:
                if fn.endswith(".json"):
                    try:
                        with open(os.path.join(jobs_dir, fn), "r", encoding="utf-8") as jf:
                            jd = json.load(jf)
                            jd["jobId"] = fn[:-5]
                            jobs.append(jd)
                    except Exception:
                        pass
            return self._send_json({"ok": True, "jobs": jobs})

        if path == "/api/screen.png" or path == "/api/screen.jpg":
            st = get_live_status()
            if not st["adb"]["ready"]:
                self.send_response(503)
                self._send_cors()
                self.end_headers()
                return
            disp = qs.get("display", ["top"])[0]
            disp_id = "4630946482288158084" if disp == "bottom" else "4630946441858561667"
            try:
                raw_png = subprocess.check_output(
                    [ADB_BIN, "exec-out", "screencap", "-d", disp_id, "-p"],
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(raw_png)))
                self._send_cors()
                self.end_headers()
                self.wfile.write(raw_png)
            except Exception:
                self.send_response(500)
                self._send_cors()
                self.end_headers()
            return

        # Serve static files from ROOT_DIR
        rel = path.lstrip("/") or "index.html"
        full_path = os.path.realpath(os.path.join(ROOT_DIR, rel))
        if not full_path.startswith(os.path.realpath(ROOT_DIR)) or not os.path.isfile(full_path):
            self.send_response(404)
            self._send_cors()
            self.end_headers()
            return
        ctype, _ = mimetypes.guess_type(full_path)
        with open(full_path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self._send_cors()
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))
        body = {}
        if length > 0:
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                body = {}

        if path == "/api/adb-connect":
            action = body.get("action", "scan")
            if action == "tcpip":
                rc, out, err = run_cmd([ADB_BIN, "tcpip", "5555"], timeout=5)
                return self._send_json({"ok": rc == 0, "message": out or err})
            elif action == "pair":
                addr = body.get("address", "").strip()
                code = body.get("code", "").strip()
                if not code:
                    return self._send_json({"ok": False, "message": "Code d'association à 6 chiffres requis."})
                # Auto-discover pairing address via mDNS if not provided
                if not addr:
                    try:
                        p = subprocess.Popen(
                            ["/usr/bin/dns-sd", "-B", "_adb-tls-pairing._tcp", "local."],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )
                        time.sleep(1.2)
                        p.terminate()
                        b_out = p.stdout.read()
                        m_inst = re.search(r"_adb-tls-pairing\._tcp\.\s+(\S+)", b_out)
                        if m_inst:
                            inst = m_inst.group(1)
                            p2 = subprocess.Popen(
                                ["/usr/bin/dns-sd", "-L", inst, "_adb-tls-pairing._tcp", "local."],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True,
                            )
                            time.sleep(1.2)
                            p2.terminate()
                            l_out = p2.stdout.read()
                            m_hp = re.search(r"can be reached at\s+([^\s:]+):(\d+)", l_out)
                            if m_hp:
                                ip = socket.gethostbyname(m_hp.group(1))
                                addr = f"{ip}:{m_hp.group(2)}"
                    except Exception:
                        pass
                if not addr:
                    return self._send_json(
                        {
                            "ok": False,
                            "message": "Aucune fenêtre 'Associer avec un code' détectée sur le Wi-Fi. Gardez la popup du code à 6 chiffres ouverte sur l'AYN Thor ou indiquez IP:Port.",
                        }
                    )
                rc, out, err = run_cmd([ADB_BIN, "pair", addr, code], timeout=8)
                # After pairing, auto-connect to _adb-tls-connect._tcp
                try:
                    p3 = subprocess.Popen(
                        ["/usr/bin/dns-sd", "-B", "_adb-tls-connect._tcp", "local."],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    time.sleep(1.0)
                    p3.terminate()
                    c_out = p3.stdout.read()
                    for inst in set(re.findall(r"_adb-tls-connect\._tcp\.\s+(\S+)", c_out)):
                        p4 = subprocess.Popen(
                            ["/usr/bin/dns-sd", "-L", inst, "_adb-tls-connect._tcp", "local."],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )
                        time.sleep(1.0)
                        p4.terminate()
                        m_hp = re.search(r"can be reached at\s+([^\s:]+):(\d+)", p4.stdout.read())
                        if m_hp:
                            ip = socket.gethostbyname(m_hp.group(1))
                            run_cmd([ADB_BIN, "connect", f"{ip}:{m_hp.group(2)}"], timeout=4)
                except Exception:
                    pass
                return self._send_json({"ok": rc == 0, "message": f"{out or err} ({addr})"})
            elif action == "connect":
                addr = body.get("address", "").strip()
                if not addr:
                    return self._send_json({"ok": False, "message": "Adresse IP:Port requise."})
                if ":" not in addr:
                    addr += ":5555"
                rc, out, err = run_cmd([ADB_BIN, "connect", addr], timeout=6)
                return self._send_json({"ok": rc == 0 and "connected" in out.lower(), "message": out or err})
            else:
                # 1. Check mDNS _adb-tls-connect._tcp first
                found = []
                try:
                    p = subprocess.Popen(
                        ["/usr/bin/dns-sd", "-B", "_adb-tls-connect._tcp", "local."],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    time.sleep(1.2)
                    p.terminate()
                    c_out = p.stdout.read()
                    for inst in set(re.findall(r"_adb-tls-connect\._tcp\.\s+(\S+)", c_out)):
                        p2 = subprocess.Popen(
                            ["/usr/bin/dns-sd", "-L", inst, "_adb-tls-connect._tcp", "local."],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                        )
                        time.sleep(1.0)
                        p2.terminate()
                        m_hp = re.search(r"can be reached at\s+([^\s:]+):(\d+)", p2.stdout.read())
                        if m_hp:
                            ip = socket.gethostbyname(m_hp.group(1))
                            ep = f"{ip}:{m_hp.group(2)}"
                            found.append(ep)
                            run_cmd([ADB_BIN, "connect", ep], timeout=4)
                except Exception:
                    pass
                # 2. Scan LAN for port 5555
                _, arp_out, _ = run_cmd(["/usr/sbin/arp", "-an"], timeout=3)
                ips = re.findall(r"\((\d+\.\d+\.\d+\.\d+)\)", arp_out)
                for ip in ips:
                    if ip.endswith(".1") or ip.endswith(".255") or ip == "192.168.1.111":
                        continue
                    s = socket.socket()
                    s.settimeout(0.15)
                    if s.connect_ex((ip, 5555)) == 0:
                        found.append(f"{ip}:5555")
                        run_cmd([ADB_BIN, "connect", f"{ip}:5555"], timeout=3)
                    s.close()
                return self._send_json(
                    {
                        "ok": True,
                        "found": found,
                        "message": f"Scan terminé : {len(found)} service(s) ADB détecté(s) ({', '.join(found) or 'aucun'}).",
                    }
                )

        if path == "/api/input":
            st = get_live_status()
            if not st["adb"]["ready"]:
                return self._send_json({"ok": False, "error": "ADB non connecté sur l'AYN Thor."})
            kind = body.get("type", "")
            disp = body.get("display", "top")
            d_flag = "4" if disp == "bottom" else "0"
            if kind == "tap":
                x = int(body.get("x", 0))
                y = int(body.get("y", 0))
                rc, out, err = run_cmd([ADB_BIN, "shell", "input", "-d", d_flag, "tap", str(x), str(y)], timeout=3)
                return self._send_json({"ok": rc == 0, "output": out or err})
            elif kind == "swipe":
                x1 = int(body.get("x1", 960))
                y1 = int(body.get("y1", 900))
                x2 = int(body.get("x2", 960))
                y2 = int(body.get("y2", 200))
                dur = int(body.get("duration", 250))
                rc, out, err = run_cmd(
                    [ADB_BIN, "shell", "input", "-d", d_flag, "swipe", str(x1), str(y1), str(x2), str(y2), str(dur)],
                    timeout=4,
                )
                return self._send_json({"ok": rc == 0, "output": out or err})
            elif kind == "key":
                keycode = str(body.get("keycode", "KEYCODE_HOME"))
                rc, out, err = run_cmd([ADB_BIN, "shell", "input", "keyevent", keycode], timeout=3)
                return self._send_json({"ok": rc == 0, "output": out or err})
            elif kind == "text":
                txt = str(body.get("text", "")).replace(" ", "%s")
                rc, out, err = run_cmd([ADB_BIN, "shell", "input", "text", txt], timeout=4)
                return self._send_json({"ok": rc == 0, "output": out or err})
            elif kind == "launch":
                pkg = str(body.get("package", ""))
                rc, out, err = run_cmd(
                    [ADB_BIN, "shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
                    timeout=5,
                )
                return self._send_json({"ok": rc == 0, "output": out or err})
            return self._send_json({"ok": False, "error": "Type d'entrée inconnu."})

        if path == "/api/sync-save":
            st = get_live_status()
            if not st["adb"]["ready"]:
                return self._send_json(
                    {
                        "ok": False,
                        "error": "ADB non actif sur l'AYN Thor. Déverrouillez la console et activez le Débogage USB.",
                    }
                )
            save_id = body.get("id", "")
            direction = body.get("direction", "push")
            android_path = body.get("androidPath", f"/sdcard/Saves/{save_id}")
            local_path = os.path.join(SAVES_ROOT, save_id)
            if not os.path.isdir(local_path):
                return self._send_json({"ok": False, "error": f"Dossier introuvable : {local_path}"})

            if direction == "push":
                run_cmd([ADB_BIN, "shell", "mkdir", "-p", android_path], timeout=4)
                job = start_transfer_job(
                    f"Push Save {save_id} -> {android_path}",
                    [ADB_BIN, "push", f"{local_path}/.", android_path],
                )
            else:
                os.makedirs(local_path, exist_ok=True)
                job = start_transfer_job(
                    f"Pull Save {android_path} -> {save_id}",
                    [ADB_BIN, "pull", f"{android_path}/.", local_path],
                )
            return self._send_json({"ok": True, "job": job})

        if path == "/api/sync-bridge":
            mode = body.get("mode", "smart")
            profile_id = body.get("profile", None)
            cmd = ["/usr/bin/python3", os.path.join(ROOT_DIR, "scripts", "sync_saves_bridge.py"), "--mode", mode]
            if profile_id:
                cmd.extend(["--profile", profile_id])
            rc, out, err = run_cmd(cmd, timeout=180)
            if rc == 0 and out:
                try:
                    data = json.loads(out)
                    return self._send_json({"ok": True, "sync": data})
                except Exception:
                    pass
            return self._send_json({"ok": False, "error": err or out or "Erreur lors de la synchronisation."})

        if path == "/api/nxbrew-download":
            action = body.get("action", "download")
            jobs_dir = os.path.join(DATA_DIR, "nxbrew_jobs")
            os.makedirs(jobs_dir, exist_ok=True)
            if action == "clear":
                target_id = body.get("jobId", "").strip()
                removed = 0
                for fn in os.listdir(jobs_dir):
                    if not fn.endswith(".json"):
                        continue
                    if target_id and fn != f"{target_id}.json":
                        continue
                    fp = os.path.join(jobs_dir, fn)
                    try:
                        if not target_id:
                            with open(fp, "r", encoding="utf-8") as jf:
                                jd = json.load(jf)
                            if jd.get("status") in ("starting", "resolving", "downloading", "extracting"):
                                continue
                        os.remove(fp)
                        removed += 1
                    except Exception:
                        pass
                return self._send_json({"ok": True, "removed": removed})

            mirror_url = body.get("url", "").strip()
            game_title = body.get("title", "Switch Game").strip()
            sec_type = body.get("type", "Base Game").strip()
            if not mirror_url:
                return self._send_json({"ok": False, "error": "URL du miroir manquante."})

            script = os.path.join(ROOT_DIR, "scripts", "nxbrew_service.py")
            if action == "resolve":
                rc, out, err = run_cmd([script, "resolve", "--url", mirror_url], timeout=45)
                if rc == 0 and out:
                    try:
                        return self._send_json(json.loads(out))
                    except Exception:
                        pass
                return self._send_json({"ok": False, "error": err or out or "Impossible de déverrouiller le lien."})
            job_id = f"nx-{int(time.time() * 1000)}"
            state_file = os.path.join(jobs_dir, f"{job_id}.json")
            init_state = {
                "jobId": job_id,
                "status": "starting",
                "gameTitle": game_title,
                "sectionType": sec_type,
                "mirrorUrl": mirror_url,
                "percent": 0.0,
                "speedMBps": 0.0,
                "message": "Initialisation du téléchargement direct…",
            }
            with open(state_file, "w", encoding="utf-8") as sf:
                json.dump(init_state, sf, ensure_ascii=False, indent=2)

            subprocess.Popen(
                [
                    script,
                    "download",
                    "--url",
                    mirror_url,
                    "--title",
                    game_title,
                    "--type",
                    sec_type,
                    "--state-file",
                    state_file,
                ]
                + [
                    arg
                    for fb in (body.get("fallbacks") or [])[:8]
                    if isinstance(fb, str) and fb.strip().startswith("https://") and fb.strip() != mirror_url
                    for arg in ("--fallback", fb.strip())
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return self._send_json({"ok": True, "jobId": job_id, "state": init_state})

        if path == "/api/push-file":
            st = get_live_status()
            if not st["adb"]["ready"]:
                return self._send_json(
                    {
                        "ok": False,
                        "error": "ADB non actif sur l'AYN Thor. Déverrouillez la console et activez le Débogage USB.",
                    }
                )
            local_path = body.get("localPath", "")
            android_path = body.get("androidPath", "/sdcard/Download/")
            if not (local_path.startswith(ROMS_ROOT) or local_path.startswith(BONUS_ROOT) or local_path.startswith(SAVES_ROOT)):
                return self._send_json({"ok": False, "error": "Chemin local non autorisé."})
            if not os.path.exists(local_path):
                return self._send_json({"ok": False, "error": f"Fichier introuvable : {local_path}"})
            parent_dir = os.path.dirname(android_path.rstrip("/")) or "/sdcard/Download"
            run_cmd([ADB_BIN, "shell", "mkdir", "-p", parent_dir], timeout=4)
            job = start_transfer_job(
                f"Push {os.path.basename(local_path)} -> {android_path}",
                [ADB_BIN, "push", local_path, android_path],
            )
            return self._send_json({"ok": True, "job": job})

        self._send_json({"ok": False, "error": "Endpoint inconnu"}, code=404)


def usb_adb_watchdog():
    """Maintient la session ADB USB active et synchronise automatiquement les sauvegardes (Mac <-> GamingSSD <-> AYN Thor)."""
    was_ready = False
    last_auto_sync = 0.0
    while True:
        try:
            st = get_live_status()
            if st["adb"].get("unauthorized") and not st["adb"]["ready"]:
                with ADB_LOCK:
                    run_cmd([ADB_BIN, "reconnect"], timeout=4)

            is_ready = bool(st["adb"].get("ready"))
            now = time.time()
            # Synchronisation automatique dès le branchement ou toutes les 120 secondes
            if is_ready and not was_ready:
                marker = "/Volumes/GamingSSD/Games/BotW_AYN_Thor/.installed_on_thor"
                installer = "/Volumes/GamingSSD/Games/BotW_AYN_Thor/install_botw_thor.sh"
                if os.path.exists(installer) and not os.path.exists(marker):
                    rc_inst, _, _ = run_cmd([installer], timeout=900)
                    if rc_inst == 0:
                        with open(marker, "w") as mf:
                            mf.write(datetime.datetime.now().isoformat())
            if is_ready and (not was_ready or (now - last_auto_sync) >= 120.0):
                last_auto_sync = now
                run_cmd(
                    ["/usr/bin/python3", os.path.join(ROOT_DIR, "scripts", "sync_saves_bridge.py"), "--mode", "smart"],
                    timeout=180,
                )
            was_ready = is_ready
        except Exception:
            pass
        time.sleep(10)


def save_and_push_endpoint(tunnel_url: str) -> None:
    global CURRENT_TUNNEL_URL
    CURRENT_TUNNEL_URL = tunnel_url
    try:
        if os.path.exists(ENDPOINT_JSON):
            with open(ENDPOINT_JSON, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if existing.get("tunnelUrl") == tunnel_url and existing.get("lanUrl") == LAN_URL:
                return
    except Exception:
        pass

    payload = {
        "tunnelUrl": tunnel_url,
        "lanUrl": LAN_URL,
        "updatedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(ENDPOINT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    try:
        subprocess.run(["git", "add", "endpoint.json"], cwd=ROOT_DIR, check=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet", "--", "endpoint.json"], cwd=ROOT_DIR)
        if diff.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore(tunnel): update live AYN Thor cloudflare tunnel endpoint"],
                cwd=ROOT_DIR,
                check=True,
            )
            subprocess.run(["git", "pull", "--rebase", "--autostash", "origin", "main"], cwd=ROOT_DIR)
            subprocess.run(["git", "push", "origin", "main"], cwd=ROOT_DIR, check=True)
    except Exception as e:
        print("Git sync error:", e, flush=True)


def run_tunnel_forever() -> None:
    global CURRENT_TUNNEL_URL
    cf_candidates = glob.glob(os.path.expanduser("~/.t3/tools/cloudflared/*/*/cloudflared"))
    if not cf_candidates:
        print("cloudflared not found", flush=True)
        return
    cf_bin = cf_candidates[0]

    while True:
        try:
            if os.path.exists(ENDPOINT_JSON):
                with open(ENDPOINT_JSON, "r", encoding="utf-8") as f:
                    ep = json.load(f)
                t_url = ep.get("tunnelUrl", "")
                if t_url:
                    rc, out, _ = run_cmd(["/usr/bin/curl", "-s", "-m", "4", f"{t_url}/api/nxbrew-jobs"], timeout=5)
                    if rc == 0 and '"ok": true' in out.lower():
                        CURRENT_TUNNEL_URL = t_url
                        time.sleep(20)
                        continue
        except Exception:
            pass

        try:
            proc = subprocess.Popen(
                [cf_bin, "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            for line in proc.stdout:
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if m:
                    url = m.group(0)
                    print("Active AYN Thor Tunnel URL:", url, flush=True)
                    save_and_push_endpoint(url)
            proc.wait()
        except Exception as e:
            print("Tunnel error:", e, flush=True)
        time.sleep(5)


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    server = ThreadedHTTPServer(("0.0.0.0", PORT), AynThorHandler)
    threading.Thread(target=usb_adb_watchdog, daemon=True).start()
    threading.Thread(target=run_tunnel_forever, daemon=True).start()
    print(f"AYN Thor Remote Daemon listening on {LAN_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
