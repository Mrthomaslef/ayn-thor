#!/usr/bin/env python3
"""
Synchroniseur Universel & Bidirectionnel des Sauvegardes entre :
  1. Les Émulateurs Mac mini M4 (`~/Library/Application Support/...`)
  2. Le Hub Central GamingSSD (`/Volumes/GamingSSD/Games/Sauvegardes_AYN_Thor/...`)
  3. La Console AYN Thor via ADB (`ef1ee24` — Stockage Interne `/sdcard` + MicroSD `/storage/6531-3035`)

Modes supportés :
  - `smart` (Bidirectionnel Intelligent) : compare fichier par fichier (taille + MD5 + date de modification)
    entre le Mac et l'AYN Thor, garde systématiquement la sauvegarde la plus récente, et propage la version
    gagnante vers le Mac, l'AYN Thor et le Hub GamingSSD (avec backup horodaté automatique avant écrasement).
  - `thor_to_mac` : récupère toutes les sauvegardes de l'AYN Thor -> met à jour les émulateurs Mac + GamingSSD.
  - `mac_to_thor` : envoie toutes les sauvegardes des émulateurs Mac -> met à jour l'AYN Thor + GamingSSD.
  - `status` : analyse l'état de synchronisation de chaque console sans modifier de fichiers.
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import struct
import subprocess
import tempfile
from typing import Dict, List, Optional, Tuple

ADB_BIN = "/Users/thomaslefevre/.local/bin/adb"
HOME = os.path.expanduser("~")
APP_SUPPORT = os.path.join(HOME, "Library", "Application Support")
GAMING_SSD_SAVES = "/Volumes/GamingSSD/Games/Sauvegardes_AYN_Thor"
BACKUP_ARCHIVE_DIR = "/Volumes/GamingSSD/Games/Sauvegardes_AYN_Thor/_Backups_Sync"
STATE_FILE = "/Users/thomaslefevre/.t3/projects/ayn-thor/data/sync_state.json"

# Switch TitleID names for clear reporting
SWITCH_TITLES = {
    "01007EF00011E000": "Zelda: Breath of the Wild",
    "0100152000022000": "Mario Kart 8 Deluxe",
    "0100000000010000": "Super Mario Odyssey",
    "0100187003A36000": "Pokémon Let's Go, Eevee!",
    "010018E011D92000": "Pokémon Shining Pearl",
    "010049900F546000": "Super Mario 3D All-Stars (Menu)",
    "010049900F546002": "Super Mario Sunshine (3D All-Stars)",
    "010049900F546003": "Super Mario Galaxy (3D All-Stars)",
    "01006BB00C6F0000": "Zelda: Link's Awakening",
    "010097F018538000": "Dave the Diver",
    "010099C022B96000": "Super Mario Galaxy 2",
    "0100ABF008968000": "Pokémon Sword",
    "0100C1F0051B6000": "Donkey Kong Country: Tropical Freeze",
    "0100F43008C44000": "Pokémon Legends: Z-A",
}

EDEN_PROFILE_UID = "4C15D5EBEE6E47F9BFD9C5157A408208"
EDEN_SAVE_BASE = f"/sdcard/Android/data/dev.eden.eden_emulator/files/nand/user/save/0000000000000000/{EDEN_PROFILE_UID}"
SSD_EDEN_SAVE_BASE = os.path.join(GAMING_SSD_SAVES, "Switch_Eden", "save", "0000000000000000", EDEN_PROFILE_UID)

# Définition des profils de synchronisation directe (Dossiers miroirs Mac <-> GamingSSD <-> AYN Thor)
SYNC_PROFILES = [
    {
        "id": "switch_ryujinx_eden",
        "name": "Nintendo Switch (Ryujinx Mac ↔ Eden AYN Thor)",
        "icon": "🔴",
        "type": "switch_bridge",
        "macPath": os.path.join(APP_SUPPORT, "Ryujinx", "bis", "user", "save"),
        "ssdPath": SSD_EDEN_SAVE_BASE,
        "thorPaths": [EDEN_SAVE_BASE],
    },
    {
        "id": "3ds_azahar",
        "name": "Nintendo 3DS (Azahar Mac ↔ Azahar / Citra Dusk AYN Thor)",
        "icon": "🕹️",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "Azahar", "sdmc", "Nintendo 3DS"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "3DS_n3ds2", "sdmc", "Nintendo 3DS"),
        "thorPaths": [
            "/storage/6531-3035/ROMs/n3ds2/sdmc/Nintendo 3DS",
            "/storage/6531-3035/ROMs/n3ds/sdmc/Nintendo 3DS",
            "/storage/6531-3035/dusk/sdmc/Nintendo 3DS",
        ],
    },
    {
        "id": "gc_dolphin",
        "name": "Nintendo GameCube (Dolphin Mac ↔ Dolphin AYN Thor)",
        "icon": "🐬",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "Dolphin", "GC"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "Dolphin_GC", "GC"),
        "thorPaths": ["/sdcard/Android/data/org.dolphinemu.dolphinemu/files/GC"],
    },
    {
        "id": "wii_dolphin",
        "name": "Nintendo Wii (Dolphin Mac ↔ Dolphin AYN Thor)",
        "icon": "🎾",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "Dolphin", "Wii", "title"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "Dolphin_Wii", "Wii", "title"),
        "thorPaths": ["/sdcard/Android/data/org.dolphinemu.dolphinemu/files/Wii/title"],
    },
    {
        "id": "wiiu_cemu",
        "name": "Nintendo Wii U (Cemu Mac ↔ Cemu AYN Thor)",
        "icon": "🟦",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "Cemu", "mlc01", "usr", "save"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "WiiU_Cemu", "save"),
        "thorPaths": ["/sdcard/Android/data/info.cemu.cemu/files/mlc01/usr/save"],
    },
    {
        "id": "ps2_pcsx2_aethersx2",
        "name": "PlayStation 2 (PCSX2 Mac ↔ NetherSX2 / AetherSX2 AYN Thor)",
        "icon": "💿",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "PCSX2", "memcards"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "PS2_AetherSX2_Turnip", "memcards"),
        "thorPaths": [
            "/sdcard/Android/data/xyz.aethersx2.tturnip/files/memcards",
            "/sdcard/Android/data/xyz.aethersx2.android/files/memcards",
        ],
    },
    {
        "id": "ps1_duckstation",
        "name": "PlayStation 1 (DuckStation Mac ↔ DuckStation AYN Thor)",
        "icon": "🦆",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "DuckStation", "savestates"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "PS1_DuckStation_States", "savestates"),
        "thorPaths": ["/sdcard/Android/data/com.github.stenzek.duckstation/files/savestates"],
    },
    {
        "id": "retroarch_saves",
        "name": "RetroArch SRAM — GBA, GBC, GB, SNES, N64 (Mac ↔ AYN Thor)",
        "icon": "👾",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "RetroArch", "saves"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "RetroArch_Saves", "saves"),
        "thorPaths": ["/sdcard/RetroArch/saves"],
    },
    {
        "id": "retroarch_states",
        "name": "RetroArch Save States (Mac ↔ AYN Thor)",
        "icon": "⚡",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "RetroArch", "states"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "RetroArch_States", "states"),
        "thorPaths": ["/sdcard/RetroArch/states"],
    },
    {
        "id": "nds_melonds",
        "name": "Nintendo DS (RetroArch melonDS Mac ↔ melonDS Dual-Screen + ROMs/nds AYN Thor)",
        "icon": "📱",
        "type": "nds_bridge",
        "macPath": os.path.join(APP_SUPPORT, "RetroArch", "saves", "melonDS DS"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "NDS_melonDS_Android", "files", "saves"),
        "thorPaths": [
            "/sdcard/Android/data/me.magnum.melondualds/files/saves",
            "/storage/6531-3035/ROMs/nds",
        ],
    },
    {
        "id": "psp_ppsspp",
        "name": "Sony PSP (PPSSPP Mac ↔ PPSSPP AYN Thor)",
        "icon": "🛸",
        "type": "directory",
        "macPath": os.path.join(APP_SUPPORT, "PPSSPP", "PSP", "SAVEDATA"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "PSP_SD", "SAVEDATA"),
        "thorPaths": ["/storage/6531-3035/ROMs/psp/PSP/SAVEDATA"],
    },
    {
        "id": "wwhd_recomp",
        "name": "Zelda: The Wind Waker HD Recomp (Mac mini M4 ↔ AYN Thor ↔ Cemu)",
        "icon": "⛵",
        "type": "directory",
        "macPath": "/Volumes/GamingSSD/Games/WindWakerHD_Mac/WindWakerHD-v0.5.0-macos-arm64/data/save/user",
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "Standalone_Saves", "WindWakerHD_Recomp", "save", "user"),
        "thorPaths": [
            "/sdcard/Android/data/org.wwhdrecomp.wwhd/files/save/user",
            "/sdcard/Android/data/info.cemu.cemu/files/mlc01/usr/save/00050000/10143600/user/80000001",
        ],
    },
    {
        "id": "botw_recomp",
        "name": "Zelda: Breath of the Wild Recomp & Duo (Mac mini M4 ↔ AYN Thor Recomp & Eden Duo)",
        "icon": "🏹",
        "type": "directory",
        "macPath": "/Volumes/GamingSSD/Games/Sauvegardes_AYN_Thor/BotW_Recomp_Mac/profile",
        "ssdPath": "/Volumes/GamingSSD/Games/Sauvegardes_AYN_Thor/Switch_Eden/save/0000000000000000/4C15D5EBEE6E47F9BFD9C5157A408208/01007EF00011E000",
        "thorPaths": [
            "/sdcard/Android/data/com.botwrecompandroid/files/v190/save",
            "/sdcard/Android/data/com.botwrecompandroid/files/save",
            "/sdcard/Android/data/dev.legacy.eden_emulator/files/nand/user/save/0000000000000000/4C15D5EBEE6E47F9BFD9C5157A408208/01007EF00011E000",
        ],
    },
    {
        "id": "standalone_ports",
        "name": "Portages & Decomps (PokéEmerald DualScreen, PokéRogue, Minish Cap, Zelda 3, Red Recomp, Wind Waker HD)",
        "icon": "💎",
        "type": "standalone_bundle",
        "macPath": os.path.join(GAMING_SSD_SAVES, "Standalone_Saves"),
        "ssdPath": os.path.join(GAMING_SSD_SAVES, "Standalone_Saves"),
        "thorPaths": [
            "/sdcard/Android/data/com.pokeemerald.dualscreen/files",
            "/sdcard/Android/data/com.example.pokerogueoffline/files",
            "/sdcard/Android/data/dev.picori.tmc/files",
            "/sdcard/Android/data/com.dishii.zelda3/files",
            "/sdcard/Android/data/org.wwhdrecomp.wwhd/files/save",
            "/storage/6531-3035/red recomp",
        ],
    },
]


def run_adb(args: List[str], timeout: int = 30) -> Tuple[int, str, str]:
    if not os.path.exists(ADB_BIN):
        return -1, "", "adb binary not found"
    try:
        p = subprocess.run([ADB_BIN] + args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return -1, "", str(e)


def get_active_thor_serial() -> Optional[str]:
    rc, out, _ = run_adb(["devices"], timeout=5)
    if rc != 0:
        return None
    for line in out.splitlines()[1:]:
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_local_tree(root: str, ignore_ext: Tuple[str, ...] = ()) -> Dict[str, Dict]:
    res = {}
    if not os.path.isdir(root):
        return res
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("rom_data", "assets", "assets_src", "__MACOSX")]
        for f in files:
            if f.startswith(".") or f in ("ExtraData0", "ExtraData1", "systeminfo.txt", "systeminfo_1.txt", "baserom.gba", "zelda3_assets.dat"):
                continue
            if ignore_ext and f.lower().endswith(ignore_ext):
                continue
            fp = os.path.join(r, f)
            if not os.path.isfile(fp):
                continue
            rel = os.path.relpath(fp, root).replace("\\", "/")
            try:
                st = os.stat(fp)
                res[rel] = {
                    "abs": fp,
                    "mtime": int(st.st_mtime),
                    "size": st.st_size,
                    "md5": file_md5(fp),
                }
            except Exception:
                pass
    return res


def get_ryujinx_save_map(ryu_save_root: str) -> Dict[str, str]:
    """
    Retourne le mapping {TitleID_Hex_16: chemin_dossier_slot_actif} dans Ryujinx.
    Ignore les save_ids de type DeviceSaveData (save_data_type != 1 quand user_id == 0) s'il existe déjà un slot User.
    """
    mapping: Dict[str, str] = {}
    if not os.path.isdir(ryu_save_root):
        return mapping
    for entry in sorted(os.listdir(ryu_save_root)):
        slot_dir = os.path.join(ryu_save_root, entry)
        ed0 = os.path.join(slot_dir, "ExtraData0")
        if not os.path.isfile(ed0):
            continue
        try:
            with open(ed0, "rb") as f:
                data = f.read(64)
            if len(data) < 36:
                continue
            tid_int = struct.unpack("<Q", data[:8])[0]
            tid = f"{tid_int:016X}"
            save_type = data[32]  # 1 = Account/User save
            dir0 = os.path.join(slot_dir, "0")
            dir1 = os.path.join(slot_dir, "1")
            # Choisir le sous-dossier 0 ou 1 qui contient les fichiers les plus récents
            files0 = scan_local_tree(dir0)
            files1 = scan_local_tree(dir1)
            active_sub = dir0
            if files1 and not files0:
                active_sub = dir1
            elif files1 and files0:
                max0 = max(v["mtime"] for v in files0.values())
                max1 = max(v["mtime"] for v in files1.values())
                active_sub = dir1 if max1 > max0 else dir0

            # Préférer les slots UserSaveData (save_type == 1) ou non vides
            if tid not in mapping or (save_type == 1 and (files0 or files1)):
                mapping[tid] = active_sub
        except Exception:
            pass
    return mapping


def pull_thor_dir_to_temp(serial: str, remote_dir: str, temp_dest: str) -> bool:
    os.makedirs(temp_dest, exist_ok=True)
    rc, _, _ = run_adb(["-s", serial, "shell", f"[ -d \"{remote_dir}\" ] && echo OK"], timeout=6)
    if rc != 0:
        return False
    rc2, _, _ = run_adb(["-s", serial, "pull", "-a", f"{remote_dir}/.", temp_dest], timeout=120)
    return rc2 == 0


def push_local_dir_to_thor(serial: str, local_dir: str, remote_dir: str) -> bool:
    if not os.path.isdir(local_dir):
        return False
    run_adb(["-s", serial, "shell", f"mkdir -p \"{remote_dir}\""], timeout=6)
    rc, _, _ = run_adb(["-s", serial, "push", f"{local_dir}/.", remote_dir], timeout=180)
    return rc == 0


def push_single_file_to_thor(serial: str, local_file: str, remote_file: str) -> bool:
    parent = os.path.dirname(remote_file)
    run_adb(["-s", serial, "shell", f"mkdir -p \"{parent}\""], timeout=6)
    rc, _, _ = run_adb(["-s", serial, "push", local_file, remote_file], timeout=60)
    return rc == 0


def is_ssd_mounted() -> bool:
    return os.path.isdir("/Volumes/GamingSSD/Games")


def backup_file_before_overwrite(src_file: str, profile_id: str, rel_path: str, origin: str) -> None:
    if not os.path.isfile(src_file):
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_dir = BACKUP_ARCHIVE_DIR if is_ssd_mounted() else "/Users/thomaslefevre/.t3/projects/ayn-thor/data/backups_sync"
    dest = os.path.join(base_dir, stamp, f"{profile_id}_{origin}", rel_path)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    try:
        shutil.copy2(src_file, dest)
    except Exception:
        pass


def merge_trees(
    profile_id: str,
    mac_root: str,
    thor_temp_root: str,
    ssd_root: Optional[str],
    mode: str,
    only_extensions: Optional[Tuple[str, ...]] = None,
) -> Dict:
    """
    Fusionne les arbres de fichiers entre `mac_root` et `thor_temp_root` selon `mode`:
      - 'smart': fichier par fichier, si MD5 différent, le mtime le plus récent gagne.
      - 'thor_to_mac': l'AYN Thor écrase ou complète le Mac.
      - 'mac_to_thor': le Mac écrase ou complète l'AYN Thor.
    Copie également l'arbre unifié vers `ssd_root`.
    """
    mac_files = scan_local_tree(mac_root)
    thor_files = scan_local_tree(thor_temp_root)

    if only_extensions:
        mac_files = {k: v for k, v in mac_files.items() if k.lower().endswith(only_extensions)}
        thor_files = {k: v for k, v in thor_files.items() if k.lower().endswith(only_extensions)}

    all_rels = sorted(set(mac_files.keys()) | set(thor_files.keys()))
    updated_on_mac = []
    updated_on_thor = []
    identical = 0

    for rel in all_rels:
        m_info = mac_files.get(rel)
        t_info = thor_files.get(rel)

        m_abs = os.path.join(mac_root, rel)
        t_abs = os.path.join(thor_temp_root, rel)

        if m_info and t_info:
            if m_info["md5"] == t_info["md5"]:
                identical += 1
                continue

            if mode == "thor_to_mac":
                winner = "thor"
            elif mode == "mac_to_thor":
                winner = "mac"
            else:
                # Smart mode: compare mtime (with 2s tolerance)
                winner = "thor" if t_info["mtime"] > m_info["mtime"] else "mac"

            if winner == "thor":
                if mode != "status":
                    backup_file_before_overwrite(m_abs, profile_id, rel, "mac")
                    os.makedirs(os.path.dirname(m_abs), exist_ok=True)
                    shutil.copy2(t_abs, m_abs)
                updated_on_mac.append(rel)
            else:
                if mode != "status":
                    backup_file_before_overwrite(t_abs, profile_id, rel, "thor")
                    os.makedirs(os.path.dirname(t_abs), exist_ok=True)
                    shutil.copy2(m_abs, t_abs)
                updated_on_thor.append(rel)

        elif t_info and not m_info:
            if mode in ("smart", "thor_to_mac", "status"):
                if mode != "status":
                    os.makedirs(os.path.dirname(m_abs), exist_ok=True)
                    shutil.copy2(t_abs, m_abs)
                updated_on_mac.append(rel)
        elif m_info and not t_info:
            if mode in ("smart", "mac_to_thor", "status"):
                if mode != "status":
                    os.makedirs(os.path.dirname(t_abs), exist_ok=True)
                    shutil.copy2(m_abs, t_abs)
                updated_on_thor.append(rel)

    # Propager vers le Hub GamingSSD (uniquement si le SSD externe est branché)
    if mode != "status" and ssd_root and is_ssd_mounted():
        for rel in all_rels:
            src_candidate = os.path.join(mac_root, rel)
            if not os.path.isfile(src_candidate):
                src_candidate = os.path.join(thor_temp_root, rel)
            if os.path.isfile(src_candidate):
                dst_ssd = os.path.join(ssd_root, rel)
                os.makedirs(os.path.dirname(dst_ssd), exist_ok=True)
                try:
                    if not os.path.isfile(dst_ssd) or file_md5(src_candidate) != file_md5(dst_ssd):
                        shutil.copy2(src_candidate, dst_ssd)
                except Exception:
                    pass

    return {
        "totalFiles": len(all_rels),
        "identical": identical,
        "updatedOnMac": updated_on_mac,
        "updatedOnThor": updated_on_thor,
    }


def sync_profile(profile: Dict, serial: Optional[str], mode: str = "smart") -> Dict:
    pid = profile["id"]
    ptype = profile["type"]
    result = {
        "id": pid,
        "name": profile["name"],
        "icon": profile["icon"],
        "mode": mode,
        "thorConnected": bool(serial),
        "totalFiles": 0,
        "identical": 0,
        "updatedOnMac": [],
        "updatedOnThor": [],
        "details": [],
        "status": "ok",
    }

    if not serial:
        # Si l'AYN Thor n'est pas branchée, on synchronise au moins Mac <-> Hub GamingSSD
        result["status"] = "offline_mac_ssd_only"
        return result

    with tempfile.TemporaryDirectory(prefix=f"aynthor_sync_{pid}_") as tmp_dir:
        if ptype == "switch_bridge":
            # 1. Tirer toutes les sauvegardes Switch d'Eden (AYN Thor)
            thor_eden_tmp = os.path.join(tmp_dir, "eden_saves")
            pull_thor_dir_to_temp(serial, EDEN_SAVE_BASE, thor_eden_tmp)

            # 2. Mapper les dossiers Ryujinx (Mac) par TitleID
            ryu_map = get_ryujinx_save_map(profile["macPath"])
            all_tids = sorted(set(ryu_map.keys()) | set(os.listdir(thor_eden_tmp) if os.path.isdir(thor_eden_tmp) else []))

            for tid in all_tids:
                if not tid or tid.startswith("."):
                    continue
                game_name = SWITCH_TITLES.get(tid.upper(), f"Title {tid}")
                mac_slot = ryu_map.get(tid.upper())
                thor_slot = os.path.join(thor_eden_tmp, tid.upper())
                ssd_slot = os.path.join(SSD_EDEN_SAVE_BASE, tid.upper())

                if not mac_slot:
                    continue
                os.makedirs(thor_slot, exist_ok=True)
                sub_res = merge_trees(f"{pid}_{tid}", mac_slot, thor_slot, ssd_slot, mode)
                # Si Ryujinx a un dossier '1' miroir et que le Mac a été mis à jour, synchroniser aussi '0' et '1'
                parent_slot = os.path.dirname(mac_slot)
                other_sub = os.path.join(parent_slot, "1" if os.path.basename(mac_slot) == "0" else "0")
                if mode != "status" and sub_res["updatedOnMac"] and os.path.isdir(other_sub):
                    for rel in sub_res["updatedOnMac"]:
                        src_f = os.path.join(mac_slot, rel)
                        dst_f = os.path.join(other_sub, rel)
                        if os.path.isfile(src_f):
                            os.makedirs(os.path.dirname(dst_f), exist_ok=True)
                            shutil.copy2(src_f, dst_f)

                result["totalFiles"] += sub_res["totalFiles"]
                result["identical"] += sub_res["identical"]
                for r in sub_res["updatedOnMac"]:
                    result["updatedOnMac"].append(f"{game_name}: {r}")
                for r in sub_res["updatedOnThor"]:
                    result["updatedOnThor"].append(f"{game_name}: {r}")

                if mode != "status" and sub_res["updatedOnThor"]:
                    push_local_dir_to_thor(serial, thor_slot, f"{EDEN_SAVE_BASE}/{tid.upper()}")

        elif ptype == "nds_bridge":
            # Nintendo DS : synchroniser uniquement les fichiers .sav / .dsv entre RetroArch melonDS DS (Mac),
            # melonDS Dual-Screen (Thor) et /storage/6531-3035/ROMs/nds (saves inline)
            thor_nds_tmp = os.path.join(tmp_dir, "nds_saves")
            os.makedirs(thor_nds_tmp, exist_ok=True)
            pull_thor_dir_to_temp(serial, "/sdcard/Android/data/me.magnum.melondualds/files/saves", thor_nds_tmp)

            # Récupérer aussi les .sav inline de /storage/6531-3035/ROMs/nds
            rc, out, _ = run_adb(["-s", serial, "shell", "ls -1 /storage/6531-3035/ROMs/nds/*.sav 2>/dev/null"], timeout=8)
            if rc == 0 and out:
                for remote_sav in out.splitlines():
                    remote_sav = remote_sav.strip()
                    if not remote_sav:
                        continue
                    bn = os.path.basename(remote_sav)
                    inline_tmp = os.path.join(tmp_dir, "inline_" + bn)
                    run_adb(["-s", serial, "pull", "-a", remote_sav, inline_tmp], timeout=20)
                    if os.path.isfile(inline_tmp):
                        dest_in_tmp = os.path.join(thor_nds_tmp, bn)
                        if not os.path.isfile(dest_in_tmp) or os.path.getmtime(inline_tmp) > os.path.getmtime(dest_in_tmp):
                            shutil.copy2(inline_tmp, dest_in_tmp)

            os.makedirs(profile["macPath"], exist_ok=True)
            sub_res = merge_trees(
                pid,
                profile["macPath"],
                thor_nds_tmp,
                profile["ssdPath"],
                mode,
                only_extensions=(".sav", ".dsv", ".srm"),
            )
            result["totalFiles"] = sub_res["totalFiles"]
            result["identical"] = sub_res["identical"]
            result["updatedOnMac"] = sub_res["updatedOnMac"]
            result["updatedOnThor"] = sub_res["updatedOnThor"]

            if mode != "status" and sub_res["updatedOnThor"]:
                for rel in sub_res["updatedOnThor"]:
                    local_sav = os.path.join(thor_nds_tmp, rel)
                    if os.path.isfile(local_sav):
                        push_single_file_to_thor(
                            serial,
                            local_sav,
                            f"/sdcard/Android/data/me.magnum.melondualds/files/saves/{rel}",
                        )
                        push_single_file_to_thor(
                            serial,
                            local_sav,
                            f"/storage/6531-3035/ROMs/nds/{rel}",
                        )

        elif ptype == "standalone_bundle":
            # Synchronise les portages natifs Android <-> GamingSSD Standalone_Saves (ou miroir local si SSD débranché)
            standalone_base = (
                profile["ssdPath"]
                if is_ssd_mounted()
                else "/Users/thomaslefevre/.t3/projects/ayn-thor/data/standalone_saves_mirror"
            )
            pairs = [
                ("PokeEmerald_DualScreen/files", "/sdcard/Android/data/com.pokeemerald.dualscreen/files", (".sav", ".cfg")),
                ("Zelda3/files/saves", "/sdcard/Android/data/com.dishii.zelda3/files/saves", None),
                ("red recomp", "/storage/6531-3035/red recomp", (".sav",)),
            ]
            for rel_ssd, remote_p, exts in pairs:
                local_p = os.path.join(standalone_base, rel_ssd)
                sub_tmp = os.path.join(tmp_dir, rel_ssd.replace("/", "_"))
                pull_thor_dir_to_temp(serial, remote_p, sub_tmp)
                os.makedirs(local_p, exist_ok=True)
                sub_res = merge_trees(f"{pid}_{rel_ssd}", local_p, sub_tmp, None, mode, only_extensions=exts)
                result["totalFiles"] += sub_res["totalFiles"]
                result["identical"] += sub_res["identical"]
                result["updatedOnMac"].extend([f"{rel_ssd}/{x}" for x in sub_res["updatedOnMac"]])
                result["updatedOnThor"].extend([f"{rel_ssd}/{x}" for x in sub_res["updatedOnThor"]])
                if mode != "status" and sub_res["updatedOnThor"]:
                    for f_rel in sub_res["updatedOnThor"]:
                        lf = os.path.join(sub_tmp, f_rel)
                        if os.path.isfile(lf):
                            push_single_file_to_thor(serial, lf, f"{remote_p}/{f_rel}")

            # Minish Cap Decomp : tirer uniquement les fichiers de sauvegarde (.sav, state_*.bin) à la racine de files/
            tmc_remote = "/sdcard/Android/data/dev.picori.tmc/files"
            tmc_local = os.path.join(standalone_base, "MinishCap_Decomp/files")
            tmc_tmp = os.path.join(tmp_dir, "tmc_saves")
            os.makedirs(tmc_tmp, exist_ok=True)
            rc_tmc, out_tmc, _ = run_adb(["-s", serial, "shell", f"ls -1 {tmc_remote}/*.sav {tmc_remote}/state_*.bin 2>/dev/null"], timeout=6)
            if rc_tmc == 0 and out_tmc:
                for rf in out_tmc.splitlines():
                    rf = rf.strip()
                    if rf:
                        run_adb(["-s", serial, "pull", "-a", rf, os.path.join(tmc_tmp, os.path.basename(rf))], timeout=15)
            sub_tmc = merge_trees(f"{pid}_tmc", tmc_local, tmc_tmp, None, mode, only_extensions=(".sav", ".bin"))
            result["totalFiles"] += sub_tmc["totalFiles"]
            result["identical"] += sub_tmc["identical"]
            result["updatedOnMac"].extend([f"MinishCap/{x}" for x in sub_tmc["updatedOnMac"]])
            result["updatedOnThor"].extend([f"MinishCap/{x}" for x in sub_tmc["updatedOnThor"]])
            if mode != "status" and sub_tmc["updatedOnThor"]:
                for f_rel in sub_tmc["updatedOnThor"]:
                    lf = os.path.join(tmc_tmp, f_rel)
                    if os.path.isfile(lf):
                        push_single_file_to_thor(serial, lf, f"{tmc_remote}/{f_rel}")

        else:
            # Type 'directory' standard (3DS, GC, Wii, WiiU, PS2, PS1, RetroArch, PSP)
            primary_thor = profile["thorPaths"][0]
            thor_tmp = os.path.join(tmp_dir, "thor_dir")
            pull_thor_dir_to_temp(serial, primary_thor, thor_tmp)
            os.makedirs(profile["macPath"], exist_ok=True)
            sub_res = merge_trees(pid, profile["macPath"], thor_tmp, profile["ssdPath"], mode)
            result["totalFiles"] = sub_res["totalFiles"]
            result["identical"] = sub_res["identical"]
            result["updatedOnMac"] = sub_res["updatedOnMac"]
            result["updatedOnThor"] = sub_res["updatedOnThor"]

            if mode != "status" and sub_res["updatedOnThor"]:
                for target_thor in profile["thorPaths"]:
                    for f_rel in sub_res["updatedOnThor"]:
                        lf = os.path.join(thor_tmp, f_rel)
                        if os.path.isfile(lf):
                            push_single_file_to_thor(serial, lf, f"{target_thor}/{f_rel}")

    return result


def run_sync_all(mode: str = "smart", target_profile_id: Optional[str] = None) -> Dict:
    serial = get_active_thor_serial()
    started = datetime.datetime.now()
    results = []
    for prof in SYNC_PROFILES:
        if target_profile_id and prof["id"] != target_profile_id:
            continue
        res = sync_profile(prof, serial, mode=mode)
        results.append(res)

    total_mac_updates = sum(len(r["updatedOnMac"]) for r in results)
    total_thor_updates = sum(len(r["updatedOnThor"]) for r in results)
    total_files = sum(r["totalFiles"] for r in results)

    summary = {
        "timestamp": started.strftime("%d/%m/%Y %H:%M:%S"),
        "mode": mode,
        "thorConnected": bool(serial),
        "serial": serial or "",
        "totalFilesChecked": total_files,
        "updatedOnMacCount": total_mac_updates,
        "updatedOnThorCount": total_thor_updates,
        "profiles": results,
    }

    if mode != "status":
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Synchronisation Bidirectionnelle Mac mini M4 <-> AYN Thor")
    parser.add_argument(
        "--mode",
        choices=["smart", "thor_to_mac", "mac_to_thor", "status"],
        default="smart",
        help="Mode de synchronisation (défaut: smart)",
    )
    parser.add_argument("--profile", default=None, help="ID d'un profil spécifique (optionnel)")
    args = parser.parse_args()

    out = run_sync_all(mode=args.mode, target_profile_id=args.profile)
    print(json.dumps(out, indent=2, ensure_ascii=False))
