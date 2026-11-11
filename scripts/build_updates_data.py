#!/usr/bin/env python3
"""Génère recomp/data/updates.json + recomp/data/updates_embedded.js
(page « Mises à jour » de Recomp Check).

Principe — rien d'inventé :
- On ne suit QUE les émulateurs / recomps réellement installés chez Thomas
  (apps Mac lues dans Info.plist, apps AYN Thor lues via adb, dépôts locaux git).
- Les nouveautés viennent UNIQUEMENT des notes de version officielles
  (GitHub Releases, Forgejo Ryubing / Eden, commits GitHub pour les ports compilés soi-même).
- Si la version installée ne se retrouve pas dans les releases, on compare par date
  d'installation et on l'indique explicitement (« basis: date »).
- Hors ligne (SSD débranché, AYN absente) : on réutilise la dernière version vue.
"""
import json
import os
import plistlib
import re
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "recomp" / "data"
SSD = Path("/Volumes/GamingSSD/Games")
HOME = Path.home()

# ─── Registre des outils installés ───────────────────────────────────────────
# source: ("gh", "owner/repo") | ("forgejo", "https://host/api/v1/repos/o/r") |
#         ("gh_tags", "owner/repo") | ("gh_commits", "owner/repo", branch)
# targets: device mac|ayn + comment lire la version installée
TOOLS = [
    # Émulateurs
    {"id": "azahar", "name": "Azahar", "kind": "emu", "sys": "3DS", "source": ("gh", "azahar-emu/azahar"),
     "targets": [{"device": "mac", "app": "/Applications/Azahar.app"}, {"device": "ayn", "pkg": "org.azahar_emu.azahar"}]},
    {"id": "dolphin", "name": "Dolphin", "kind": "emu", "sys": "GameCube / Wii", "source": ("gh_tags", "dolphin-emu/dolphin"),
     "notesUrl": "https://dolphin-emu.org/download/",
     "targets": [{"device": "mac", "app": "/Applications/Dolphin.app"}, {"device": "ayn", "pkg": "org.dolphinemu.dolphinemu"}]},
    {"id": "cemu", "name": "Cemu", "kind": "emu", "sys": "Wii U", "source": ("gh", "cemu-project/Cemu"),
     "targets": [{"device": "mac", "app": "/Applications/Cemu.app"}]},
    {"id": "ryujinx", "name": "Ryujinx (Ryubing)", "kind": "emu", "sys": "Switch",
     "source": ("forgejo", "https://git.ryujinx.app/api/v1/repos/ryubing/ryujinx"),
     # Info.plist de Ryubing non fiable (figé) : version lue dans le nom de l'app sur le SSD
     "targets": [{"device": "mac", "glob": (SSD, r"Ryujinx-Ryubing-([\d.]+)\.app")}]},
    {"id": "eden", "name": "Eden", "kind": "emu", "sys": "Switch",
     "source": ("forgejo", "https://git.eden-emu.dev/api/v1/repos/eden-emu/eden"),
     "targets": [{"device": "ayn", "pkg": "dev.eden.eden_emulator"}]},
    {"id": "eden-duo", "name": "Eden Duo", "kind": "emu", "sys": "Switch (double écran)", "source": ("gh", "igawa6/eden-duo"),
     "targets": [{"device": "ayn", "pkg": "dev.igawa6.edenduo"}]},
    {"id": "pcsx2", "name": "PCSX2", "kind": "emu", "sys": "PS2", "source": ("gh", "PCSX2/pcsx2"), "pages": 100,
     "targets": [{"device": "mac", "app": "/Applications/PCSX2-v2.8.2.app"}]},
    {"id": "rpcs3", "name": "RPCS3", "kind": "emu", "sys": "PS3", "source": ("gh", "RPCS3/rpcs3"),
     "targets": [{"device": "mac", "app": "/Applications/RPCS3.app"}]},
    {"id": "aps3e", "name": "aPS3e", "kind": "emu", "sys": "PS3", "source": ("gh", "aenu1/aps3e"),
     "targets": [{"device": "ayn", "pkg": "aenu.aps3e"}]},
    {"id": "rpcsx", "name": "RPCSX", "kind": "emu", "sys": "PS3", "source": ("gh", "RPCSX/rpcsx-ui-android"),
     "targets": [{"device": "ayn", "pkg": "net.rpcsx"}]},
    {"id": "xendroid", "name": "XenDroid", "kind": "emu", "sys": "Xbox 360", "source": ("gh", "rfandango/XenDroid"),
     "targets": [{"device": "ayn", "pkg": "xendroid.compose"}]},
    {"id": "duckstation", "name": "DuckStation", "kind": "emu", "sys": "PS1", "source": ("gh", "stenzek/duckstation"),
     "targets": [{"device": "mac", "app": "/Applications/DuckStation.app"}]},
    {"id": "ppsspp", "name": "PPSSPP", "kind": "emu", "sys": "PSP", "source": ("gh", "hrydgard/ppsspp"),
     "targets": [{"device": "mac", "app": "/Applications/PPSSPPSDL.app"}, {"device": "ayn", "pkg": "org.ppsspp.ppsspp"}]},
    {"id": "retroarch", "name": "RetroArch", "kind": "emu", "sys": "Multi-consoles", "source": ("gh", "libretro/RetroArch"),
     "targets": [{"device": "mac", "app": "/Applications/RetroArch.app"}, {"device": "ayn", "pkg": "com.retroarch.aarch64"}]},
    {"id": "openemu", "name": "OpenEmu", "kind": "emu", "sys": "Multi-consoles", "source": ("gh", "OpenEmu/OpenEmu"),
     "targets": [{"device": "mac", "app": "/Applications/OpenEmu.app"}]},
    {"id": "gamenative", "name": "GameNative", "kind": "emu", "sys": "Jeux PC Steam", "source": ("gh", "utkarshdalal/GameNative"),
     "targets": [{"device": "ayn", "pkg": "app.gamenative"}]},
    # Recomps / portages natifs
    {"id": "dusklight", "name": "Dusklight", "kind": "recomp", "sys": "Zelda Twilight Princess", "source": ("gh", "TwilitRealm/dusklight"),
     "targets": [{"device": "mac", "app": str(HOME / "Applications/Dusklight.app")}, {"device": "ayn", "pkg": "dev.twilitrealm.dusk"}]},
    {"id": "wwhd", "name": "Wind Waker HD Recomp", "kind": "recomp", "sys": "Zelda Wind Waker HD", "source": ("gh", "ZeldaWWHDRecomp/ZeldaWWHDRecomp"),
     "targets": [{"device": "mac", "app": str(SSD / "WindWakerHD_Mac/WindWakerHD-v0.5.0-macos-arm64/Wind Waker HD.app")},
                 {"device": "ayn", "pkg": "org.wwhdrecomp.wwhd"}]},
    {"id": "botw-android", "name": "BotW Recomp Android", "kind": "recomp", "sys": "Zelda Breath of the Wild",
     "source": ("gh", "mamcjr/BOTWRecompAndroid"), "allowPre": True,
     "targets": [{"device": "ayn", "pkg": "com.botwrecomp.android"}]},
    {"id": "macnativegames", "name": "MacNativeGames (BotW Mac)", "kind": "recomp", "sys": "Zelda Breath of the Wild",
     "source": ("gh_commits", "ly05010419/MacNativeGames", "main"), "rebuild": True,
     # dépôt multi-jeux : on ne garde que les commits qui parlent de BotW
     "focus": r"\bbotw\b|breath of the wild", "focusStrict": True,
     "targets": [{"device": "mac", "git": str(SSD / "BotW_Recomp_Mac/MacNativeGames")}]},
    {"id": "nxnative", "name": "nxnative (OoT natif Mac)", "kind": "recomp", "sys": "Zelda Ocarina of Time (Switch 2)",
     "source": ("gh_commits", "seedds/nxnative", "main"), "rebuild": True,
     # traducteur générique : commits OoT d'abord, puis améliorations communes à tous les jeux
     "focus": r"ocarina|\boot\b|zelda",
     "targets": [{"device": "mac", "git": str(SSD / "OoT_Native/nxnative")}]},
    {"id": "nx2android", "name": "nx2android", "kind": "recomp", "sys": "Portages Switch → AYN", "source": ("gh", "shirai91/nx2android"),
     "targets": [{"device": "mac", "file": str(SSD / "nx2android/VERSION"), "label": "Outil sur le Mac"}]},
    {"id": "skate3-mac", "name": "Skate 3 Recomp", "kind": "recomp", "sys": "Skate 3", "source": ("gh", "mchughalex/skate3recomp"),
     "targets": [{"device": "mac", "app": str(SSD / "Skate3_Recomp_Mac/Skate 3.app")}]},
    {"id": "skate3-mobile", "name": "Skate3-Mobile", "kind": "recomp", "sys": "Skate 3", "source": ("gh", "Buku313/Skate3-Mobile"),
     "targets": [{"device": "ayn", "pkg": "chat.buku.skate3"}]},
    # Pilote GPU
    {"id": "turnip", "name": "Pilote Turnip (Adreno)", "kind": "driver", "sys": "GPU AYN Thor",
     "source": ("gh", "StevenMXZ/freedreno_turnip-CI"), "nameFilter": r"turnip",
     "targets": [{"device": "ayn", "glob": (SSD / "Drivers_AYN_Thor", r"Turnip_Gen8_V(\d+)\.zip"), "prefix": "v"}]},
]

# Installés mais non suivis (aucune source officielle fiable trouvée → pas de comparaison inventée)
UNTRACKED = [
    ("Cemu Android", "AYN", "version 0.5.2 introuvable dans les releases publiques"),
    ("ARMSX3", "AYN", "version installée 0.3.1 sans correspondance dans le dépôt"),
    ("DuckStation Android", "AYN", "distribué via Play Store, pas de notes GitHub"),
    ("Mupen64Plus FZ", "AYN", "pas de dépôt public de releases"),
    ("melonDualDS", "AYN", "source officielle non identifiée"),
    ("Pegasus", "Mac", "build « continuous » sans numéro de version"),
    ("Lichii", "Mac", "pas de dépôt officiel identifié"),
    ("Ports Zelda3 / Minish Cap / Pokémon", "AYN", "builds communautaires sans releases suivies"),
]

NOISE = re.compile(r"(translat|crowdin|typo|readme|\bci\b|workflow|github action|dependabot|\bbump\b|merge (pull request|branch)|"
                   r"full changelog|submodule|\bchore\b|\blint|clang-format|copyright|\.gitignore|new contributors|made their first)", re.I)
TAGS = {
    "security": r"security|vulnerab|arbitrary code|\bghsa\b|\bcve-",
    "perf": r"perform|faster|speed ?up|\bfps\b|stutter|optimi[sz]|shader cache|frame ?pacing|latency|smoother",
    "fix": r"\bcrash|\bfix|freez|\bhang|regression|black screen|softlock|broken",
    "compat": r"compatib|now boots|playable|support for|supports? |new game|dlc",
    "mac": r"mac ?os|\bmac\b|metal|moltenvk|apple|m[1-4]\b|darwin",
    "android": r"android|adreno|turnip|snapdragon|\bapk\b|bionic",
    "controller": r"controller|gamepad|manette|input|rumble|8bitdo|joystick|button",
    "save": r"\bsaves?\b|save ?state|memory card|sauvegarde",
    "graphics": r"vulkan|render|resolution|upscal|widescreen|\bhdr\b|texture|graphic",
}
TAG_LABEL = {"focus": "Ton jeu", "security": "Sécurité", "perf": "Performances", "fix": "Correctifs", "compat": "Compatibilité", "mac": "Mac",
             "android": "Android", "controller": "Manettes", "save": "Sauvegardes", "graphics": "Graphismes"}


# ─── HTTP ────────────────────────────────────────────────────────────────────
def _token():
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    gh = shutil.which("gh") or "/opt/homebrew/bin/gh"
    try:
        return subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=10).stdout.strip() or None
    except Exception:
        return None


TOKEN = _token()


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "recomp-check-updates", "Accept": "application/json"})
    if TOKEN and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.load(r)


# ─── Versions installées ─────────────────────────────────────────────────────
def adb_serial():
    adb = shutil.which("adb") or str(HOME / ".local/bin/adb")
    try:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return None, None
    devs = [l.split()[0] for l in out.splitlines()[1:] if l.strip().endswith("device")]
    devs.sort(key=lambda s: ":" in s)  # USB d'abord
    return adb, (devs[0] if devs else None)


ADB, SERIAL = adb_serial()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def installed(t):
    """Retourne {version, installedAt} ou None si l'appareil/le volume n'est pas accessible."""
    if "app" in t:
        p = Path(t["app"])
        try:
            info = plistlib.loads((p / "Contents/Info.plist").read_bytes())
        except Exception:
            return None
        v = str(info.get("CFBundleShortVersionString") or info.get("CFBundleVersion") or "").strip()
        return {"version": v, "installedAt": iso(p.stat().st_mtime)} if v else None
    if "pkg" in t:
        if not SERIAL:
            return None
        try:
            out = subprocess.run([ADB, "-s", SERIAL, "shell", "dumpsys", "package", t["pkg"]],
                                 capture_output=True, text=True, timeout=20).stdout
        except Exception:
            return None
        v = re.search(r"versionName=(.+)", out)
        u = re.search(r"lastUpdateTime=([\d-]+ [\d:]+)", out)
        if not v:
            return None
        at = datetime.strptime(u.group(1), "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc).isoformat(timespec="seconds") if u else None
        return {"version": v.group(1).strip(), "installedAt": at}
    if "git" in t:
        g = Path(t["git"])
        if not (g / ".git").exists():
            return None
        out = subprocess.run(["git", "-C", str(g), "log", "-1", "--format=%h|%cI"], capture_output=True, text=True).stdout.strip()
        if not out:
            return None
        h, d = out.split("|")
        return {"version": h, "installedAt": d}
    if "file" in t:
        p = Path(t["file"])
        if not p.exists():
            return None
        return {"version": p.read_text().strip(), "installedAt": iso(p.stat().st_mtime)}
    if "glob" in t:
        folder, rx = t["glob"]
        if not folder.exists():
            return None
        best = None
        for f in folder.iterdir():
            m = re.fullmatch(rx, f.name)
            if m:
                key = tuple(int(x) for x in re.findall(r"\d+", m.group(1)))
                if not best or key > best[0]:
                    best = (key, m.group(1), f)
        if not best:
            return None
        return {"version": t.get("prefix", "") + best[1], "installedAt": iso(best[2].stat().st_mtime)}
    return None


# ─── Releases ────────────────────────────────────────────────────────────────
def fetch_releases(tool):
    kind, src = tool["source"][0], tool["source"][1]
    rels = []
    if kind == "gh":
        data = get_json(f"https://api.github.com/repos/{src}/releases?per_page={tool.get('pages', 30)}")
        for r in data:
            if r.get("draft"):
                continue
            rels.append({"tag": r["tag_name"], "name": r.get("name") or r["tag_name"], "date": r.get("published_at") or r.get("created_at"),
                         "pre": bool(r.get("prerelease")), "url": r["html_url"], "body": r.get("body") or ""})
    elif kind == "forgejo":
        data = get_json(f"{src}/releases?limit=20")
        for r in data:
            if r.get("draft"):
                continue
            rels.append({"tag": r["tag_name"], "name": r.get("name") or r["tag_name"], "date": r.get("published_at") or r.get("created_at"),
                         "pre": bool(r.get("prerelease")), "url": r["html_url"], "body": r.get("body") or ""})
    elif kind == "gh_tags":
        tags = [t for t in get_json(f"https://api.github.com/repos/{src}/tags?per_page=40") if re.fullmatch(r"\d{4}[a-z]?", t["name"])]
        tags.sort(key=lambda t: (int(t["name"][:4]), t["name"][4:]), reverse=True)
        for t in tags[:8]:
            c = get_json(t["commit"]["url"])
            rels.append({"tag": t["name"], "name": f"Dolphin {t['name']}", "date": c["commit"]["committer"]["date"], "pre": False,
                         "url": f"https://github.com/{src}/releases/tag/{t['name']}", "body": ""})
    if kind == "gh" and rels and all(r["pre"] for r in rels) and not tool.get("allowPre"):
        try:  # dépôts à nightlies (PCSX2) : la dernière stable n'est pas dans la 1re page
            r = get_json(f"https://api.github.com/repos/{src}/releases/latest")
            rels.append({"tag": r["tag_name"], "name": r.get("name") or r["tag_name"], "date": r.get("published_at"),
                         "pre": False, "url": r["html_url"], "body": r.get("body") or ""})
        except Exception:
            pass
    if tool.get("nameFilter"):
        rels = [r for r in rels if re.search(tool["nameFilter"], r["name"], re.I)]
    rels.sort(key=lambda r: r["date"] or "", reverse=True)
    return rels


def vnorm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"\s*\(.*?\)", "", s)
    s = re.sub(r"^(v(ersion)?\s*|xendroid-|build-)", "", s)
    s = re.sub(r"-g[0-9a-f]{7,}$", "", s)
    s = re.sub(r"[-_ ](vanilla|git|free|stable|release).*$", "", s)
    parts = s.split(".")
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


def clean_md(line):
    line = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
    line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
    line = re.sub(r"<[^>]+>", "", line)
    line = re.sub(r"https?://\S+", "", line)
    line = re.sub(r"[*_`#>]+", "", line)
    line = re.sub(r":[a-z0-9_+-]+:", "", line)  # émojis Forgejo :ryubing:
    line = re.sub(r"\s*\|\s*@.*$", "", line)  # « | @auteur 1234 » (Azahar)
    return re.sub(r"\s+", " ", line).strip(" -–•:")


def tags_of(text):
    return [k for k, rx in TAGS.items() if re.search(rx, text, re.I)]


def highlights(body, limit=8):
    """Extrait les puces réelles des notes de version (verbatim, nettoyées), les plus pertinentes d'abord."""
    lines = body.replace("\r", "").split("\n")
    bullets = [clean_md(l) for l in lines if re.match(r"^\s*([-*•+]|\d+[.)])\s+", l)]
    if not bullets:
        bullets = [clean_md(l) for l in lines if l.strip() and not l.lstrip().startswith("#")][:4]
    out, seen = [], set()
    for b in bullets:
        b = re.sub(r"^[0-9a-f]{7,12}\s+", "", b)  # hash de commit DuckStation
        if len(b) < 8 or NOISE.search(b) or b.lower() in seen:
            continue
        seen.add(b.lower())
        out.append({"text": b[:200] + ("…" if len(b) > 200 else ""), "tags": tags_of(b)})
    out.sort(key=lambda h: -len(h["tags"]))  # tri stable : pertinents d'abord, ordre d'origine sinon
    return out[:limit], len(out)


def compare_commits(repo, branch, inst, focus=None, strict=False):
    data = get_json(f"https://api.github.com/repos/{repo}/compare/{inst['version']}...{branch}")
    commits = data.get("commits", [])[::-1]
    items, seen = [], set()
    for c in commits:
        msg = c["commit"]["message"].split("\n")[0].strip()
        if NOISE.search(msg) or msg.lower() in seen or msg.lower().startswith("merge"):
            continue
        seen.add(msg.lower())
        tg = tags_of(msg)
        if focus and re.search(focus, msg, re.I):
            tg.insert(0, "focus")
        elif focus and strict:
            continue
        items.append({"text": msg[:200] + ("…" if len(msg) > 200 else ""), "tags": tg})
    items.sort(key=lambda h: ("focus" not in h["tags"], -len(h["tags"])))
    latest = commits[0] if commits else None
    return {
        "aheadBy": len(items) if strict else data.get("ahead_by", 0),
        "totalAhead": data.get("ahead_by", 0),
        "latest": latest["sha"][:7] if latest else inst["version"],
        "latestDate": latest["commit"]["committer"]["date"] if latest else None,
        "url": data.get("html_url") or f"https://github.com/{repo}/commits/{branch}",
        "items": items[:10],
        "count": len(items),
    }


def decide(tool, device, newer):
    """Recommandation transparente, uniquement à partir des étiquettes trouvées dans les notes."""
    if not newer:
        return "uptodate", "Tu as la dernière version."
    tags = set()
    for r in newer:
        for h in r.get("highlights", []):
            tags.update(h["tags"])
    rel_dev = "mac" if device == "mac" else "android"
    reasons = [TAG_LABEL[k] for k in ("security", "perf", "fix", "compat", "graphics", "controller", "save") if k in tags]
    if rel_dev in tags:
        reasons.insert(0, f"spécifique {TAG_LABEL[rel_dev]}")
    n = len(newer)
    head = f"{n} nouvelle{'s' if n > 1 else ''} version{'s' if n > 1 else ''}"
    important = tags & {"focus", "security", "perf", "fix", "compat", rel_dev} or tool["kind"] in ("recomp", "driver")
    level = "recommended" if important else "optional"
    return level, head + (" · " + ", ".join(reasons[:4]) if reasons else "")


# ─── Build ───────────────────────────────────────────────────────────────────
def main():
    prev = {}
    if (OUT / "updates.json").exists():
        try:
            for t in json.loads((OUT / "updates.json").read_text())["tools"]:
                for tg in t["targets"]:
                    prev[(t["id"], tg["device"])] = tg.get("installed")
        except Exception:
            pass

    tools_out, errors = [], []
    for tool in TOOLS:
        try:
            rels = None if tool["source"][0] == "gh_commits" else fetch_releases(tool)
        except Exception as e:
            errors.append(f"{tool['name']}: {e}")
            continue
        targets = []
        for t in tool["targets"]:
            inst = installed(t)
            live = inst is not None
            if not live:
                inst = prev.get((tool["id"], t["device"]))
            if not inst:
                continue
            if live:
                inst["seenAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            row = {"device": t["device"], "label": t.get("label"), "installed": inst, "live": live}

            if tool["source"][0] == "gh_commits":
                try:
                    c = compare_commits(tool["source"][1], tool["source"][2], inst, tool.get("focus"), tool.get("focusStrict"))
                except Exception as e:
                    errors.append(f"{tool['name']}: {e}")
                    continue
                row.update({"latest": c["latest"], "latestDate": c["latestDate"], "basis": "commit", "newerPre": None,
                            "releases": [{"tag": f"{c['aheadBy']} commits", "name": "Nouveaux commits sur main", "date": c["latestDate"],
                                          "pre": False, "url": c["url"], "highlights": c["items"], "more": max(0, c["count"] - len(c["items"]))}]
                            if c["aheadBy"] else []})
                row["status"], row["reason"] = decide(tool, t["device"], row["releases"])
                if not c["aheadBy"] and c["totalAhead"]:
                    row["reason"] = f"{c['totalAhead']} commits, aucun ne concerne ton jeu."
                if c["aheadBy"]:
                    row["reason"] = f"{c['aheadBy']} commits {'sur ton jeu ' if tool.get('focusStrict') else ''}depuis ta compilation" + (" · " + row["reason"].split(" · ", 1)[1] if " · " in row["reason"] else "")
                targets.append(row)
                continue

            allow_pre = tool.get("allowPre") or (rels and all(r["pre"] for r in rels))
            pool = [r for r in rels if allow_pre or not r["pre"]]
            iv = vnorm(inst["version"])
            match = next((r for r in rels if vnorm(r["tag"]) == iv or (len(iv) >= 6 and vnorm(r["tag"]).endswith(iv))), None)
            if match:
                basis = "version"
                newer = [r for r in pool if (r["date"] or "") > (match["date"] or "") and r is not match]
                newer_pre = next((r for r in rels if r["pre"] and not allow_pre and (r["date"] or "") > (match["date"] or "")), None)
            else:
                basis = "date"
                cut = inst.get("installedAt") or ""
                newer = [r for r in pool if (r["date"] or "") > cut]
                newer_pre = next((r for r in rels if r["pre"] and not allow_pre and (r["date"] or "") > cut), None)
            latest = pool[0] if pool else None
            rel_out = []
            for r in newer[:6]:
                h, total = highlights(r["body"])
                rel_out.append({"tag": r["tag"], "name": r["name"], "date": r["date"], "pre": r["pre"], "url": r["url"],
                                "highlights": h, "more": max(0, total - len(h))})
            row.update({"latest": latest["tag"] if latest else None, "latestDate": latest["date"] if latest else None,
                        "basis": basis, "releases": rel_out, "olderHidden": max(0, len(newer) - 6),
                        "newerPre": {"tag": newer_pre["tag"], "date": newer_pre["date"], "url": newer_pre["url"]} if newer_pre else None})
            row["status"], row["reason"] = decide(tool, t["device"], rel_out)
            if row["olderHidden"]:
                row["reason"] = row["reason"].replace(f"{len(rel_out)} nouvelle", f"{len(newer)} nouvelle", 1)
            targets.append(row)
        if targets:
            src = tool["source"]
            repo_url = (src[1].replace("/api/v1/repos", "") if src[0] == "forgejo" else f"https://github.com/{src[1]}")
            tools_out.append({"id": tool["id"], "name": tool["name"], "kind": tool["kind"], "sys": tool["sys"], "repo": repo_url,
                              "notesUrl": tool.get("notesUrl"), "rebuild": bool(tool.get("rebuild")), "targets": targets})

    order = {"recommended": 0, "optional": 1, "uptodate": 2}
    tools_out.sort(key=lambda t: (min(order[x["status"]] for x in t["targets"]), t["name"].lower()))
    stats = {k: sum(1 for t in tools_out for x in t["targets"] if x["status"] == k) for k in order}
    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "aynConnected": bool(SERIAL),
        "stats": stats,
        "tagLabels": TAG_LABEL,
        "tools": tools_out,
        "untracked": [{"name": n, "device": d, "why": w} for n, d, w in UNTRACKED],
        "errors": errors,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "updates.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (OUT / "updates_embedded.js").write_text("window.RECOMP_UPDATES = " + json.dumps(out, ensure_ascii=False) + ";\n")
    print(f"{len(tools_out)} outils · {stats} · AYN {'connectée' if SERIAL else 'absente'}")
    for e in errors:
        print("  ⚠", e)


if __name__ == "__main__":
    main()
