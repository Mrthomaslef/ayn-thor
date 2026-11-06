#!/usr/bin/env python3
"""
Installation automatique d'un jeu Switch fraîchement téléchargé (appelé par nxbrew_service.py) :

1. Mac mini M4 (Ryujinx plein écran + manette 8BitDo, via ryujinx_launch.sh) :
   - jeu de base dans ~/Games/ROMs/Switch, Update/DLC dans ~/Games/Updates_Switch (dossier autoload Ryujinx) ;
   - fiche ES-DE (gamelist.xml) + jaquette / fond / capture depuis api.nlib.cc ;
   - regénère metadata.pegasus.txt → le jeu apparaît dans Pegasus ET dans l'app « M4 Gaming » (1 clic).
2. AYN Thor (Eden, mappage Odin déjà global) : adb push dans /storage/6531-3035/ROMs/switch
   (Eden y lit aussi Updates/DLC : ext_content_from_game_dirs=true). AYN absente → file d'attente
   traitée par le daemon dès qu'elle se reconnecte (commande `process-queue`).
3. Recomp natif AYN (nx2android) : si le jeu figure dans `porter profiles` (statut playable) et que
   la version exigée est présente (base + update), portage automatique + installation de l'APK.
   Recomp Mac (nxnative) : signalé seulement (exige macOS 26.6 + Xcode 27.2, absents du Mac mini).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

HOME = os.path.expanduser("~")
ROMS_SWITCH = os.path.join(HOME, "Games/ROMs/Switch")
UPDATES_DIR = os.path.join(HOME, "Games/Updates_Switch")
GAMELIST = os.path.join(HOME, "ES-DE/gamelists/switch/gamelist.xml")
MEDIA = os.path.join(HOME, "ES-DE/downloaded_media/switch")
PEGASUS_SYNC = os.path.join(HOME, "ES-DE/scripts/sync_pegasus_metadata.py")

ADB = os.path.join(HOME, ".local/bin/adb")
AYN_SWITCH_DIR = "/storage/6531-3035/ROMs/switch"

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
QUEUE_FILE = os.path.join(DATA_DIR, "ayn_install_queue.json")
PROFILES_CACHE = os.path.join(DATA_DIR, "nx2android_profiles.json")

NX2A_DIR = "/Volumes/GamingSSD/Games/nx2android"
NX2A_BIN = os.path.join(NX2A_DIR, "nx2android")
NX2A_PORTS = "/Volumes/GamingSSD/Games/nx2android_ports"

SWITCH_EXTS = (".nsp", ".xci", ".nsz", ".xcz")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) ayn-thor-installer"}


# ───────────────────────── utilitaires ─────────────────────────
def log(cb, msg):
    if cb:
        cb(msg)
    else:
        print(msg, flush=True)


def run(args, timeout=60, env=None):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def classify(path, sec_type=""):
    """→ (title_id ou '', 'base'|'update'|'dlc', base_title_id ou '', version int)."""
    name = os.path.basename(path)
    m = re.search(r"\[([0-9A-Fa-f]{16})\]", name)
    tid = m.group(1).upper() if m else ""
    mv = re.search(r"\[v(\d+)\]", name)
    ver = int(mv.group(1)) if mv else 0
    if tid:
        suffix = int(tid, 16) & 0xFFF
        if suffix == 0x800:
            return tid, "update", tid[:-3] + "000", ver
        if suffix == 0:
            return tid, "base", tid, ver
        base = (int(tid, 16) & ~0xFFF) - 0x1000
        return tid, "dlc", f"{base:016X}", ver
    st = (sec_type or "").lower()
    kind = "update" if "update" in st or "upd" in name.lower() else ("dlc" if "dlc" in st else "base")
    return "", kind, "", ver


def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def adb_ready():
    rc, out, _ = run([ADB, "devices"], timeout=8)
    return rc == 0 and any(l.strip().endswith("\tdevice") for l in out.splitlines()[1:])


# ───────────────────────── 1. Mac mini ─────────────────────────
def place_on_mac(path, kind):
    """Base → ROMs/Switch ; Update/DLC → Updates_Switch (autoload Ryujinx). Renvoie le chemin final."""
    target_dir = ROMS_SWITCH if kind == "base" else UPDATES_DIR
    os.makedirs(target_dir, exist_ok=True)
    dest = os.path.join(target_dir, os.path.basename(path))
    if os.path.realpath(os.path.dirname(path)) != os.path.realpath(target_dir):
        shutil.move(path, dest)
    return dest


def add_to_gamelist(rom_path, tid, fallback_title, cb=None):
    """Ajoute la fiche ES-DE + médias (nlib.cc). Idempotent."""
    fname = os.path.basename(rom_path)
    stem = os.path.splitext(fname)[0]
    os.makedirs(os.path.dirname(GAMELIST), exist_ok=True)
    if os.path.exists(GAMELIST):
        tree = ET.parse(GAMELIST)
        root = tree.getroot()
    else:
        root = ET.Element("gameList")
        tree = ET.ElementTree(root)
    for g in root.findall("game"):
        if os.path.basename(g.findtext("path", "")) == fname:
            return g.findtext("name", "") or fallback_title

    meta = {}
    if tid:
        try:
            meta = json.loads(http_get(f"https://api.nlib.cc/nx/{tid}"))
        except Exception:
            meta = {}
    name = (meta.get("name") or fallback_title or stem).strip()

    g = ET.SubElement(root, "game")
    ET.SubElement(g, "path").text = f"./{fname}"
    ET.SubElement(g, "name").text = name
    if meta.get("description"):
        ET.SubElement(g, "desc").text = meta["description"]
    if meta.get("releaseDate"):
        ET.SubElement(g, "releasedate").text = meta["releaseDate"].replace("-", "") + "T000000"
    if meta.get("numberOfPlayers"):
        ET.SubElement(g, "players").text = str(meta["numberOfPlayers"])
    if meta.get("developer") or meta.get("publisher"):
        ET.SubElement(g, "developer").text = meta.get("developer") or meta.get("publisher")
    if meta.get("publisher"):
        ET.SubElement(g, "publisher").text = meta["publisher"]
    if meta.get("category"):
        ET.SubElement(g, "genre").text = ", ".join(meta["category"][:2])
    try:
        ET.indent(tree, space="\t")
    except AttributeError:
        pass
    tree.write(GAMELIST, encoding="utf-8", xml_declaration=True)

    if tid:
        media = {
            "covers": f"https://api.nlib.cc/nx/{tid}/icon/1024/1024",
            "fanart": f"https://api.nlib.cc/nx/{tid}/banner/1920/1080",
        }
        shots = (meta.get("screens") or {}).get("screenshots") or []
        if shots:
            media["screenshots"] = shots[0].replace("http://", "https://")
        for sub, url in media.items():
            dest = os.path.join(MEDIA, sub, stem + ".jpg")
            if os.path.exists(dest):
                continue
            try:
                data = http_get(url)
                if len(data) > 2000:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(data)
            except Exception:
                pass
    log(cb, f"Mac : fiche « {name} » ajoutée (Pegasus + app M4 Gaming)")
    return name


EMULATOR_PROCS = ["Ryujinx", "Cemu", "Dolphin", "RetroArch", "PCSX2", "RPCS3", "duckstation", "PPSSPP"]


def refresh_frontends():
    """Regénère metadata.pegasus.txt ; relance Pegasus (s'il tourne et qu'aucun jeu n'est en cours) pour
    qu'il affiche le nouveau jeu. L'app M4 Gaming relit les gamelists toute seule (cache 30 s)."""
    run(["/usr/bin/python3", PEGASUS_SYNC], timeout=180)
    rc, _, _ = run(["/usr/bin/pgrep", "-x", "pegasus-fe"], timeout=5)
    if rc != 0:
        return
    for p in EMULATOR_PROCS:
        if run(["/usr/bin/pgrep", "-f", p], timeout=5)[0] == 0:
            return
    run(["/usr/bin/pkill", "-9", "-x", "pegasus-fe"], timeout=5)
    time.sleep(1.5)
    run(["/usr/bin/open", "-g", "-a", "/Applications/Pegasus.app"], timeout=15)


# ───────────────────────── 2. AYN Thor (Eden) ─────────────────────────
def load_queue():
    try:
        with open(QUEUE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_queue(q):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False, indent=2)


def queue_add(item):
    q = load_queue()
    if item not in q:
        q.append(item)
    save_queue(q)


def push_to_ayn(local_path, cb=None):
    """Copie le fichier dans le dossier Eden de la carte SD de l'AYN. True si OK."""
    if not adb_ready():
        return False
    name = os.path.basename(local_path)
    remote = f"{AYN_SWITCH_DIR}/{name}"
    size = os.path.getsize(local_path)
    rc, out, _ = run([ADB, "shell", f"stat -c %s '{remote}' 2>/dev/null"], timeout=10)
    if rc == 0 and out.strip().isdigit() and int(out.strip()) == size:
        log(cb, f"AYN : {name} déjà présent dans Eden")
        return True
    log(cb, f"AYN : copie de {name} vers Eden ({size / 1e9:.2f} Go)…")
    run([ADB, "shell", "mkdir", "-p", AYN_SWITCH_DIR], timeout=10)
    rc, _, err = run([ADB, "push", local_path, remote], timeout=3600)
    if rc != 0:
        log(cb, f"AYN : échec de la copie ({err[-160:]})")
        return False
    log(cb, f"AYN : {name} prêt dans Eden ✓")
    return True


# ───────────────────────── 3. Recomp natif (nx2android) ─────────────────────────
def nx2a_env():
    env = dict(os.environ)
    env["SSL_CERT_FILE"] = "/etc/ssl/cert.pem"
    return env


def nx2a_profiles():
    """Liste officielle des jeux portables (porter profiles --json), en cache 12 h."""
    try:
        if time.time() - os.path.getmtime(PROFILES_CACHE) < 12 * 3600:
            with open(PROFILES_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    if not os.path.exists(NX2A_BIN):
        return []
    rc, out, _ = run([NX2A_BIN, "-m", "porter", "profiles", "--json"], timeout=120, env=nx2a_env())
    if rc != 0:
        return []
    try:
        data = json.loads(out)
        with open(PROFILES_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return data
    except Exception:
        return []


def find_switch_files(base_tid):
    """Tous les fichiers (base, updates, DLC) du jeu présents sur le Mac."""
    found = []
    for d in (ROMS_SWITCH, UPDATES_DIR):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            if not fn.lower().endswith(SWITCH_EXTS):
                continue
            tid, kind, btid, ver = classify(fn)
            if btid == base_tid:
                found.append((os.path.join(d, fn), kind, ver))
    return found


def try_nx2android(base_tid, cb=None):
    """Porte et installe le recomp natif AYN si le jeu est vérifié et la bonne version présente.
    → (statut, message)."""
    prof = next((p for p in nx2a_profiles() if p.get("id", "").upper() == base_tid and p.get("android")), None)
    if not prof:
        return "none", ""
    if prof.get("status") not in ("playable",):
        return "skip", f"Recomp AYN nx2android : statut « {prof.get('status')} » (non jouable), Eden utilisé"
    m = re.match(r"v(\d+)", prof.get("tested", ""))
    need_ver = int(m.group(1)) if m else 0
    files = find_switch_files(base_tid)
    if not any(k == "base" for _, k, _ in files):
        return "wait", "Recomp AYN : jeu de base manquant"
    versions = {v for _, k, v in files if k in ("base", "update")}
    if need_ver and need_ver not in versions:
        return "wait", (f"Recomp AYN nx2android disponible ({prof['name']}) : télécharge l'Update "
                        f"v{need_ver} pour le générer automatiquement")
    if not os.path.exists(NX2A_BIN):
        return "wait", "Recomp AYN : outil nx2android absent"

    port_dir = os.path.join(NX2A_PORTS, base_tid)
    os.makedirs(port_dir, exist_ok=True)
    for p, _, _ in files:
        link = os.path.join(port_dir, os.path.basename(p))
        if not os.path.lexists(link):
            os.symlink(p, link)

    args = [NX2A_BIN, "-m", "porter", "port", port_dir, "--progress", "lines"]
    if need_ver:
        args += ["-p", f"v{need_ver}"]
    installed = adb_ready()
    if installed:
        args.append("--install")
    log(cb, f"Recomp AYN : portage natif nx2android de {prof['name']} (quelques minutes)…")
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=nx2a_env(),
                         cwd=NX2A_DIR)
    last = 0.0
    tail = []
    for line in p.stdout:
        line = line.strip()
        if not line:
            continue
        tail = (tail + [line])[-12:]
        if time.time() - last > 2:
            last = time.time()
            log(cb, f"Recomp AYN : {line[:140]}")
    p.wait()
    with open(os.path.join(port_dir, "last_port.log"), "w", encoding="utf-8") as f:
        f.write("\n".join(tail))
    if p.returncode != 0:
        return "error", f"Recomp AYN : échec du portage ({tail[-1][:160] if tail else 'voir last_port.log'})"
    if not installed:
        queue_add({"type": "nx2a_install", "tid": base_tid, "dir": port_dir})
        return "queued", f"Recomp AYN {prof['name']} prêt : installation dès que l'AYN se connecte"
    return "ok", f"Recomp AYN natif « {prof['name']} » installé (app {prof.get('package', '')}) ✓"


def nx2a_install(port_dir, cb=None):
    datas = [d for d in os.listdir(port_dir) if d.endswith("-data") and os.path.isdir(os.path.join(port_dir, d))]
    if not datas or not adb_ready():
        return False
    rc, out, err = run([NX2A_BIN, "-m", "porter", "install", os.path.join(port_dir, datas[0])],
                       timeout=3600, env=nx2a_env())
    log(cb, "Recomp AYN installé ✓" if rc == 0 else f"Recomp AYN : échec install ({(err or out)[-160:]})")
    return rc == 0


# ───────────────────────── orchestration ─────────────────────────
def install(paths, game_title="", sec_type="", cb=None):
    """Installe les fichiers téléchargés partout. Renvoie un dict de résumé."""
    summary = {"mac": [], "ayn": [], "recomp": [], "finalPaths": []}
    clean_title = re.sub(r"\s*(Switch\s+)?(NSP|XCI|NSZ)\b.*$", "", game_title or "", flags=re.I).strip()
    base_tids = set()
    mac_changed = False

    for path in paths:
        if not path.lower().endswith(SWITCH_EXTS) or not os.path.exists(path):
            continue
        tid, kind, btid, _ = classify(path, sec_type)
        final = place_on_mac(path, kind)
        summary["finalPaths"].append(final)
        if kind == "base":
            name = add_to_gamelist(final, tid, clean_title, cb)
            summary["mac"].append(f"{name} → Ryujinx plein écran (Pegasus + M4 Gaming)")
            mac_changed = True
        else:
            summary["mac"].append(f"{'Update' if kind == 'update' else 'DLC'} chargé auto par Ryujinx")
        if btid:
            base_tids.add(btid)

        if push_to_ayn(final, cb):
            summary["ayn"].append(f"{os.path.basename(final)} → Eden")
        else:
            queue_add({"type": "push", "path": final})
            summary["ayn"].append("AYN hors ligne : copie vers Eden dès sa reconnexion")
            log(cb, "AYN non connectée : copie mise en file d'attente")

    if mac_changed:
        log(cb, "Mac : mise à jour de Pegasus…")
        refresh_frontends()

    for btid in base_tids:
        st, msg = try_nx2android(btid, cb)
        if msg:
            summary["recomp"].append(msg)
        if st in ("none", "skip"):
            summary["recomp"].append("Mac : Ryujinx (recomp nxnative non installable : exige macOS 26.6 + Xcode)")
    return summary


def process_queue(cb=None):
    """Traite la file d'attente AYN (appelé par le daemon à la connexion de l'AYN)."""
    q = load_queue()
    if not q or not adb_ready():
        return 0
    remaining = []
    done = 0
    for item in q:
        ok = False
        if item.get("type") == "push" and os.path.exists(item.get("path", "")):
            ok = push_to_ayn(item["path"], cb)
        elif item.get("type") == "nx2a_install" and os.path.isdir(item.get("dir", "")):
            ok = nx2a_install(item["dir"], cb)
        elif item.get("type") == "push":
            ok = True  # fichier disparu : on oublie
        if ok:
            done += 1
        else:
            remaining.append(item)
    save_queue(remaining)
    return done


def main():
    import argparse

    ap = argparse.ArgumentParser(description="Installation auto Switch : Mac (Ryujinx/Pegasus/M4) + AYN (Eden/nx2android)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("install")
    pi.add_argument("files", nargs="+")
    pi.add_argument("--title", default="")
    pi.add_argument("--type", default="")
    sub.add_parser("process-queue")
    pr = sub.add_parser("recomp")
    pr.add_argument("tid")
    a = ap.parse_args()
    if a.cmd == "install":
        print(json.dumps(install(a.files, a.title, a.type), ensure_ascii=False, indent=2))
    elif a.cmd == "process-queue":
        print(process_queue())
    elif a.cmd == "recomp":
        print(try_nx2android(a.tid.upper()))


if __name__ == "__main__":
    main()
