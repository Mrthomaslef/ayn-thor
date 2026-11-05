#!/usr/bin/env python3
"""
Génère l'inventaire réel (Sauvegardes AYN Thor, ROMs/BIOS GamingSSD, Drivers Turnip, État USB/ADB)
dans data/inventory.json et data/embedded_data.js (fallback 100% hors-ligne).
"""
import datetime
import json
import os
import re
import subprocess

ROOT_DIR = "/Users/thomaslefevre/.t3/projects/ayn-thor"
DATA_DIR = os.path.join(ROOT_DIR, "data")
SAVES_ROOT = "/Volumes/GamingSSD/Games/Sauvegardes_AYN_Thor"
ROMS_ROOT = "/Volumes/GamingSSD/Games/ROMs"
BONUS_ROOT = "/Volumes/GamingSSD/Games/Outils_Bonus"
ADB_BIN = "/Users/thomaslefevre/.local/bin/adb"

SAVE_TARGET_MAP = {
    "3DS_dusk": {
        "label": "Nintendo 3DS (Citra Dusk — MicroSD)",
        "emulator": "Citra Dusk (`dev.twilitrealm.dusk`)",
        "androidPath": "/storage/6531-3035/dusk",
        "category": "3DS",
        "icon": "🕹️",
    },
    "3DS_n3ds": {
        "label": "Nintendo 3DS — Profil n3ds (NAND + SDMC + States)",
        "emulator": "Azahar (`org.azahar_emu.azahar`)",
        "androidPath": "/storage/6531-3035/ROMs/n3ds",
        "category": "3DS",
        "icon": "🎮",
    },
    "3DS_n3ds2": {
        "label": "Nintendo 3DS — Profil Principal n3ds2 (Synchronisé avec Azahar Mac)",
        "emulator": "Azahar (`org.azahar_emu.azahar`)",
        "androidPath": "/storage/6531-3035/ROMs/n3ds2",
        "category": "3DS",
        "icon": "🎮",
    },
    "Dolphin_GC": {
        "label": "Nintendo GameCube (Cartes Mémoire GCI & SRAM — Synchronisé avec Dolphin Mac)",
        "emulator": "Dolphin Emulator",
        "androidPath": "/sdcard/Android/data/org.dolphinemu.dolphinemu/files",
        "category": "GameCube / Wii",
        "icon": "🐬",
    },
    "Dolphin_Wii": {
        "label": "Nintendo Wii (NAND Saves & Title Data — Synchronisé avec Dolphin Mac)",
        "emulator": "Dolphin Emulator",
        "androidPath": "/sdcard/Android/data/org.dolphinemu.dolphinemu/files",
        "category": "GameCube / Wii",
        "icon": "🐬",
    },
    "Download_Main_Save": {
        "label": "Sauvegarde Principale Pokémon (`main` PKHeX)",
        "emulator": "Switch / 3DS PKHeX",
        "androidPath": "/sdcard/Download/main",
        "category": "Pokémon",
        "icon": "⚡",
    },
    "Inline_ROM_Saves": {
        "label": "Sauvegardes NDS Inline (Pokémon SoulSilver & NSMB `.sav` / `.ml0`)",
        "emulator": "melonDS Dual-Screen / RetroArch",
        "androidPath": "/storage/6531-3035/ROMs",
        "category": "Nintendo DS",
        "icon": "📱",
    },
    "NDS_melonDS_Android": {
        "label": "Nintendo DS (melonDS Dual-Screen — Synchronisé avec RetroArch Mac)",
        "emulator": "melonDS Dual-Screen (`me.magnum.melondualds`)",
        "androidPath": "/sdcard/Android/data/me.magnum.melondualds",
        "category": "Nintendo DS",
        "icon": "📱",
    },
    "PS1_DuckStation_States": {
        "label": "PlayStation 1 (DuckStation Save States — Synchronisé avec DuckStation Mac)",
        "emulator": "DuckStation",
        "androidPath": "/sdcard/Android/data/com.github.stenzek.duckstation/files",
        "category": "PlayStation",
        "icon": "🦆",
    },
    "PS2_AetherSX2": {
        "label": "PlayStation 2 — Cartes Mémoire (`Mcd001.ps2` / `Mcd002.ps2` — Synchronisé PCSX2 Mac)",
        "emulator": "AetherSX2 (`xyz.aethersx2.android`)",
        "androidPath": "/sdcard/Android/data/xyz.aethersx2.android/files",
        "category": "PlayStation",
        "icon": "💿",
    },
    "PS2_AetherSX2_States": {
        "label": "PlayStation 2 — Save States AetherSX2",
        "emulator": "AetherSX2 (`xyz.aethersx2.android`)",
        "androidPath": "/sdcard/Android/data/xyz.aethersx2.android/files",
        "category": "PlayStation",
        "icon": "💿",
    },
    "PS2_AetherSX2_Turnip": {
        "label": "PlayStation 2 (NetherSX2 Turnip) — Cartes Mémoire (Synchronisé PCSX2 Mac)",
        "emulator": "NetherSX2 Turnip (`xyz.aethersx2.tturnip`)",
        "androidPath": "/sdcard/Android/data/xyz.aethersx2.tturnip/files",
        "category": "PlayStation",
        "icon": "💿",
    },
    "PS2_AetherSX2_Turnip_States": {
        "label": "PlayStation 2 (NetherSX2 Turnip) — Save States",
        "emulator": "NetherSX2 Turnip (`xyz.aethersx2.tturnip`)",
        "androidPath": "/sdcard/Android/data/xyz.aethersx2.tturnip/files",
        "category": "PlayStation",
        "icon": "💿",
    },
    "PSP_SD": {
        "label": "Sony PSP — Savedata (`PSP/SAVEDATA` — Synchronisé avec PPSSPP Mac)",
        "emulator": "PPSSPP",
        "androidPath": "/storage/6531-3035/ROMs/psp/PSP",
        "category": "PlayStation",
        "icon": "🛸",
    },
    "PSP_SD_States": {
        "label": "Sony PSP — PPSSPP Save States (`PSP/PPSSPP_STATE`)",
        "emulator": "PPSSPP",
        "androidPath": "/storage/6531-3035/ROMs/psp/PSP",
        "category": "PlayStation",
        "icon": "🛸",
    },
    "RetroArch_Saves": {
        "label": "RetroArch — Sauvegardes SRAM (`.srm` / `.sav` — Synchronisé avec RetroArch Mac)",
        "emulator": "RetroArch (64-bit)",
        "androidPath": "/sdcard/RetroArch",
        "category": "RetroArch",
        "icon": "👾",
    },
    "RetroArch_States": {
        "label": "RetroArch — États de sauvegarde (`.state` — Synchronisé avec RetroArch Mac)",
        "emulator": "RetroArch (64-bit)",
        "androidPath": "/sdcard/RetroArch",
        "category": "RetroArch",
        "icon": "👾",
    },
    "Standalone_Saves": {
        "label": "Portages Natifs & Decomps (PokéEmerald DualScreen, PokéRogue, MinishCap, Zelda3, Red Recomp)",
        "emulator": "Ports Android Natifs",
        "androidPath": "/sdcard/Android/data/com.pokeemerald.dualscreen/files",
        "category": "Ports & Decomps",
        "icon": "💎",
    },
    "Switch_Eden": {
        "label": "Nintendo Switch — Sauvegardes NAND Eden (Synchronisé avec Ryujinx Mac)",
        "emulator": "Eden (`dev.eden.eden_emulator`)",
        "androidPath": "/sdcard/Android/data/dev.eden.eden_emulator/files/nand/user",
        "category": "Nintendo Switch",
        "icon": "🔴",
    },
    "Switch_Eden_System": {
        "label": "Nintendo Switch — Clés (`prod.keys`), Config & Profils Système Eden",
        "emulator": "Eden (`dev.eden.eden_emulator`)",
        "androidPath": "/sdcard/Android/data/dev.eden.eden_emulator/files/nand/system",
        "category": "Nintendo Switch",
        "icon": "🔑",
    },
    "Switch_Mods_Updates": {
        "label": "Nintendo Switch — Mods & Updates (MicroSD)",
        "emulator": "Eden (`dev.eden.eden_emulator`)",
        "androidPath": "/storage/6531-3035/mods update",
        "category": "Nintendo Switch",
        "icon": "🧩",
    },
    "Switch_Zip_Backup": {
        "label": "Nintendo Switch — Archive ZIP Mario Kart 8 Deluxe",
        "emulator": "Eden / Yuzu Backup",
        "androidPath": "/storage/6531-3035",
        "category": "Nintendo Switch",
        "icon": "📦",
    },
    "WiiU_Cemu": {
        "label": "Nintendo Wii U — Sauvegardes Cemu (Synchronisé avec Cemu Mac)",
        "emulator": "Cemu Android (`info.cemu.cemu`)",
        "androidPath": "/sdcard/Android/data/info.cemu.cemu/files/mlc01/usr",
        "category": "GameCube / Wii",
        "icon": "🟦",
    },
}


def detect_usb_and_adb():
    usb_info = {
        "connected": False,
        "vendor": "AYN",
        "productName": "",
        "serial": "",
        "idVendor": 0,
        "idProduct": 0,
        "usbMode": "disconnected",
        "usbModeLabel": "Non branchée",
        "adbReady": False,
        "adbDevices": [],
    }
    try:
        out = subprocess.check_output(
            ["/usr/sbin/ioreg", "-p", "IOUSB", "-l", "-w", "0"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=4,
        )
        if "QCS_KALAMAP" in out or '"USB Vendor Name" = "AYN"' in out:
            usb_info["connected"] = True
            m_prod = re.search(r'"USB Product Name" = "([^"]*QCS_KALAMAP[^"]*)"', out)
            if m_prod:
                usb_info["productName"] = m_prod.group(1)
            # Extract block around QCS_KALAMAP
            idx = out.find("QCS_KALAMAP")
            chunk = out[max(0, idx - 600) : min(len(out), idx + 1500)]
            m_ser = re.search(r'"USB Serial Number" = "([^"]+)"', chunk)
            if m_ser:
                usb_info["serial"] = m_ser.group(1)
            m_vid = re.search(r'"idVendor" = (\d+)', chunk)
            if m_vid:
                usb_info["idVendor"] = int(m_vid.group(1))
            m_pid = re.search(r'"idProduct" = (\d+)', chunk)
            if m_pid:
                usb_info["idProduct"] = int(m_pid.group(1))

            pid = usb_info["idProduct"]
            if pid == 20193:  # 0x4EE1
                usb_info["usbMode"] = "mtp_only_no_adb"
                usb_info["usbModeLabel"] = "MTP seul (0x4EE1) — Débogage USB inactif ou écran verrouillé"
            elif pid in (20194, 20199):  # 0x4EE2 (MTP+ADB) or 0x4EE7 (ADB)
                usb_info["usbMode"] = "adb_enabled"
                usb_info["usbModeLabel"] = f"MTP + ADB actif ({hex(pid)})"
            else:
                usb_info["usbMode"] = f"usb_{hex(pid)}"
                usb_info["usbModeLabel"] = f"Branchée USB ({hex(pid)})"
    except Exception:
        pass

    if os.path.exists(ADB_BIN):
        try:
            adb_out = subprocess.check_output(
                [ADB_BIN, "devices", "-l"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            devs = []
            for line in adb_out.splitlines()[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    devs.append({"id": parts[0], "state": parts[1], "raw": line})
            usb_info["adbDevices"] = devs
            usb_info["adbReady"] = any(d["state"] == "device" for d in devs)
        except Exception:
            pass

    return usb_info


def scan_saves():
    items = []
    if not os.path.isdir(SAVES_ROOT):
        return items
    for name in sorted(os.listdir(SAVES_ROOT)):
        if name.startswith(".") or name.startswith("_"):
            continue
        p = os.path.join(SAVES_ROOT, name)
        if not os.path.isdir(p):
            continue
        file_count = 0
        total_bytes = 0
        sample_files = []
        latest_mtime = 0.0
        for r, _, files in os.walk(p):
            for f in sorted(files):
                if f.startswith("."):
                    continue
                file_count += 1
                fp = os.path.join(r, f)
                try:
                    st = os.stat(fp)
                    total_bytes += st.st_size
                    if st.st_mtime > latest_mtime:
                        latest_mtime = st.st_mtime
                except Exception:
                    pass
                rel = os.path.relpath(fp, p)
                if len(sample_files) < 6:
                    sample_files.append(rel)

        meta = SAVE_TARGET_MAP.get(
            name,
            {
                "label": name.replace("_", " "),
                "emulator": "Android Emulator",
                "androidPath": f"/sdcard/Saves/{name}",
                "category": "Autres",
                "icon": "💾",
            },
        )
        items.append(
            {
                "id": name,
                "label": meta["label"],
                "emulator": meta["emulator"],
                "category": meta["category"],
                "icon": meta["icon"],
                "localPath": p,
                "androidPath": meta["androidPath"],
                "fileCount": file_count,
                "sizeBytes": total_bytes,
                "sizeMB": round(total_bytes / (1024 * 1024), 2),
                "sampleFiles": sample_files,
                "updatedAt": (
                    datetime.datetime.fromtimestamp(latest_mtime).strftime("%d/%m/%Y %H:%M")
                    if latest_mtime > 0
                    else "31/10/2026"
                ),
            }
        )
    return items


def scan_roms():
    systems = []
    if not os.path.isdir(ROMS_ROOT):
        return systems

    ROM_EMULATOR_MAP = {
        "Switch": ("Nintendo Switch", "Eden / Citron (Turnip T29)", "🔴", "/sdcard/ROMs/switch"),
        "WiiU": ("Nintendo Wii U", "Cemu Android", "🟦", "/sdcard/ROMs/wiiu"),
        "Wii": ("Nintendo Wii", "Dolphin Emulator", "🐬", "/sdcard/ROMs/wii"),
        "GameCube": ("Nintendo GameCube", "Dolphin Emulator", "🟣", "/sdcard/ROMs/gc"),
        "gc": ("Nintendo GameCube (ES-DE)", "Dolphin Emulator", "🟣", "/sdcard/ROMs/gc"),
        "3DS": ("Nintendo 3DS", "Azahar / Lime3DS", "🕹️", "/sdcard/ROMs/n3ds"),
        "n3ds": ("Nintendo 3DS (ES-DE)", "Azahar / Lime3DS", "🕹️", "/sdcard/ROMs/n3ds"),
        "NDS": ("Nintendo DS", "melonDS Android", "📱", "/sdcard/ROMs/nds"),
        "nds": ("Nintendo DS (ES-DE)", "melonDS Android", "📱", "/sdcard/ROMs/nds"),
        "GBA": ("Game Boy Advance", "RetroArch (mGBA)", "👾", "/sdcard/ROMs/gba"),
        "GBC": ("Game Boy Color", "RetroArch (Gambatte)", "🟢", "/sdcard/ROMs/gbc"),
        "GB": ("Game Boy", "RetroArch (Gambatte)", "⚪", "/sdcard/ROMs/gb"),
        "N64": ("Nintendo 64", "RetroArch (Mupen64Plus-Next)", "🦖", "/sdcard/ROMs/n64"),
        "SNES": ("Super Nintendo", "RetroArch (Snes9x)", "🎛️", "/sdcard/ROMs/snes"),
        "NES": ("Nintendo NES", "RetroArch (Mesen)", "🧱", "/sdcard/ROMs/nes"),
        "PS1": ("PlayStation 1", "DuckStation", "🦆", "/sdcard/ROMs/psx"),
        "psx": ("PlayStation 1 (ES-DE)", "DuckStation", "🦆", "/sdcard/ROMs/psx"),
        "PS2": ("PlayStation 2", "NetherSX2 / AetherSX2", "💿", "/sdcard/ROMs/ps2"),
        "PSP": ("Sony PSP", "PPSSPP", "🛸", "/sdcard/ROMs/psp"),
        "BIOS": ("BIOS & Firmware (PS1, PS2, Switch prod.keys)", "Système / RetroArch", "🔑", "/sdcard/RetroArch/system"),
    }

    seen_realpaths = set()
    for name in sorted(os.listdir(ROMS_ROOT)):
        if name.startswith("."):
            continue
        p = os.path.join(ROMS_ROOT, name)
        if not os.path.isdir(p):
            continue
        real_p = os.path.realpath(p)
        if real_p in seen_realpaths:
            continue

        entries = [
            f
            for f in sorted(os.listdir(p))
            if not f.startswith(".") and f.lower() not in ("systeminfo.txt", "place_roms_here.txt")
        ]
        if not entries:
            continue
        seen_realpaths.add(real_p)

        total_size = 0
        games = []
        for f in entries:
            fp = os.path.join(p, f)
            try:
                sz = os.path.getsize(fp) if os.path.isfile(fp) else 0
            except Exception:
                sz = 0
            total_size += sz
            games.append(
                {
                    "name": f,
                    "sizeMB": round(sz / (1024 * 1024), 1),
                }
            )

        info = ROM_EMULATOR_MAP.get(
            name,
            (name.upper(), "ES-DE / RetroArch", "🎮", f"/sdcard/ROMs/{name.lower()}"),
        )
        systems.append(
            {
                "id": name,
                "label": info[0],
                "emulator": info[1],
                "icon": info[2],
                "localPath": p,
                "androidPath": info[3],
                "gameCount": len(games),
                "totalSizeGB": round(total_size / (1024**3), 2),
                "totalSizeMB": round(total_size / (1024**2), 1),
                "games": games[:25],
            }
        )
    systems.sort(key=lambda x: (-x["gameCount"], x["label"]))
    return systems


def scan_bonus_tools():
    tools = []
    if not os.path.isdir(BONUS_ROOT):
        return tools
    descriptions = {
        "turnip_mrpurple_T29-toasted.adpkg.zip": {
            "label": "Driver GPU Turnip MrPurple T29-Toasted (Adreno 740 / Snapdragon 8 Gen 2)",
            "type": "Driver GPU Vulkan (.adpkg.zip)",
            "target": "/sdcard/Download/turnip_mrpurple_T29-toasted.adpkg.zip",
            "usage": "Recommandé pour Eden / Citron (Switch) et Azahar (3DS) sur l'AYN Thor (QCS8550).",
        },
        "turnip_mrpurple_T23-toasted.adpkg.zip": {
            "label": "Driver GPU Turnip MrPurple T23-Toasted (Haute stabilité Switch/WiiU)",
            "type": "Driver GPU Vulkan (.adpkg.zip)",
            "target": "/sdcard/Download/turnip_mrpurple_T23-toasted.adpkg.zip",
            "usage": "Alternative ultra-stable pour les jeux Switch récents et Cemu Android.",
        },
        "EmeraldSeaglass_v3.0.ips": {
            "label": "Patch Pokémon Emerald Seaglass v3.0 (.ips)",
            "type": "Patch ROM GBA (.ips)",
            "target": "/sdcard/ROMs/gba/EmeraldSeaglass_v3.0.ips",
            "usage": "Romhack GBA complet pour RetroArch (mGBA).",
        },
    }
    for f in sorted(os.listdir(BONUS_ROOT)):
        if f.startswith("."):
            continue
        fp = os.path.join(BONUS_ROOT, f)
        if not os.path.isfile(fp):
            continue
        sz = os.path.getsize(fp)
        meta = descriptions.get(
            f,
            {
                "label": f,
                "type": "Fichier Bonus",
                "target": f"/sdcard/Download/{f}",
                "usage": "Outil ou ressource AYN Thor.",
            },
        )
        tools.append(
            {
                "filename": f,
                "label": meta["label"],
                "type": meta["type"],
                "localPath": fp,
                "androidPath": meta["target"],
                "usage": meta["usage"],
                "sizeMB": round(sz / (1024 * 1024), 2),
            }
        )
    return tools


def get_emulator_presets():
    return [
        {
            "id": "eden_switch",
            "name": "Eden / Citron (Nintendo Switch)",
            "package": "dev.eden.eden_emulator",
            "icon": "🔴",
            "socProfile": "Mode Performance / Ventilateur Intelligent",
            "gpuDriver": "turnip_mrpurple_T29-toasted.adpkg.zip",
            "resolution": "1x (720p/1080p Docked) — FSR 80%",
            "settings": [
                "Graphics > GPU Driver : installer `turnip_mrpurple_T29-toasted.adpkg.zip`",
                "Graphics > API : Vulkan | Disk Shader Cache : ON | Async Shaders : ON",
                "CPU > Accuracy : Auto | NCE (Native Code Execution) : Activé (arm64 natif)",
                "Audio > Output Engine : cubeb",
            ],
        },
        {
            "id": "azahar_3ds",
            "name": "Azahar / Lime3DS (Nintendo 3DS)",
            "package": "io.github.lime3ds.android",
            "icon": "🕹️",
            "socProfile": "Mode Standard / Équilibré",
            "gpuDriver": "turnip_mrpurple_T29-toasted.adpkg.zip",
            "resolution": "3x à 4x (1080p OLED)",
            "settings": [
                "Graphics > Graphics API : Vulkan",
                "Graphics > Async Shader Compilation : ON | Hardware Shaders : ON",
                "Audio > Audio Stretching : ON (évite les craquements)",
                "Layout : Large Screen + Small Screen (ou bascule écran avec L3/R3)",
            ],
        },
        {
            "id": "aethersx2_ps2",
            "name": "AetherSX2 / NetherSX2 (PlayStation 2)",
            "package": "xyz.aethersx2.android",
            "icon": "💿",
            "socProfile": "Mode Performance",
            "gpuDriver": "Pilote Système ou Turnip T23",
            "resolution": "2.5x à 3x (1080p) — Widescreen Patches ON",
            "settings": [
                "Graphics > GPU Renderer : Vulkan",
                "Graphics > Threaded Presentation : ON",
                "System > Multi-Threaded VU1 (MTVU) : ON | Instant VU1 : ON",
                "Memory Cards : pointer vers `/sdcard/Android/data/xyz.aethersx2.android/files/memcards`",
            ],
        },
        {
            "id": "dolphin_gc_wii",
            "name": "Dolphin Emulator (GameCube & Wii)",
            "package": "org.dolphinemu.dolphinemu",
            "icon": "🐬",
            "socProfile": "Mode Standard",
            "gpuDriver": "Vulkan + Hybrid Ubershaders",
            "resolution": "3x (1080p)",
            "settings": [
                "GraphicsSettings > Video Backend : Vulkan",
                "Shader Compilation Mode : Hybrid Ubershaders + Compile Shaders Before Starting",
                "Enhancements > Internal Resolution : 3x Native (1080p)",
                "GameCube > Slot A : Memory Card (`GC/USA` & `GC/EUR` synchronisés depuis GamingSSD)",
            ],
        },
        {
            "id": "melonds_nds",
            "name": "melonDS Android (Nintendo DS)",
            "package": "me.magnum.melonds",
            "icon": "📱",
            "socProfile": "Mode Économie d'énergie",
            "gpuDriver": "OpenGL / Software",
            "resolution": "OpenGL 4x",
            "settings": [
                "System > BIOS : Custom BIOS DSi/DS dans `/sdcard/ROMs/BIOS`",
                "Saves > Save Next to ROMFile : ON (compatible Pokémon SoulSilver `.sav`)",
                "Input > Mapping Xbox / Odin Controller Profile",
            ],
        },
        {
            "id": "retroarch_multi",
            "name": "RetroArch AArch64 (GB/GBA/SNES/N64/MD)",
            "package": "com.retroarch.aarch64",
            "icon": "👾",
            "socProfile": "Mode Économie d'énergie",
            "gpuDriver": "Vulkan",
            "resolution": "Integer Scale / Shaders CRT-Royale ou LCD3x",
            "settings": [
                "Drivers > Video : vulkan",
                "Saving > Savefile Directory : `/sdcard/RetroArch/saves`",
                "Saving > Savestate Directory : `/sdcard/RetroArch/states`",
                "Saving > Auto Save State / Auto Load State : ON",
            ],
        },
        {
            "id": "esde_frontend",
            "name": "ES-DE Android (EmulationStation)",
            "package": "org.es_de.frontend",
            "icon": "🚀",
            "socProfile": "Lanceur Console par défaut",
            "gpuDriver": "Natif",
            "resolution": "1080p 60/120Hz",
            "settings": [
                "ROMs Directory : `/sdcard/ROMs`",
                "Media Directory : `/sdcard/ES-DE/downloaded_media`",
                "Alternative Emulators : Eden (Switch), Azahar (n3ds), melonDS Standalone (nds), DuckStation (psx)",
            ],
        },
    ]


def build():
    os.makedirs(DATA_DIR, exist_ok=True)
    usb = detect_usb_and_adb()
    saves = scan_saves()
    roms = scan_roms()
    bonus = scan_bonus_tools()
    presets = get_emulator_presets()

    total_save_files = sum(s["fileCount"] for s in saves)
    total_save_mb = round(sum(s["sizeBytes"] for s in saves) / (1024 * 1024), 1)
    total_rom_games = sum(r["gameCount"] for r in roms)
    total_rom_gb = round(sum(r["totalSizeMB"] for r in roms) / 1024, 2)

    payload = {
        "generatedAt": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "host": {
            "name": "Mac-mini-de-Thomas.local",
            "lanIp": "192.168.1.111",
            "daemonPort": 8798,
            "gamingSsdMounted": os.path.isdir("/Volumes/GamingSSD/Games"),
        },
        "device": usb,
        "summary": {
            "saveFoldersCount": len(saves),
            "saveFilesCount": total_save_files,
            "saveTotalMB": total_save_mb,
            "romSystemsCount": len(roms),
            "romGamesCount": total_rom_games,
            "romTotalGB": total_rom_gb,
            "bonusToolsCount": len(bonus),
        },
        "saves": saves,
        "roms": roms,
        "bonusTools": bonus,
        "presets": presets,
    }

    inv_path = os.path.join(DATA_DIR, "inventory.json")
    with open(inv_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    emb_path = os.path.join(DATA_DIR, "embedded_data.js")
    with open(emb_path, "w", encoding="utf-8") as f:
        f.write("window.AYN_THOR_DATA = ")
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write(";\n")

    print(
        f"Built inventory: {len(saves)} save sets ({total_save_files} files, {total_save_mb} MB), "
        f"{len(roms)} ROM systems ({total_rom_games} files, {total_rom_gb} GB), "
        f"USB connected={usb['connected']} mode={usb['usbMode']}"
    )


if __name__ == "__main__":
    build()
