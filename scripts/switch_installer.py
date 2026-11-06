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
3. Recomp natif AYN (nx2android) : JAMAIS automatique (Thomas décide au cas par cas). L'installeur signale
   seulement qu'un portage vérifié existe ; portage manuel : `switch_installer.py recomp <titleId>`.
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
# Updates/DLC : 2e dossier de jeux d'Eden (stockage interne), NON scanné par Cocoon Shell → pas de doublon dans
# la tuile Switch, mais Eden les charge (ext_content_from_game_dirs=true).
AYN_SWITCH_EXTRA_DIR = "/sdcard/Documents/ROMs/switch"
COCOON_MEDIA = "/sdcard/Documents/downloaded_media"
COCOON_PKG = "rip.moth.cocoonshell"

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


def adb_serial():
    """Série ADB de l'AYN (USB prioritaire sur le Wi-Fi : la même console peut apparaître deux fois)."""
    rc, out, _ = run([ADB, "devices"], timeout=8)
    if rc != 0:
        return ""
    serials = [l.split("\t")[0] for l in out.splitlines()[1:] if l.strip().endswith("\tdevice")]
    usb = [s for s in serials if ":" not in s]
    return (usb or serials or [""])[0]


def adb(*args):
    s = adb_serial()
    return [ADB] + (["-s", s] if s else []) + list(args)


def adb_ready():
    return bool(adb_serial())


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
    """Base → carte SD ROMs/switch (tuile Switch de Cocoon + Eden) ; Update/DLC → Documents/ROMs/switch
    (Eden seulement). True si OK."""
    _, kind, _, _ = classify(local_path)
    remote_dir = AYN_SWITCH_DIR if kind == "base" else AYN_SWITCH_EXTRA_DIR
    return push_to_ayn_dir(local_path, remote_dir, cb)


def push_cocoon_media(system, stem, mac_media_dir, cb=None):
    """Copie jaquette / fond / capture dans le dossier médias de Cocoon AVANT le scan (Cocoon ne relit les
    médias qu'à l'ajout du jeu). Noms = nom du fichier ROM sans extension."""
    mapping = {"covers": "icon", "fanart": "hero", "screenshots": "screenshot_gameplay", "marquees": "logo"}
    for src_sub, dst_sub in mapping.items():
        for ext in (".jpg", ".png"):
            src = os.path.join(mac_media_dir, src_sub, stem + ext)
            if os.path.exists(src):
                remote_dir = f"{COCOON_MEDIA}/{system}/{dst_sub}"
                run(adb("shell", "mkdir", "-p", remote_dir), timeout=10)
                run(adb("push", src, f"{remote_dir}/{stem}{ext}"), timeout=60)
                break


def cocoon_idle():
    """Vrai si Cocoon Shell est au premier plan sur les 2 écrans (aucun jeu lancé)."""
    rc, out, _ = run(adb("shell", "dumpsys window | grep mFocusedApp"), timeout=10)
    lines = [l for l in out.splitlines() if "mFocusedApp=" in l]
    return rc == 0 and bool(lines) and all(COCOON_PKG in l for l in lines)


def cocoon_rescan(cb=None):
    """Lance « Rescanner les jeux » de Cocoon Shell (pas d'API : navigation manette/tactile via ADB).
    Uniquement si Cocoon est au repos ; sinon file d'attente (retentée par le daemon toutes les 5 min)."""
    if not adb_ready():
        queue_add({"type": "cocoon_rescan"})
        return False
    if not cocoon_idle():
        log(cb, "AYN : un jeu est en cours, scan Cocoon reporté")
        queue_add({"type": "cocoon_rescan"})
        return False
    _, power, _ = run(adb("shell", "dumpsys power | grep mWakefulness="), timeout=10)
    was_asleep = "Awake" not in power
    log(cb, "AYN : scan de la bibliothèque Cocoon (tuile Switch)…")
    steps = [
        ("shell", "input keyevent KEYCODE_WAKEUP", 1.0),
        ("shell", "input keyevent KEYCODE_HOME", 2.0),
        ("shell", "input -d 4 keyevent KEYCODE_BUTTON_START", 1.5),  # menu Start (écran du bas)
        ("shell", "input -d 4 tap 280 938", 2.5),                     # Paramètres
        ("shell", "input -d 0 tap 299 529", 1.5),                     # Bibliothèque (écran du haut)
        ("shell", "input -d 0 tap 960 533", 2.5),                     # Rescanner les jeux
        ("shell", "input -d 0 tap 1700 115", 30.0),                   # Toutes les plateformes ▶
        ("shell", "input -d 0 tap 960 945", 1.0),                     # OK
        ("shell", "input -d 0 keyevent KEYCODE_BACK", 1.0),
        ("shell", "input keyevent KEYCODE_HOME", 1.0),
    ]
    for kind, cmd, wait in steps:
        run(adb(kind, cmd), timeout=15)
        time.sleep(wait)
    if was_asleep:
        run(adb("shell", "input keyevent KEYCODE_SLEEP"), timeout=10)
    log(cb, "AYN : jeu ajouté à la tuile Switch de Cocoon ✓")
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
        args += ["--install", "--device", adb_serial()]
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


def add_console_gamelist(rom_path, gamelist_key, fallback_title, thumb_url="", meta_info=None, cb=None):
    """Ajoute une fiche ES-DE + jaquette pour n'importe quelle console hors Switch. Idempotent."""
    meta_info = meta_info or {}
    fname = os.path.basename(rom_path)
    stem = os.path.splitext(fname)[0]
    gamelist_path = os.path.join(HOME, "ES-DE/gamelists", gamelist_key, "gamelist.xml")
    media_dir = os.path.join(HOME, "ES-DE/downloaded_media", gamelist_key)
    os.makedirs(os.path.dirname(gamelist_path), exist_ok=True)
    if os.path.exists(gamelist_path):
        try:
            tree = ET.parse(gamelist_path)
            root = tree.getroot()
        except Exception:
            root = ET.Element("gameList")
            tree = ET.ElementTree(root)
    else:
        root = ET.Element("gameList")
        tree = ET.ElementTree(root)

    clean_stem = re.sub(r"\s*\([^)]*\)|\s*\[[^\]]*\]", "", stem).strip()
    name = (fallback_title or clean_stem or stem).strip()

    existing = None
    for g in root.findall("game"):
        if os.path.basename(g.findtext("path", "")) == fname:
            existing = g
            break

    if existing is None:
        g = ET.SubElement(root, "game")
        ET.SubElement(g, "path").text = f"./{fname}"
        ET.SubElement(g, "name").text = name
        if meta_info.get("Description"):
            ET.SubElement(g, "desc").text = meta_info["Description"]
        if meta_info.get("Publisher"):
            ET.SubElement(g, "publisher").text = meta_info["Publisher"]
        if meta_info.get("Genre"):
            ET.SubElement(g, "genre").text = meta_info["Genre"]
        try:
            ET.indent(tree, space="\t")
        except AttributeError:
            pass
        tree.write(gamelist_path, encoding="utf-8", xml_declaration=True)

    if thumb_url and thumb_url.startswith("http"):
        for sub in ("covers", "miximages"):
            dest = os.path.join(media_dir, sub, stem + ".jpg")
            if os.path.exists(dest):
                continue
            try:
                data = http_get(thumb_url, timeout=20)
                if len(data) > 1500:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with open(dest, "wb") as f:
                        f.write(data)
            except Exception:
                pass
    log(cb, f"Mac : fiche « {name} » ({gamelist_key.upper()}) ajoutée (Pegasus + app M4 Gaming)")
    return name


def push_to_ayn_dir(local_path, remote_dir, cb=None):
    """Copie un fichier ROM dans le dossier cible de l'AYN Thor via ADB. True si OK."""
    if not adb_ready():
        return False
    name = os.path.basename(local_path)
    remote = f"{remote_dir.rstrip('/')}/{name}"
    size = os.path.getsize(local_path)
    rc, out, _ = run(adb("shell", f"stat -c %s '{remote}' 2>/dev/null"), timeout=10)
    if rc == 0 and out.strip().isdigit() and int(out.strip()) == size:
        log(cb, f"AYN : {name} déjà présent dans {remote_dir}")
        return True
    log(cb, f"AYN : copie de {name} vers {remote_dir} ({size / 1e6:.1f} Mo)…")
    run(adb("shell", "mkdir", "-p", remote_dir), timeout=10)
    rc, _, err = run(adb("push", local_path, remote), timeout=3600)
    if rc != 0:
        log(cb, f"AYN : échec de la copie ({err[-160:]})")
        return False
    log(cb, f"AYN : {name} prêt sur l'AYN Thor ({remote_dir}) ✓")
    return True


def install_console_rom(paths, system_cfg, game_title="", thumb_url="", meta_info=None, recomp_info=None, cb=None):
    """Installe un jeu hors Switch sur Mac mini M4 (ROMs/<macDir> + ES-DE + Pegasus) et AYN Thor (ADB ou file d'attente)."""
    summary = {"mac": [], "ayn": [], "recomp": [], "finalPaths": []}
    mac_dir = os.path.join(HOME, "Games/ROMs", system_cfg["macDir"])
    os.makedirs(mac_dir, exist_ok=True)
    gamelist_key = system_cfg["gamelistKey"]
    ayn_dir = system_cfg["aynDir"]
    mac_emu = system_cfg["macEmulator"]
    ayn_emu = system_cfg["aynEmulator"]
    valid_exts = tuple(e.lower() for e in system_cfg.get("exts", []))

    mac_changed = False
    for path in paths:
        if not os.path.exists(path):
            continue
        is_bin_with_cue = path.lower().endswith(".bin") and any(p.lower().endswith(".cue") for p in paths)
        dest = os.path.join(mac_dir, os.path.basename(path))
        if os.path.realpath(os.path.dirname(path)) != os.path.realpath(mac_dir):
            shutil.move(path, dest)
        summary["finalPaths"].append(dest)

        if not is_bin_with_cue and (not valid_exts or dest.lower().endswith(valid_exts)):
            name = add_console_gamelist(dest, gamelist_key, game_title, thumb_url=thumb_url, meta_info=meta_info, cb=cb)
            summary["mac"].append(f"{name} → {mac_emu} plein écran (Pegasus + M4 Gaming)")
            mac_changed = True

        if push_to_ayn_dir(dest, ayn_dir, cb):
            if not is_bin_with_cue:
                summary["ayn"].append(f"{os.path.basename(dest)} → {ayn_emu}")
        else:
            queue_add({"type": "push_console", "path": dest, "remoteDir": ayn_dir})
            if not is_bin_with_cue:
                summary["ayn"].append(f"AYN hors ligne : copie vers {ayn_emu} dès sa reconnexion")
                log(cb, "AYN non connectée : copie mise en file d'attente")

    if mac_changed:
        log(cb, "Mac : mise à jour de Pegasus…")
        refresh_frontends()

    if recomp_info and (recomp_info.get("m4") or recomp_info.get("ayn")):
        parts = []
        if recomp_info.get("m4") and recomp_info.get("m4Tool"):
            parts.append(f"Mac M4 : {recomp_info['m4Tool']}")
        if recomp_info.get("ayn") and recomp_info.get("aynTool"):
            parts.append(f"AYN Thor : {recomp_info['aynTool']}")
        summary["recomp"].append("Portage / Recomp Natif dispo — " + " · ".join(parts))
    else:
        summary["recomp"].append(f"Mac : {mac_emu} · AYN Thor : {ayn_emu}")

    return summary


# ───────────────────────── orchestration ─────────────────────────
def install(paths, game_title="", sec_type="", cb=None):
    """Installe les fichiers téléchargés partout. Renvoie un dict de résumé."""
    summary = {"mac": [], "ayn": [], "recomp": [], "finalPaths": []}
    clean_title = re.sub(r"\s*(Switch\s+)?(NSP|XCI|NSZ)\b.*$", "", game_title or "", flags=re.I).strip()
    base_tids = set()
    mac_changed = False
    ayn_new_base = False

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

        if kind == "base" and adb_ready():
            push_cocoon_media("switch", os.path.splitext(os.path.basename(final))[0], MEDIA, cb)
        if push_to_ayn(final, cb):
            if kind == "base":
                ayn_new_base = True
                summary["ayn"].append(f"{name} → tuile Switch de l'accueil (Eden plein écran)")
            else:
                summary["ayn"].append(f"{'Update' if kind == 'update' else 'DLC'} chargé auto par Eden")
        else:
            queue_add({"type": "push", "path": final})
            summary["ayn"].append("AYN hors ligne : copie vers Eden dès sa reconnexion")
            log(cb, "AYN non connectée : copie mise en file d'attente")

    if mac_changed:
        log(cb, "Mac : mise à jour de Pegasus…")
        refresh_frontends()
    if ayn_new_base:
        cocoon_rescan(cb)

    # Recomp AYN : PAS de portage automatique (choix de Thomas, au cas par cas). On signale seulement
    # qu'un portage natif vérifié existe ; il se lance à la main : switch_installer.py recomp <titleId>.
    for btid in base_tids:
        prof = next((p for p in nx2a_profiles() if p.get("id", "").upper() == btid and p.get("android")), None)
        if prof and prof.get("status") == "playable":
            summary["recomp"].append(f"Recomp natif AYN dispo pour {prof['name']} (nx2android, à lancer au cas par cas)")
    return summary


def process_queue(cb=None):
    """Traite la file d'attente AYN (appelé par le daemon à la connexion de l'AYN)."""
    q = load_queue()
    if not q or not adb_ready():
        return 0
    remaining = []
    done = 0
    need_rescan = any(i.get("type") == "cocoon_rescan" for i in q)
    for item in q:
        ok = False
        if item.get("type") == "cocoon_rescan":
            continue  # traité en fin de file
        if item.get("type") == "push" and os.path.exists(item.get("path", "")):
            if classify(item["path"])[1] == "base":
                push_cocoon_media("switch", os.path.splitext(os.path.basename(item["path"]))[0], MEDIA, cb)
                need_rescan = True
            ok = push_to_ayn(item["path"], cb)
        elif item.get("type") == "push_console" and os.path.exists(item.get("path", "")):
            ok = push_to_ayn_dir(item["path"], item.get("remoteDir", "/storage/6531-3035/ROMs"), cb)
        elif item.get("type") == "nx2a_install" and os.path.isdir(item.get("dir", "")):
            ok = nx2a_install(item["dir"], cb)
        elif item.get("type") in ("push", "push_console"):
            ok = True  # fichier disparu : on oublie
        if ok:
            done += 1
        else:
            remaining.append(item)
    save_queue(remaining)
    if need_rescan:
        save_queue([i for i in load_queue() if i.get("type") != "cocoon_rescan"])
        cocoon_rescan(cb)  # se remet lui-même en file si l'AYN est occupée
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
