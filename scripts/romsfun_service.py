#!/Users/thomaslefevre/.local/share/uv/tools/nxbrew-dl/bin/python
"""
Moteur Multi-Consoles Direct Downloader (RomsFun CDN + Archive.org + TorBox) :
1. Récupère et parse une fiche de jeu sur romsfun.com + miroirs Archive.org (sans Captcha ni JDownloader).
2. Résout le lien CDN signé RomsFun (action=k_get_download -> sto.romsfast.com / statics.romsfun.com)
   ou les miroirs directs Archive.org / TorBox.
3. Télécharge avec suivi de progression en temps réel dans data/nxbrew_jobs/.
4. Extrait automatiquement les archives (.zip / .7z / .rar) si nécessaire selon les extensions attendues
   par l'émulateur de la console (ex: .3ds/.cci, .nds, .rvz/.iso, .chd/.cue/.bin, .wux/.wua, .gba, .z64, .sfc)
   puis installe sur Mac mini M4 (ES-DE gamelist + jaquette + Pegasus Frontend + M4 Gaming) ET AYN Thor (ADB push ou file d'attente).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT_DIR, "data")
ROMS_ROOT = "/Volumes/GamingSSD/Games/ROMs"
SEVEN_ZIP_BIN = os.path.expanduser("~/.local/bin/7zz")
T3_VAULT = os.path.expanduser("~/.t3/bin/t3-vault")
TORBOX_API = "https://api.torbox.app/v1/api"

# Configuration complète des 20 consoles hors Switch
CONSOLE_CONFIG = {
    "n3ds": {
        "id": "n3ds",
        "label": "Nintendo 3DS",
        "shortLabel": "3DS",
        "icon": "🕹️",
        "romsfunSlug": "3ds",
        "macDir": "3DS",
        "gamelistKey": "n3ds",
        "aynDir": "/storage/6531-3035/ROMs/n3ds",
        "macEmulator": "Azahar (1080p Metal)",
        "aynEmulator": "Azahar / Citra Dusk (Turnip V39 · Double Écran)",
        "exts": [".3ds", ".cci", ".cxi", ".3dsx", ".zcci", ".cia"],
        "keepZip": False,
        "archiveKeywords": "3ds decrypted",
    },
    "nds": {
        "id": "nds",
        "label": "Nintendo DS",
        "shortLabel": "NDS",
        "icon": "📱",
        "romsfunSlug": "nintendo-ds",
        "macDir": "NDS",
        "gamelistKey": "nds",
        "aynDir": "/storage/6531-3035/ROMs/nds",
        "macEmulator": "RetroArch (melonDS DS)",
        "aynEmulator": "melonDS Dual-Screen",
        "exts": [".nds", ".dsi"],
        "keepZip": False,
        "archiveKeywords": "nintendo ds",
    },
    "gc": {
        "id": "gc",
        "label": "Nintendo GameCube",
        "shortLabel": "GameCube",
        "icon": "🟣",
        "romsfunSlug": "gamecube",
        "macDir": "GameCube",
        "gamelistKey": "gc",
        "aynDir": "/storage/6531-3035/ROMs/gc",
        "macEmulator": "Dolphin (Metal 1080p)",
        "aynEmulator": "Dolphin Android (Vulkan 3x)",
        "exts": [".rvz", ".iso", ".gcm", ".gcz", ".ciso", ".wia"],
        "keepZip": False,
        "archiveKeywords": "gamecube rvz iso",
    },
    "wii": {
        "id": "wii",
        "label": "Nintendo Wii",
        "shortLabel": "Wii",
        "icon": "🐬",
        "romsfunSlug": "nintendo-wii",
        "macDir": "Wii",
        "gamelistKey": "wii",
        "aynDir": "/storage/6531-3035/ROMs/wii",
        "macEmulator": "Dolphin (Metal 1080p)",
        "aynEmulator": "Dolphin Android (Vulkan)",
        "exts": [".rvz", ".wbfs", ".iso", ".wia", ".gcz", ".ciso", ".wad"],
        "keepZip": False,
        "archiveKeywords": "wii rvz wbfs",
    },
    "wiiu": {
        "id": "wiiu",
        "label": "Nintendo Wii U",
        "shortLabel": "Wii U",
        "icon": "🟦",
        "romsfunSlug": "wii-u",
        "macDir": "WiiU",
        "gamelistKey": "wiiu",
        "aynDir": "/storage/6531-3035/ROMs/wiiu",
        "macEmulator": "Cemu (Metal 1080p)",
        "aynEmulator": "Cemu Android (Turnip V39)",
        "exts": [".wua", ".wux", ".wud", ".wuhb", ".rpx"],
        "keepZip": False,
        "archiveKeywords": "wii u wua wux",
    },
    "ps2": {
        "id": "ps2",
        "label": "Sony PlayStation 2",
        "shortLabel": "PS2",
        "icon": "💿",
        "romsfunSlug": "playstation-2",
        "macDir": "ps2",
        "gamelistKey": "ps2",
        "aynDir": "/storage/6531-3035/ROMs/ps2",
        "macEmulator": "PCSX2 v2.8.2 (Metal)",
        "aynEmulator": "NetherSX2 / AetherSX2 Turnip",
        "exts": [".chd", ".iso", ".cso", ".zso", ".gz", ".bin"],
        "keepZip": False,
        "archiveKeywords": "ps2 iso chd",
    },
    "psx": {
        "id": "psx",
        "label": "Sony PlayStation 1",
        "shortLabel": "PS1",
        "icon": "🦆",
        "romsfunSlug": "playstation",
        "macDir": "PS1",
        "gamelistKey": "psx",
        "aynDir": "/storage/6531-3035/ROMs/psx",
        "macEmulator": "DuckStation (Metal 1080p)",
        "aynEmulator": "DuckStation Android",
        "exts": [".chd", ".cue", ".pbp", ".iso", ".bin", ".m3u"],
        "keepZip": False,
        "archiveKeywords": "psx chd bin cue",
    },
    "psp": {
        "id": "psp",
        "label": "PlayStation Portable",
        "shortLabel": "PSP",
        "icon": "🛸",
        "romsfunSlug": "playstation-portable",
        "macDir": "PSP",
        "gamelistKey": "psp",
        "aynDir": "/storage/6531-3035/ROMs/psp",
        "macEmulator": "PPSSPP (Metal 1080p)",
        "aynEmulator": "PPSSPP Android (Vulkan 4x)",
        "exts": [".iso", ".cso", ".chd", ".pbp"],
        "keepZip": False,
        "archiveKeywords": "psp iso",
    },
    "gba": {
        "id": "gba",
        "label": "Game Boy Advance",
        "shortLabel": "GBA",
        "icon": "👾",
        "romsfunSlug": "game-boy-advance",
        "macDir": "GBA",
        "gamelistKey": "gba",
        "aynDir": "/storage/6531-3035/ROMs/gba",
        "macEmulator": "RetroArch (mGBA)",
        "aynEmulator": "RetroArch (mGBA)",
        "exts": [".gba", ".agb"],
        "keepZip": False,
        "archiveKeywords": "gba",
    },
    "n64": {
        "id": "n64",
        "label": "Nintendo 64",
        "shortLabel": "N64",
        "icon": "🦖",
        "romsfunSlug": "nintendo-64",
        "macDir": "N64",
        "gamelistKey": "n64",
        "aynDir": "/storage/6531-3035/ROMs/n64",
        "macEmulator": "RetroArch (Mupen64Plus-Next)",
        "aynEmulator": "RetroArch (Mupen64Plus-Next)",
        "exts": [".z64", ".n64", ".v64"],
        "keepZip": False,
        "archiveKeywords": "nintendo 64 z64",
    },
    "snes": {
        "id": "snes",
        "label": "Super Nintendo",
        "shortLabel": "SNES",
        "icon": "🎛️",
        "romsfunSlug": "super-nintendo",
        "macDir": "SNES",
        "gamelistKey": "snes",
        "aynDir": "/storage/6531-3035/ROMs/snes",
        "macEmulator": "RetroArch (Snes9x)",
        "aynEmulator": "RetroArch (Snes9x)",
        "exts": [".sfc", ".smc"],
        "keepZip": False,
        "archiveKeywords": "snes sfc",
    },
    "gbc": {
        "id": "gbc",
        "label": "Game Boy Color",
        "shortLabel": "GBC",
        "icon": "🟢",
        "romsfunSlug": "game-boy-color",
        "macDir": "GBC",
        "gamelistKey": "gbc",
        "aynDir": "/storage/6531-3035/ROMs/gbc",
        "macEmulator": "RetroArch (Gambatte)",
        "aynEmulator": "RetroArch (Gambatte)",
        "exts": [".gbc"],
        "keepZip": False,
        "archiveKeywords": "gbc",
    },
    "gb": {
        "id": "gb",
        "label": "Game Boy",
        "shortLabel": "GB",
        "icon": "⚪",
        "romsfunSlug": "game-boy",
        "macDir": "GB",
        "gamelistKey": "gb",
        "aynDir": "/storage/6531-3035/ROMs/gb",
        "macEmulator": "RetroArch (Gambatte)",
        "aynEmulator": "RetroArch (Gambatte)",
        "exts": [".gb"],
        "keepZip": False,
        "archiveKeywords": "game boy gb",
    },
    "nes": {
        "id": "nes",
        "label": "Nintendo NES",
        "shortLabel": "NES",
        "icon": "🧱",
        "romsfunSlug": "nes",
        "macDir": "NES",
        "gamelistKey": "nes",
        "aynDir": "/storage/6531-3035/ROMs/nes",
        "macEmulator": "RetroArch (Mesen)",
        "aynEmulator": "RetroArch (Mesen)",
        "exts": [".nes", ".fds"],
        "keepZip": False,
        "archiveKeywords": "nes",
    },
    "genesis": {
        "id": "genesis",
        "label": "Sega Mega Drive / Genesis",
        "shortLabel": "Mega Drive",
        "icon": "🦔",
        "romsfunSlug": "sega-genesis",
        "macDir": "genesis",
        "gamelistKey": "genesis",
        "aynDir": "/storage/6531-3035/ROMs/genesis",
        "macEmulator": "OpenEmu / RetroArch",
        "aynEmulator": "RetroArch (Genesis Plus GX)",
        "exts": [".md", ".gen", ".bin", ".smd"],
        "keepZip": False,
        "archiveKeywords": "sega genesis",
    },
    "dreamcast": {
        "id": "dreamcast",
        "label": "Sega Dreamcast",
        "shortLabel": "Dreamcast",
        "icon": "🌀",
        "romsfunSlug": "dreamcast",
        "macDir": "dreamcast",
        "gamelistKey": "dreamcast",
        "aynDir": "/storage/6531-3035/ROMs/dreamcast",
        "macEmulator": "Flycast Mac",
        "aynEmulator": "Flycast / RetroArch",
        "exts": [".chd", ".gdi", ".cdi", ".cue", ".bin"],
        "keepZip": False,
        "archiveKeywords": "dreamcast chd gdi",
    },
    "saturn": {
        "id": "saturn",
        "label": "Sega Saturn",
        "shortLabel": "Saturn",
        "icon": "🪐",
        "romsfunSlug": "sega-saturn",
        "macDir": "saturn",
        "gamelistKey": "saturn",
        "aynDir": "/storage/6531-3035/ROMs/saturn",
        "macEmulator": "OpenEmu / Mednafen",
        "aynEmulator": "YabaSanshiro 2 / Beetle Saturn",
        "exts": [".chd", ".cue", ".iso", ".bin", ".mdf"],
        "keepZip": False,
        "archiveKeywords": "sega saturn chd cue",
    },
    "psvita": {
        "id": "psvita",
        "label": "PlayStation Vita",
        "shortLabel": "PS Vita",
        "icon": "💎",
        "romsfunSlug": "ps-vita",
        "macDir": "psvita",
        "gamelistKey": "psvita",
        "aynDir": "/storage/6531-3035/ROMs/psvita",
        "macEmulator": "Vita3K Mac",
        "aynEmulator": "Vita3K Android (Turnip V39)",
        "exts": [".vpk", ".zip"],
        "keepZip": True,
        "archiveKeywords": "ps vita vpk nonpdrm",
    },
    "xbox360": {
        "id": "xbox360",
        "label": "Microsoft Xbox 360",
        "shortLabel": "Xbox 360",
        "icon": "🟢",
        "romsfunSlug": "xbox-360",
        "macDir": "xbox360",
        "gamelistKey": "xbox360",
        "aynDir": "/storage/6531-3035/ROMs/xbox360",
        "macEmulator": "Recomp ARM64 / Xenia",
        "aynEmulator": "XenDroid / Xenia Canary (Turnip V39)",
        "exts": [".iso", ".xex", ".zar"],
        "keepZip": False,
        "archiveKeywords": "xbox 360 iso",
    },
    "ps3": {
        "id": "ps3",
        "label": "Sony PlayStation 3",
        "shortLabel": "PS3",
        "icon": "🖤",
        "romsfunSlug": "playstation-3",
        "macDir": "ps3",
        "gamelistKey": "ps3",
        "aynDir": "/storage/6531-3035/ROMs/ps3",
        "macEmulator": "RPCS3 ARM64 (Metal)",
        "aynEmulator": "RPCS3 Android / Winlator",
        "exts": [".iso", ".pkg", ".bin"],
        "keepZip": False,
        "archiveKeywords": "ps3 iso pkg",
    },
}

# Base de données vérifiée des portages Recomp & Decomp natifs hors Switch (Mac M4 + AYN Thor)
CONSOLE_RECOMP_DB = [
    {
        "systems": ["gc", "wii"],
        "keywords": ["twilight princess"],
        "m4": True,
        "ayn": False,
        "installedBoth": True,
        "m4Tool": "Dusklight (Port Natif ARM64 Metal 60fps — Déjà installé sur Mac M4)",
        "aynTool": None,
        "note": "Installé sur Mac mini M4 (Dusklight.app) · Sauvegardes GCI synchronisées avec Dolphin AYN Thor",
    },
    {
        "systems": ["wiiu", "gc"],
        "keywords": ["wind waker"],
        "m4": True,
        "ayn": True,
        "installedBoth": True,
        "m4Tool": "Wind Waker HD Recomp (Natif ARM64 Metal 60fps — Déjà installé)",
        "aynTool": "WWHD Recomp v0.5.0 (`org.wwhdrecomp.wwhd` — Déjà installé sur AYN Thor)",
        "note": "Installé en natif 60fps sur Mac M4 et AYN Thor · Sauvegardes synchronisées",
    },
    {
        "systems": ["wiiu"],
        "keywords": ["breath of the wild"],
        "m4": True,
        "ayn": True,
        "installedBoth": True,
        "m4Tool": "BotW Recomp (Natif ARM64 Metal — Déjà installé sur Mac M4)",
        "aynTool": "BotW Recomp AYN Thor (Natif ARM64 Turnip V39 — Déjà installé)",
        "note": "Installé en natif sur Mac M4 et AYN Thor · 6 slots de sauvegarde synchronisés",
    },
    {
        "systems": ["xbox360", "ps3"],
        "keywords": ["skate 3"],
        "m4": True,
        "ayn": True,
        "installedBoth": True,
        "m4Tool": "Skate 3 Recomp v2.0.2 (Natif ARM64 Metal — Déjà installé sur Mac M4)",
        "aynTool": "XenDroid / Skate 3 AYN Thor (Turnip V39)",
        "note": "Installé en natif 60fps sur Mac mini M4 (Skate 3.app)",
    },
    {
        "systems": ["gba"],
        "keywords": ["minish cap"],
        "m4": True,
        "ayn": True,
        "installedBoth": True,
        "m4Tool": "tmc-decomp (Port Natif C ARM64)",
        "aynTool": "Minish Cap Native Android (Standalone_Saves synchronisé)",
        "note": "Portage décompilé natif disponible sur Mac M4 et installé sur AYN Thor",
    },
    {
        "systems": ["gba"],
        "keywords": ["pokemon emerald", "emerald version"],
        "m4": True,
        "ayn": True,
        "installedBoth": True,
        "m4Tool": "pokeemerald-expansion (Natif ARM64 / Emerald3DS)",
        "aynTool": "PokéEmerald DualScreen Android (Standalone_Saves synchronisé)",
        "note": "Portage natif Dual-Screen installé sur AYN Thor",
    },
    {
        "systems": ["gb"],
        "keywords": ["pokemon red", "red version", "pokemon blue"],
        "m4": True,
        "ayn": True,
        "installedBoth": True,
        "m4Tool": "pokered Recomp (Natif C ARM64)",
        "aynTool": "PokéRed Recomp Android (Standalone_Saves synchronisé)",
        "note": "Portage natif C installé sur AYN Thor",
    },
    {
        "systems": ["snes"],
        "keywords": ["link to the past"],
        "m4": True,
        "ayn": True,
        "installedBoth": True,
        "m4Tool": "Zelda3 (Port Natif C ARM64 Metal/SDL2 60fps widescreen)",
        "aynTool": "Zelda3 Native Android (`com.dishii.zelda3` — Standalone_Saves synchronisé)",
        "note": "Portage natif 16:9 60fps installé sur AYN Thor et compatible Mac M4",
    },
    {
        "systems": ["snes"],
        "keywords": ["super metroid"],
        "m4": True,
        "ayn": True,
        "m4Tool": "sm (Port Natif C Super Metroid ARM64)",
        "aynTool": "Super Metroid Native APK",
        "note": "Décompilation native C (60fps + widescreen)",
    },
    {
        "systems": ["n64", "3ds", "n3ds"],
        "keywords": ["ocarina of time"],
        "m4": True,
        "ayn": True,
        "m4Tool": "Ship of Harkinian (Port Natif Mac ARM64 Metal 60/120fps)",
        "aynTool": "Ship of Harkinian Android APK (Natif ARM64 60fps)",
        "note": "Portage natif PC/Mac/Android Ship of Harkinian (caméra libre + 60fps)",
    },
    {
        "systems": ["n64", "3ds", "n3ds"],
        "keywords": ["majora s mask", "majoras mask"],
        "m4": True,
        "ayn": True,
        "m4Tool": "2Ship2Harkinian / Zelda64Recomp (Natif Mac ARM64 Metal 60fps)",
        "aynTool": "2Ship2Harkinian Android APK (Natif ARM64 60fps)",
        "note": "Portage natif 2Ship2Harkinian & Zelda64Recomp (60fps + gyroscope)",
    },
    {
        "systems": ["n64", "nds"],
        "keywords": ["super mario 64"],
        "m4": True,
        "ayn": True,
        "m4Tool": "sm64ex-alo / Render96 (Natif Mac ARM64 60fps)",
        "aynTool": "sm64ex Android APK (Natif ARM64 60fps)",
        "note": "Décompilation native C (caméra libre + 60fps)",
    },
    {
        "systems": ["n64"],
        "keywords": ["mario kart 64"],
        "m4": True,
        "ayn": True,
        "m4Tool": "SpaghettiKart (Port Natif Mac ARM64 60fps)",
        "aynTool": "SpaghettiKart Android APK (Natif ARM64)",
        "note": "Portage décompilé natif 60fps widescreen",
    },
    {
        "systems": ["n64", "3ds", "n3ds"],
        "keywords": ["star fox 64", "lylat wars"],
        "m4": True,
        "ayn": True,
        "m4Tool": "Starship (Port Natif Mac ARM64 Metal 60fps)",
        "aynTool": "Starship Android APK (Natif ARM64)",
        "note": "Portage natif Starship (HarbourMasters) 60fps widescreen",
    },
    {
        "systems": ["n64"],
        "keywords": ["paper mario"],
        "m4": True,
        "ayn": False,
        "m4Tool": "papermario-dx (Décompilation C / Recomp N64 Mac ARM64)",
        "aynTool": None,
        "note": "Décompilation C 100% complétée",
    },
    {
        "systems": ["n64"],
        "keywords": ["banjo kazooie"],
        "m4": True,
        "ayn": False,
        "m4Tool": "BanjoRecomp (N64Recomp Natif Mac ARM64 Metal 60fps)",
        "aynTool": None,
        "note": "Recompilation statique N64Recomp 60fps widescreen",
    },
    {
        "systems": ["n64"],
        "keywords": ["perfect dark"],
        "m4": True,
        "ayn": True,
        "m4Tool": "perfect_dark port (Natif Mac ARM64 60fps)",
        "aynTool": "Perfect Dark Native Android APK",
        "note": "Portage natif C 60fps + double stick",
    },
    {
        "systems": ["ps2"],
        "keywords": ["jak and daxter", "precursor legacy"],
        "m4": True,
        "ayn": False,
        "m4Tool": "OpenGOAL (Compilateur Natif GOAL ARM64/Rosetta 60fps)",
        "aynTool": None,
        "note": "Portage natif OpenGOAL (Jak 1 & Jak II)",
    },
    {
        "systems": ["ps2"],
        "keywords": ["jak ii", "jak 2"],
        "m4": True,
        "ayn": False,
        "m4Tool": "OpenGOAL Jak II (Port Natif 60fps)",
        "aynTool": None,
        "note": "Portage natif OpenGOAL",
    },
    {
        "systems": ["psx", "ps2", "psp"],
        "keywords": ["grand theft auto iii", "grand theft auto 3", "gta 3"],
        "m4": True,
        "ayn": True,
        "m4Tool": "re3 (Port Natif Mac ARM64 60fps)",
        "aynTool": "re3 Android APK",
        "note": "Reverse-engineering natif re3",
    },
    {
        "systems": ["ps2", "psp"],
        "keywords": ["vice city"],
        "m4": True,
        "ayn": True,
        "m4Tool": "reVC (Port Natif Mac ARM64 60fps)",
        "aynTool": "reVC Android APK",
        "note": "Reverse-engineering natif reVC",
    },
    {
        "systems": ["psx"],
        "keywords": ["symphony of the night", "castlevania"],
        "m4": True,
        "ayn": True,
        "m4Tool": "sotn-decomp (Décompilation C PS1)",
        "aynTool": "SOTN Android Port",
        "note": "Décompilation & portage natif disponibles",
    },
    {
        "systems": ["psx"],
        "keywords": ["crash team racing"],
        "m4": True,
        "ayn": False,
        "m4Tool": "CTR-ModSDK / OpenCTR (Décompilation C 60fps)",
        "aynTool": None,
        "note": "Décompilation C PS1 60fps",
    },
    {
        "systems": ["psx"],
        "keywords": ["wipeout"],
        "m4": True,
        "ayn": True,
        "m4Tool": "wipeout-rewrite (Port Natif C Mac ARM64 60fps)",
        "aynTool": "WipEout Rewrite Android",
        "note": "Réécriture C native ultra-fluide",
    },
    {
        "systems": ["xbox360"],
        "keywords": ["sonic unleashed"],
        "m4": True,
        "ayn": False,
        "m4Tool": "UnleashedRecomp (XenonRecomp Natif Mac ARM64 Metal 60fps)",
        "aynTool": None,
        "note": "Recompilation statique Xbox 360 -> ARM64 Metal",
    },
]


def match_console_recomp(sys_id: str, title: str):
    t_low = re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()
    for entry in CONSOLE_RECOMP_DB:
        if sys_id in entry["systems"]:
            if any(kw in t_low for kw in entry["keywords"]):
                return {
                    "m4": entry["m4"],
                    "ayn": entry["ayn"],
                    "installedBoth": entry.get("installedBoth", False),
                    "m4Tool": entry["m4Tool"],
                    "aynTool": entry["aynTool"],
                    "note": entry["note"],
                }
    return None


def detect_system_from_url(url: str) -> str:
    for sys_id, cfg in CONSOLE_CONFIG.items():
        slug = cfg["romsfunSlug"]
        if f"/roms/{slug}/" in url:
            return sys_id
    return "n3ds"


def search_archive_org_mirrors(game_title: str, sys_id: str, max_items: int = 3) -> list:
    """Cherche des miroirs directs sans Captcha sur Archive.org en complément de RomsFun."""
    cfg = CONSOLE_CONFIG.get(sys_id, {})
    clean_q = re.sub(r"[^a-zA-Z0-9 ]+", " ", game_title).strip()
    if not clean_q:
        return []
    kw = cfg.get("archiveKeywords", sys_id)
    q_str = f'title:("{clean_q}") AND ({kw}) AND mediatype:software'
    search_url = (
        f"https://archive.org/advancedsearch.php?q={urllib.parse.quote(q_str)}"
        f"&fl[]=identifier,title,downloads&sort[]=downloads+desc&rows={max_items}&output=json"
    )
    mirrors = []
    try:
        s = cffi_requests.Session()
        r = s.get(search_url, impersonate="chrome", timeout=10)
        if r.status_code != 200:
            return []
        docs = r.json().get("response", {}).get("docs", [])
        valid_exts = tuple(cfg.get("exts", []) + [".zip", ".7z", ".rar"])
        for doc in docs[:2]:
            ident = doc.get("identifier")
            if not ident:
                continue
            rm = s.get(f"https://archive.org/metadata/{ident}", impersonate="chrome", timeout=10)
            if rm.status_code != 200:
                continue
            files = rm.json().get("files", [])
            for f in files:
                fname = f.get("name", "")
                fsize = int(f.get("size") or 0)
                if fsize < 50 * 1024:
                    continue
                if fname.lower().endswith(valid_exts) and not fname.lower().endswith(("_meta.xml", ".torrent")):
                    dl_url = f"https://archive.org/download/{ident}/{urllib.parse.quote(fname)}"
                    sz_str = f"{fsize / (1024**3):.2f} Go" if fsize >= 1024**3 else f"{fsize / (1024**2):.1f} Mo"
                    mirrors.append(
                        {
                            "host": f"Archive.org Direct — {fname} ({sz_str})",
                            "url": dl_url,
                            "size": sz_str,
                            "format": "Archive.org Direct",
                            "fast": True,
                        }
                    )
                    if len(mirrors) >= 4:
                        return mirrors
    except Exception:
        pass
    return mirrors


def parse_romsfun_page(url: str, sys_id: str = "") -> dict:
    """Extrait les métadonnées (titre, jaquette, région, langue) et toutes les versions téléchargeables d'une fiche RomsFun."""
    if not sys_id:
        sys_id = detect_system_from_url(url)
    cfg = CONSOLE_CONFIG.get(sys_id, CONSOLE_CONFIG["n3ds"])

    s = cffi_requests.Session()
    r = s.get(url, impersonate="chrome", timeout=25)
    if r.status_code != 200:
        raise RuntimeError(f"Page RomsFun introuvable (HTTP {r.status_code})")

    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1") or soup.find("title")
    title = h1.get_text(" ", strip=True).replace(" ROM & ISO - RomsFun.com", "").strip() if h1 else url

    thumb = ""
    og_img = soup.find("meta", {"property": "og:image"})
    if og_img and og_img.get("content"):
        thumb = og_img["content"].replace("http://", "https://")

    meta_info = {"Console": cfg["label"], "Mac": cfg["macEmulator"], "AYN Thor": cfg["aynEmulator"]}
    for tr in soup.find_all("tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all(["th", "td"])]
        if len(tds) >= 2:
            k = tds[0].rstrip(":")
            v = " · ".join(tds[1:])
            if k in ("Release Date", "Publisher", "Region", "Language", "Genre"):
                meta_info[k] = v

    # Chercher la page de téléchargement (/download/<slug>-<id>)
    dl_page_url = ""
    for a in soup.find_all("a", href=True):
        if "/download/" in a["href"] and "romsfun.com" in a["href"]:
            dl_page_url = a["href"].strip()
            break

    sections_by_fmt = {}
    if dl_page_url:
        r2 = s.get(dl_page_url, impersonate="chrome", timeout=25)
        if r2.status_code == 200:
            soup2 = BeautifulSoup(r2.text, "html.parser")
            base_prefix = dl_page_url.rstrip("/") + "/"
            for tr in soup2.find_all("tr"):
                a = tr.find("a", href=lambda h: h and h.startswith(base_prefix))
                if not a:
                    continue
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                v_name = tds[0] if len(tds) >= 1 else a.get_text(" ", strip=True)
                v_fmt = tds[1] if len(tds) >= 2 and tds[1] else "ROM Direct"
                v_size = tds[2] if len(tds) >= 3 else ""
                v_url = a["href"].strip()

                # Prioriser les versions Europe / France / EnFr
                is_eu_fr = bool(re.search(r"europe|france|\bfr\b|enfr", v_name, re.I))
                is_demo = bool(re.search(r"\bdemo\b|\bkiosk\b|\bbeta\b|\btaikenban\b|\btrial\b|\bunlock key\b|\btheme\b|\baction replay\b|\bcheats?\b", v_name, re.I))

                label_parts = [v_name]
                if v_size:
                    label_parts.append(f"({v_size})")
                host_label = " ".join(label_parts)

                sec_key = v_fmt or "ROM Direct"
                if sec_key not in sections_by_fmt:
                    sections_by_fmt[sec_key] = {
                        "type": "Base Game",
                        "label": f"{cfg['shortLabel']} — {sec_key}",
                        "mirrors": [],
                    }
                sections_by_fmt[sec_key]["mirrors"].append(
                    {
                        "host": host_label,
                        "url": v_url,
                        "size": v_size,
                        "format": v_fmt,
                        "priority": (0 if is_eu_fr and not is_demo else (1 if not is_demo else 2)),
                    }
                )

    sections = list(sections_by_fmt.values())
    for sec in sections:
        sec["mirrors"].sort(key=lambda m: m.get("priority", 1))

    # Pour les consoles dont le CDN secondaire RomsFun (sto2.romsforever.co) peut activer un challenge Cloudflare
    # ou demander un compte (Wii U, Xbox 360, PS3, PS Vita), ajouter aussi les miroirs directs Archive.org
    if sys_id in ("wiiu", "xbox360", "ps3", "psvita", "gc", "ps2") or not sections:
        arch_mirrors = search_archive_org_mirrors(title, sys_id)
        if arch_mirrors:
            sections.append(
                {
                    "type": "Base Game",
                    "label": f"Miroirs Directs Archive.org ({cfg['shortLabel']})",
                    "mirrors": arch_mirrors,
                }
            )

    recomp = match_console_recomp(sys_id, title)

    return {
        "ok": True,
        "system": sys_id,
        "systemLabel": cfg["label"],
        "macEmulator": cfg["macEmulator"],
        "aynEmulator": cfg["aynEmulator"],
        "url": url,
        "title": title,
        "thumb": thumb,
        "meta": meta_info,
        "recomp": recomp,
        "sections": sections,
    }


def resolve_romsfun_cdn(variant_url: str) -> tuple[str, str, str]:
    """Résout une URL https://romsfun.com/download/<slug>/<idx> en lien CDN signé direct."""
    if not variant_url.startswith("https://romsfun.com/download/"):
        fn = unquote(os.path.basename(urlparse(variant_url).path))
        return variant_url, fn, ""

    s = cffi_requests.Session()
    r1 = s.get(variant_url, impersonate="chrome", timeout=25)
    soup1 = BeautifulSoup(r1.text, "html.parser")
    static_a = soup1.find("a", id="download-link")
    static_href = static_a["href"] if static_a and static_a.get("href") and static_a["href"] != "#" else ""

    ra = s.post(
        "https://romsfun.com/wp-admin/admin-ajax.php",
        data={"action": "k_get_download"},
        headers={"Referer": variant_url, "X-Requested-With": "XMLHttpRequest"},
        impersonate="chrome",
        timeout=25,
    )
    cdn_url = ""
    if ra.status_code == 200:
        try:
            d = ra.json()
            if d.get("success") and d.get("data", {}).get("html"):
                soup_a = BeautifulSoup(d["data"]["html"], "html.parser")
                a = soup_a.find("a", href=True)
                if a:
                    cdn_url = a["href"].strip()
            elif d.get("data", {}).get("code") == "members_only":
                if static_href:
                    cdn_url = static_href
                else:
                    raise RuntimeError("Ce fichier RomsFun requiert une connexion membre — utilisez le miroir Archive.org ci-dessous ou ouvrez dans Safari.")
        except RuntimeError:
            raise
        except Exception:
            pass

    if not cdn_url and static_href:
        cdn_url = static_href
    if not cdn_url:
        raise RuntimeError(f"Impossible d'obtenir le lien CDN RomsFun depuis {variant_url}")

    filename = unquote(os.path.basename(urlparse(cdn_url).path))
    return cdn_url, filename, "https://romsfun.com/"


def update_job_state(state_file: str, data: dict):
    if not state_file:
        return
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def download_and_install_console(
    mirror_url: str,
    sys_id: str,
    game_title: str,
    sec_type: str = "Base Game",
    state_file: str = "",
    fallbacks=None,
    thumb_url: str = "",
):
    cfg = CONSOLE_CONFIG.get(sys_id, CONSOLE_CONFIG["n3ds"])
    state = {
        "status": "resolving",
        "system": sys_id,
        "systemLabel": cfg["label"],
        "gameTitle": f"[{cfg['shortLabel']}] {game_title}",
        "sectionType": sec_type,
        "mirrorUrl": mirror_url,
        "hostUrl": mirror_url,
        "directUrl": "",
        "filename": "",
        "downloadedBytes": 0,
        "totalBytes": 0,
        "percent": 0.0,
        "speedMBps": 0.0,
        "message": f"Génération du jeton CDN direct ({cfg['shortLabel']})…",
        "outputFiles": [],
    }
    update_job_state(state_file, state)

    try:
        candidates = [mirror_url] + [u for u in (fallbacks or []) if u and u != mirror_url]
        # Ajouter automatiquement les miroirs Archive.org en secours si tous les miroirs RomsFun échouent
        arch_backups = [m["url"] for m in search_archive_org_mirrors(game_title, sys_id)]
        for ab in arch_backups:
            if ab not in candidates:
                candidates.append(ab)

        s = cffi_requests.Session()
        resp = None
        direct_url = ""
        filename = ""
        referer_url = ""
        errors = []

        for cand in candidates:
            try:
                state["mirrorUrl"] = cand
                state["hostUrl"] = cand
                state["message"] = f"Résolution du lien direct ({urlparse(cand).netloc})…"
                update_job_state(state_file, state)

                d_url, fn, ref = resolve_romsfun_cdn(cand)
                r_try = s.get(
                    d_url,
                    headers=({"Referer": ref} if ref else {}),
                    impersonate="chrome",
                    stream=True,
                    timeout=90,
                )
                ctype = (r_try.headers.get("Content-Type") or "").lower()
                if r_try.status_code == 200 and "text/html" not in ctype:
                    resp = r_try
                    direct_url = d_url
                    filename = fn
                    referer_url = ref
                    break
                else:
                    r_try.close()
                    errors.append(f"{urlparse(d_url).netloc} (HTTP {r_try.status_code})")
            except Exception as e:
                errors.append(str(e))

        if not resp or not direct_url:
            raise RuntimeError("Aucun miroir direct sans Captcha disponible — " + " | ".join(errors[:3]))

        if not filename:
            filename = re.sub(r"[^a-zA-Z0-9._\- ]+", "_", game_title).strip() + ".zip"
        state["directUrl"] = direct_url
        state["filename"] = filename
        state["status"] = "downloading"
        state["message"] = f"Téléchargement en cours : {filename}"
        update_job_state(state_file, state)

        base_rom_dir = os.path.join(ROMS_ROOT, cfg["macDir"])
        try:
            os.makedirs(os.path.join(base_rom_dir, ".rom_tmp"), exist_ok=True)
            test_f = os.path.join(base_rom_dir, ".rom_tmp", ".write_test")
            with open(test_f, "w") as tf:
                tf.write("ok")
            os.remove(test_f)
        except Exception:
            base_rom_dir = os.path.expanduser(f"~/Downloads/ROMs_{cfg['macDir']}")
            os.makedirs(base_rom_dir, exist_ok=True)

        tmp_dir = os.path.join(base_rom_dir, ".rom_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        dest_archive = os.path.join(tmp_dir, filename)

        try:
            total = int(resp.headers.get("Content-Length") or resp.headers.get("content-length") or 0)
            state["totalBytes"] = total
            downloaded = 0
            t0 = time.time()
            last_report = 0.0

            with open(dest_archive, "wb") as f:
                for chunk in resp.iter_content(chunk_size=512 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_report >= 0.8:
                        last_report = now
                        elapsed = max(0.1, now - t0)
                        speed = (downloaded / (1024 * 1024)) / elapsed
                        pct = (downloaded * 100.0 / total) if total > 0 else 0.0
                        state["downloadedBytes"] = downloaded
                        state["percent"] = round(pct, 1)
                        state["speedMBps"] = round(speed, 2)
                        state["message"] = (
                            f"Téléchargement : {downloaded / (1024*1024):.1f} / {total / (1024*1024):.1f} Mo "
                            f"({pct:.1f}% · {speed:.1f} Mo/s)"
                        )
                        update_job_state(state_file, state)
        finally:
            try:
                resp.close()
            except Exception:
                pass

        state["downloadedBytes"] = downloaded
        state["percent"] = 100.0

        # Extraction automatique si archive (.zip / .7z / .rar) et que la console attend un fichier décompressé
        lower_fn = filename.lower()
        out_paths = []
        should_extract = lower_fn.endswith((".rar", ".7z")) or (lower_fn.endswith(".zip") and not cfg.get("keepZip", False))
        if should_extract and os.path.exists(SEVEN_ZIP_BIN):
            state["status"] = "extracting"
            state["message"] = f"Extraction de {filename} vers {base_rom_dir}…"
            update_job_state(state_file, state)

            ext_sub = os.path.join(tmp_dir, f"ext_{int(time.time())}")
            os.makedirs(ext_sub, exist_ok=True)
            p_ext = subprocess.run(
                [SEVEN_ZIP_BIN, "x", "-y", f"-o{ext_sub}", dest_archive],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if p_ext.returncode == 0:
                os.remove(dest_archive)
                for dp, _, fns in os.walk(ext_sub):
                    for fn_in in sorted(fns):
                        if fn_in.startswith("."):
                            continue
                        src_p = os.path.join(dp, fn_in)
                        dst_p = os.path.join(base_rom_dir, fn_in)
                        shutil.move(src_p, dst_p)
                        out_paths.append(dst_p)
                shutil.rmtree(ext_sub, ignore_errors=True)
            else:
                shutil.rmtree(ext_sub, ignore_errors=True)
                final_p = os.path.join(base_rom_dir, filename)
                shutil.move(dest_archive, final_p)
                out_paths.append(final_p)
        else:
            final_p = os.path.join(base_rom_dir, filename)
            shutil.move(dest_archive, final_p)
            out_paths.append(final_p)

        state["outputFiles"] = out_paths
        state["status"] = "installing"
        state["speedMBps"] = 0.0
        state["message"] = f"Installation automatique ({cfg['macEmulator']} + AYN Thor)…"
        update_job_state(state_file, state)

        def _on_install(msg):
            state["message"] = msg
            update_job_state(state_file, state)

        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import switch_installer

        recomp_info = match_console_recomp(sys_id, game_title)
        summary = switch_installer.install_console_rom(
            out_paths,
            cfg,
            game_title=game_title,
            thumb_url=thumb_url,
            recomp_info=recomp_info,
            cb=_on_install,
        )
        state["outputFiles"] = summary.get("finalPaths") or out_paths
        state["install"] = summary
        state["status"] = "done"
        parts = ["Prêt à jouer !"] + [f"🍎 {m}" for m in summary["mac"][:1]] + [f"🎮 {a}" for a in summary["ayn"][:1]]
        state["message"] = " · ".join(parts)
        update_job_state(state_file, state)
        print(json.dumps(state, ensure_ascii=False))
    except Exception as e:
        state["status"] = "error"
        state["message"] = f"Erreur : {e}"
        update_job_state(state_file, state)
        print(json.dumps(state, ensure_ascii=False))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="RomsFun & Archive.org Multi-Console Downloader")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="Parse game page and return mirrors")
    p_info.add_argument("--url", required=True)
    p_info.add_argument("--system", default="")

    p_res = sub.add_parser("resolve", help="Resolve RomsFun download link to signed CDN URL")
    p_res.add_argument("--url", required=True)

    p_dl = sub.add_parser("download", help="Download, extract and install a console ROM")
    p_dl.add_argument("--url", required=True)
    p_dl.add_argument("--system", required=True)
    p_dl.add_argument("--title", default="ROM Game")
    p_dl.add_argument("--type", default="Base Game")
    p_dl.add_argument("--thumb", default="")
    p_dl.add_argument("--state-file", default="")
    p_dl.add_argument("--fallback", action="append", default=[])

    args = parser.parse_args()
    if args.cmd == "info":
        res = parse_romsfun_page(args.url, args.system)
        print(json.dumps(res, ensure_ascii=False))
    elif args.cmd == "resolve":
        cdn_url, filename, _ = resolve_romsfun_cdn(args.url)
        print(json.dumps({"ok": True, "hostUrl": args.url, "directUrl": cdn_url, "filename": filename}, ensure_ascii=False))
    elif args.cmd == "download":
        download_and_install_console(
            args.url,
            args.system,
            args.title,
            args.type,
            args.state_file,
            args.fallback,
            args.thumb,
        )


if __name__ == "__main__":
    main()
