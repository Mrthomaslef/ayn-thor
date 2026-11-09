#!/usr/bin/env python3
"""Génère recomp/data/recomp.json + recomp/data/embedded_data.js
(liste des Recomp / Portages natifs compatibles AYN Thor & Mac mini M4).

Sources (déjà dans le repo) :
- data/nxbrew_catalog.json   -> jeux Switch avec champ "recomp" (nxnative / nx2android)
- data/nx2android_profiles.json -> statut nx2android Android (playable / wip / ...)
- data/romsfun_catalog.json  -> recompMap des consoles rétro (N64, SNES, GBA, PS1, ...)
"""
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "recomp" / "data"

SYSTEM_LABELS = {
    "switch": "Switch", "n3ds": "3DS", "nds": "NDS", "gc": "GameCube", "wii": "Wii",
    "wiiu": "Wii U", "ps2": "PS2", "psx": "PS1", "psp": "PSP", "gba": "GBA", "n64": "N64",
    "snes": "SNES", "gbc": "GBC", "gb": "Game Boy", "nes": "NES", "xbox360": "Xbox 360",
    "ps3": "PS3",
}
# Ordre d'affichage des plateformes d'origine
SYSTEM_ORDER = ["switch", "wiiu", "wii", "gc", "n64", "n3ds", "nds", "snes", "gba", "gb",
                "ps3", "xbox360", "ps2", "psx", "psp"]


# Noms canoniques des portages rétro (clé = début de m4Tool), évite les titres de rom-hacks
CANON = {
    "Zelda3": "The Legend of Zelda: A Link to the Past",
    "pokeemerald": "Pokémon Émeraude",
    "pokered": "Pokémon Rouge / Bleu",
    "Skate 3": "Skate 3",
    "BotW": "The Legend of Zelda: Breath of the Wild",
    "tmc-decomp": "The Legend of Zelda: The Minish Cap",
    "Wind Waker": "The Legend of Zelda: The Wind Waker HD",
    "sotn-decomp": "Castlevania: Symphony of the Night",
    "re3": "Grand Theft Auto III",
    "reVC": "Grand Theft Auto: Vice City",
    "Starship": "Star Fox 64",
    "SpaghettiKart": "Mario Kart 64",
    "Ship of Harkinian": "The Legend of Zelda: Ocarina of Time",
    "perfect_dark": "Perfect Dark",
    "sm64ex": "Super Mario 64",
    "sm ": "Super Metroid",
    "2Ship2Harkinian": "The Legend of Zelda: Majora's Mask",
    "wipeout": "WipEout",
    "Dusklight": "The Legend of Zelda: Twilight Princess",
    "CTR": "Crash Team Racing",
    "OpenGOAL Jak II": "Jak II",
    "OpenGOAL": "Jak and Daxter: The Precursor Legacy",
    "papermario": "Paper Mario",
    "UnleashedRecomp": "Sonic Unleashed",
    "BanjoRecomp": "Banjo-Kazooie",
}


# Jaquettes des portages rétro (libretro-thumbnails Named_Boxarts, sinon Wikipédia)
LR = "https://thumbnails.libretro.com/"
COVERS = {
    "The Legend of Zelda: A Link to the Past": ("Nintendo - Super Nintendo Entertainment System", "Legend of Zelda, The - A Link to the Past (USA)"),
    "Pokémon Émeraude": ("Nintendo - Game Boy Advance", "Pokemon - Emerald Version (USA, Europe)"),
    "Pokémon Rouge / Bleu": ("Nintendo - Game Boy", "Pokemon - Red Version (USA, Europe) (SGB Enhanced)"),
    "Skate 3": "https://upload.wikimedia.org/wikipedia/en/8/84/Skate-3-Boxart.jpg",
    "The Legend of Zelda: Breath of the Wild": ("Nintendo - Wii U", "Legend of Zelda, The - Breath of the Wild (Europe) (En,Fr,De,Es,It,Nl,Ru)"),
    "The Legend of Zelda: The Minish Cap": ("Nintendo - Game Boy Advance", "Legend of Zelda, The - The Minish Cap (USA)"),
    "The Legend of Zelda: The Wind Waker HD": ("Nintendo - Wii U", "Legend of Zelda, The - The Wind Waker HD (Europe, Australia) (En,Fr,De,Es,It)"),
    "Castlevania: Symphony of the Night": ("Sony - PlayStation", "Castlevania - Symphony of the Night (USA)"),
    "Grand Theft Auto III": ("Sony - PlayStation 2", "Grand Theft Auto III (USA)"),
    "Grand Theft Auto: Vice City": ("Sony - PlayStation 2", "Grand Theft Auto - Vice City (USA) (v3.00)"),
    "Star Fox 64": ("Nintendo - Nintendo 64", "Star Fox 64 (USA)"),
    "Mario Kart 64": ("Nintendo - Nintendo 64", "Mario Kart 64 (USA)"),
    "The Legend of Zelda: Ocarina of Time": ("Nintendo - Nintendo 64", "Legend of Zelda, The - Ocarina of Time (USA)"),
    "Perfect Dark": ("Nintendo - Nintendo 64", "Perfect Dark (USA)"),
    "Super Mario 64": ("Nintendo - Nintendo 64", "Super Mario 64 (USA)"),
    "Super Metroid": ("Nintendo - Super Nintendo Entertainment System", "Super Metroid (Japan, USA) (En,Ja)"),
    "The Legend of Zelda: Majora's Mask": ("Nintendo - Nintendo 64", "Legend of Zelda, The - Majora's Mask (USA)"),
    "WipEout": ("Sony - PlayStation", "WipEout (Europe)"),
    "The Legend of Zelda: Twilight Princess": ("Nintendo - Wii", "Legend of Zelda, The - Twilight Princess (Europe, Australia) (En,Fr,De,Es,It)"),
    "Crash Team Racing": ("Sony - PlayStation", "CTR - Crash Team Racing (USA)"),
    "Jak II": ("Sony - PlayStation 2", "Jak II (USA) (En,Ja,Fr,De,Es,It,Ko) (v1.00)"),
    "Jak and Daxter: The Precursor Legacy": ("Sony - PlayStation 2", "Jak and Daxter - The Precursor Legacy (USA) (En,Fr,De,Es,It)"),
    "Paper Mario": ("Nintendo - Nintendo 64", "Paper Mario (USA)"),
    "Sonic Unleashed": "https://upload.wikimedia.org/wikipedia/en/a/a0/Sonic_unleashed_boxart.jpg",
    "Banjo-Kazooie": ("Nintendo - Nintendo 64", "Banjo-Kazooie (USA)"),
}


def retro_cover(title: str):
    c = COVERS.get(title)
    if isinstance(c, tuple):
        q = lambda x: urllib.parse.quote(x, safe="")
        return f"{LR}{q(c[0])}/Named_Boxarts/{q(c[1])}.png"
    return c


def eshop_cover(title: str):
    """Jaquette Switch via la recherche eShop Nintendo Europe (jeux absents du catalogue nxbrew)."""
    url = ("https://searching.nintendo-europe.com/fr/select?wt=json&rows=3&fq=type:GAME&q="
           + urllib.parse.quote(title))
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            docs = json.load(r)["response"]["docs"]
    except Exception:
        return None
    key = norm(re.sub(r"\(.*?\)", "", title))
    for d in docs:
        if norm(d.get("title")) == key or norm(d.get("title")).startswith(key):
            return d.get("image_url_sq_s") or d.get("image_url")
    return None


def canon_title(m4tool: str):
    for prefix in sorted(CANON, key=len, reverse=True):
        if (m4tool or "").startswith(prefix):
            return CANON[prefix]
    return None


def norm(s: str) -> str:
    s = (s or "").lower().replace("™", "").replace("®", "")
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def clean_title(t: str) -> str:
    return re.sub(r"[™®]", "", t or "").strip()


def build_switch():
    cat = json.loads((DATA / "nxbrew_catalog.json").read_text())
    profiles = json.loads((DATA / "nx2android_profiles.json").read_text())
    prof_by_name = {norm(p["name"]): p for p in profiles}
    seen = {}
    for g in cat["games"]:
        rc = g.get("recomp")
        if not rc:
            continue
        title = clean_title(g.get("shortName") or g["title"])
        key = norm(title)
        if key in seen:
            continue
        prof = prof_by_name.pop(key, None)
        seen[key] = {
            "id": f"switch/{key.replace(' ', '-')}",
            "title": title,
            "systems": ["switch"],
            "m4": bool(rc.get("m4")),
            "ayn": bool(rc.get("ayn")) or bool(prof),
            "m4Tool": rc.get("m4Tool"),
            "aynTool": rc.get("aynTool") or (f"nx2android ({prof['package']})" if prof else None),
            "aynStatus": prof["status"] if prof else ("playable" if rc.get("ayn") else None),
            "note": rc.get("note"),
            "installed": False,
            "cover": f"https://img-eshop.cdn.nintendo.net/i/{g['icon']}.jpg" if g.get("icon") else None,
        }
    # Profils nx2android non présents dans le catalogue nxbrew
    for p in prof_by_name.values():
        key = norm(p["name"])
        seen[key] = {
            "id": f"switch/{key.replace(' ', '-')}",
            "title": clean_title(p["name"]),
            "systems": ["switch"],
            "m4": False,
            "ayn": True,
            "m4Tool": None,
            "aynTool": f"nx2android ({p['package']})",
            "aynStatus": p["status"],
            "note": f"Testé : {p.get('tested', '')}".strip(),
            "installed": False,
            "cover": None,
        }
    return list(seen.values())


def build_retro():
    cat = json.loads((DATA / "romsfun_catalog.json").read_text())
    titles = {}
    for sys_id, games in cat.get("systems", {}).items():
        for item in games:
            if isinstance(item, list) and len(item) >= 2:
                titles[f"{sys_id}/{item[1]}"] = item[0]
    # Regroupe les variantes (régions, versions 3DS/N64...) par outil
    groups = {}
    for key, rc in cat.get("recompMap", {}).items():
        sys_id, slug = key.split("/", 1)
        title = clean_title(titles.get(key) or slug.replace("-", " ").title())
        gkey = (rc.get("m4Tool"), rc.get("aynTool"))
        g = groups.setdefault(gkey, {
            "id": key, "titles": [], "systems": [],
            "m4": bool(rc.get("m4")), "ayn": bool(rc.get("ayn")),
            "m4Tool": rc.get("m4Tool"), "aynTool": rc.get("aynTool"),
            "aynStatus": "playable" if rc.get("ayn") else None,
            "note": rc.get("note"), "installed": False, "cover": None,
        })
        g["titles"].append(title)
        if sys_id not in g["systems"]:
            g["systems"].append(sys_id)
        g["installed"] = g["installed"] or bool(rc.get("installedBoth"))
    out = []
    for g in groups.values():
        ts = sorted(set(g.pop("titles")), key=lambda t: (len(t), t))
        g["title"] = canon_title(g["m4Tool"]) or ts[0]
        g["cover"] = retro_cover(g["title"])
        g["systems"].sort(key=lambda s: SYSTEM_ORDER.index(s) if s in SYSTEM_ORDER else 99)
        out.append(g)
    return out


def main():
    games = build_switch() + build_retro()
    for g in games:
        if not g["cover"] and g["systems"] == ["switch"]:
            g["cover"] = eshop_cover(g["title"])
    games.sort(key=lambda g: (not (g["m4"] and g["ayn"]), not g["installed"], g["title"].lower()))
    stats = {
        "total": len(games),
        "both": sum(g["m4"] and g["ayn"] for g in games),
        "ayn": sum(g["ayn"] for g in games),
        "m4": sum(g["m4"] for g in games),
        "installed": sum(g["installed"] for g in games),
    }
    systems = sorted({s for g in games for s in g["systems"]},
                     key=lambda s: SYSTEM_ORDER.index(s) if s in SYSTEM_ORDER else 99)
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hardware": {
            "ayn": "AYN Thor — Snapdragon 8 Gen 2 · Adreno 740 · Android (Turnip)",
            "m4": "Mac mini M4 — Apple Silicon ARM64 · Metal",
        },
        "systemLabels": {s: SYSTEM_LABELS.get(s, s.upper()) for s in systems},
        "systems": systems,
        "stats": stats,
        "games": games,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    txt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    (OUT / "recomp.json").write_text(txt)
    (OUT / "embedded_data.js").write_text(f"window.RECOMP_DATA = {txt};\n")
    print(f"✓ {stats['total']} jeux · {stats['both']} Mac+AYN · {stats['ayn']} AYN · "
          f"{stats['m4']} Mac M4 · {stats['installed']} installés")


if __name__ == "__main__":
    main()
