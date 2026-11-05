#!/Users/thomaslefevre/.local/share/uv/tools/nxbrew-dl/bin/python
"""
Moteur NXBrew Direct Downloader (sans JDownloader ni compte externe) :
1. Récupère et parse une fiche de jeu sur nxbrew.net (avec contournement IPv4 anti-522).
2. Déverrouille automatiquement les liens ouo.io / 1link (profil Chrome TLS).
3. Résout les liens de téléchargement direct sur FileKeeper / DataNodes / 1Fichier.
4. Télécharge avec suivi de progression en temps réel, extrait les archives .rar/.7z/.zip
   via 7zz vers /Volumes/GamingSSD/Games/ROMs/Switch (et Updates/DLC).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from nxbrew_dl.util import bypass_1link, bypass_ouo

SWITCH_ROMS_DIR = "/Volumes/GamingSSD/Games/ROMs/Switch"
SEVEN_ZIP_BIN = os.path.expanduser("~/.local/bin/7zz")


def fetch_html_ipv4(url: str, timeout: int = 90) -> str:
    """Récupère le HTML via curl -4 pour éviter le timeout IPv6 Cloudflare 522 de nxbrew.net."""
    try:
        p = subprocess.run(
            [
                "/usr/bin/curl",
                "-4",
                "-sL",
                "--max-time",
                str(timeout),
                "-A",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
        if p.returncode == 0 and len(p.stdout) > 1000 and "error code: 522" not in p.stdout:
            return p.stdout
    except Exception:
        pass

    # Fallback via curl_cffi
    r = cffi_requests.get(url, impersonate="chrome", timeout=timeout)
    return r.text


def parse_game_page(url: str) -> dict:
    """Extrait les métadonnées (titre, taille, langues, jaquette) et toutes les sections de téléchargement."""
    html = fetch_html_ipv4(url, timeout=90)
    soup = BeautifulSoup(html, "html.parser")

    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True).replace(" - NXBrew.net", "") if title_el else url

    thumb = ""
    og_img = soup.find("meta", {"property": "og:image"})
    if og_img and og_img.get("content"):
        thumb = og_img["content"]

    meta_info = {}
    full_text = soup.get_text("\n")
    for key in ["Title ID", "Title Size", "Release Date", "Language", "Format", "Publisher", "Category"]:
        m = re.search(rf"{key}\s*:\s*([^\n\r]+)", full_text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            # Nettoyage si plusieurs champs sont collés
            val = re.split(r"(?:Release Date|Category|Publisher|Type|Format|Title ID|Title Size|Language)\s*:", val)[0].strip()
            meta_info[key] = val

    # Extraction structurée des liens de téléchargement (Base Game, Update, DLC)
    sections = []
    current_section = {"type": "Base Game", "label": "Base Game", "mirrors": []}

    # Parcourir les paragraphes et tableaux dans le contenu de l'article
    content_div = soup.find("div", class_=re.compile(r"entry-content|post-content|the-content")) or soup.body
    if content_div:
        for el in content_div.find_all(["p", "h2", "h3", "h4", "tr", "li"]):
            txt = el.get_text(" ", strip=True)
            if not txt:
                continue

            # Détecter un changement de section (Base Game, Update, DLC)
            if re.search(r"\b(Base\s*Game|Update\s*v?[\d\.]+|Update\b|DLC\b|Part\s*\d+)", txt, re.IGNORECASE) and not el.find("a", href=re.compile(r"ouo\.|1link\.|filekeeper|datanodes|1fichier|multiup|gofile|megaup", re.I)):
                if len(txt) < 120:
                    if current_section["mirrors"]:
                        sections.append(current_section)
                    sec_type = "Base Game"
                    if re.search(r"\bupdate\b", txt, re.I):
                        sec_type = "Update"
                    elif re.search(r"\bdlc\b", txt, re.I):
                        sec_type = "DLC"
                    current_section = {"type": sec_type, "label": txt, "mirrors": []}
                    continue

            links = el.find_all("a", href=True)
            for a in links:
                href = a["href"].strip()
                if not any(d in href.lower() for d in ["ouo.io", "ouo.press", "1link.club", "filekeeper.net", "datanodes.to", "1fichier.com", "gofile.io", "megaup.net", "multiup.io"]):
                    continue

                a_txt = a.get_text(" ", strip=True)
                # Chercher le nom de l'hébergeur soit dans le texte du lien, soit dans le strong précédent
                host_name = a_txt
                if a_txt.lower() in ("download", "link", "mirror", "") or "part" in a_txt.lower():
                    prev_strong = a.find_previous("strong")
                    if prev_strong and prev_strong in el.find_all("strong"):
                        host_name = prev_strong.get_text(" ", strip=True)
                        if "part" in a_txt.lower():
                            host_name = f"{host_name} ({a_txt})"

                # Vérifier si le paragraphe lui-même contient un en-tête de section (ex: Update sur la même ligne)
                prev_heading = el.find_previous(["p", "h3", "h4"])
                if prev_heading:
                    ptxt = prev_heading.get_text(" ", strip=True)
                    if len(ptxt) < 100 and re.search(r"\b(Base\s*Game|Update|DLC)\b", ptxt, re.I):
                        if ptxt != current_section["label"]:
                            if current_section["mirrors"]:
                                sections.append(current_section)
                            sec_type = "Base Game"
                            if re.search(r"\bupdate\b", ptxt, re.I):
                                sec_type = "Update"
                            elif re.search(r"\bdlc\b", ptxt, re.I):
                                sec_type = "DLC"
                            current_section = {"type": sec_type, "label": ptxt, "mirrors": []}

                if not any(m["url"] == href for m in current_section["mirrors"]):
                    current_section["mirrors"].append({"host": host_name or "Mirror", "url": href})

    if current_section["mirrors"]:
        sections.append(current_section)

    return {
        "ok": True,
        "url": url,
        "title": title,
        "thumb": thumb,
        "meta": meta_info,
        "sections": sections,
    }


def resolve_shortener(url: str) -> str:
    """Déverrouille ouo.io / ouo.press / 1link.club avec le profil Chrome TLS."""
    if "1link" in url:
        return bypass_1link(url, impersonate="chrome", max_retries=3)
    if "ouo.io" in url or "ouo.press" in url:
        return bypass_ouo(url, impersonate="chrome", max_retries=3)
    return url


def resolve_filekeeper_direct(url: str) -> tuple[str, str]:
    """Transforme un lien https://filekeeper.net/<code> en lien CDN direct téléchargeable."""
    s = cffi_requests.Session()
    r1 = s.get(url, impersonate="chrome", timeout=30)
    soup = BeautifulSoup(r1.content, "html.parser")

    filename = ""
    fa = soup.find("file-actions")
    if fa and fa.get("link"):
        filename = os.path.basename(urlparse(fa["link"]).path)

    cd = soup.find("div", id="download-countdown")
    wait_sec = int(cd.get("data-countdown", 5)) if cd else 5
    code = cd.get("data-code") if cd and cd.get("data-code") else urlparse(url).path.strip("/").split("/")[0]
    rand = cd.get("data-rand", "") if cd else ""
    method = (cd.get("data-method") if cd else "") or "Free download"

    time.sleep(max(1, min(wait_sec + 1, 10)))
    fields = {
        "op": "download2",
        "id": code,
        "rand": rand,
        "referer": "",
        "method_free": method,
        "down_direct": "1",
    }
    r2 = s.post(url, data=fields, headers={"Referer": url}, impersonate="chrome", allow_redirects=False, timeout=30)
    direct = r2.headers.get("Location") or r2.headers.get("location")
    if direct:
        return direct, filename

    soup2 = BeautifulSoup(r2.content, "html.parser")
    for a in soup2.find_all("a", href=True):
        if "fetchio" in a["href"] or ".rar" in a["href"] or ".nsp" in a["href"] or ".xci" in a["href"]:
            return a["href"], filename
    raise RuntimeError(f"Impossible d'extraire le lien CDN depuis FileKeeper ({url})")


def update_job_state(state_file: str, data: dict):
    if not state_file:
        return
    try:
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def download_and_extract(mirror_url: str, game_title: str, sec_type: str, state_file: str = ""):
    state = {
        "status": "resolving",
        "gameTitle": game_title,
        "sectionType": sec_type,
        "mirrorUrl": mirror_url,
        "hostUrl": "",
        "directUrl": "",
        "filename": "",
        "downloadedBytes": 0,
        "totalBytes": 0,
        "percent": 0.0,
        "speedMBps": 0.0,
        "message": "Déverrouillage du lien court (ouo.io)…",
        "outputFiles": [],
    }
    update_job_state(state_file, state)

    try:
        host_url = resolve_shortener(mirror_url)
        state["hostUrl"] = host_url
        state["message"] = f"Lien hébergeur résolu : {host_url}"
        update_job_state(state_file, state)

        direct_url = ""
        filename = ""
        if "filekeeper.net" in host_url:
            state["message"] = "Attente du jeton de session FileKeeper (5s)…"
            update_job_state(state_file, state)
            direct_url, filename = resolve_filekeeper_direct(host_url)
        else:
            direct_url = host_url
            filename = os.path.basename(urlparse(host_url).path) or "download.bin"

        state["directUrl"] = direct_url
        if not filename:
            filename = re.sub(r"[^a-zA-Z0-9._\- ]+", "_", game_title).strip() + ".rar"
        state["filename"] = filename
        state["status"] = "downloading"
        state["message"] = f"Téléchargement en cours : {filename}"
        update_job_state(state_file, state)

        # Dossier de destination (avec repli automatique vers ~/Downloads/Switch_ROMs si lancé depuis un LaunchAgent restreint par macOS TCC sur /Volumes/GamingSSD)
        base_switch_dir = SWITCH_ROMS_DIR
        try:
            os.makedirs(os.path.join(base_switch_dir, ".nxbrew_tmp"), exist_ok=True)
            test_file = os.path.join(base_switch_dir, ".nxbrew_tmp", ".write_test")
            with open(test_file, "w") as tf:
                tf.write("ok")
            os.remove(test_file)
        except Exception:
            base_switch_dir = os.path.expanduser("~/Downloads/Switch_ROMs")
            os.makedirs(base_switch_dir, exist_ok=True)

        if sec_type == "Update":
            target_dir = os.path.join(base_switch_dir, "Updates")
        elif sec_type == "DLC":
            target_dir = os.path.join(base_switch_dir, "DLC")
        else:
            target_dir = base_switch_dir
        os.makedirs(target_dir, exist_ok=True)

        tmp_dir = os.path.join(base_switch_dir, ".nxbrew_tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        dest_archive = os.path.join(tmp_dir, filename)

        s = cffi_requests.Session()
        with s.get(direct_url, impersonate="chrome", stream=True, timeout=120) as resp:
            resp.raise_for_status()
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

        state["downloadedBytes"] = downloaded
        state["percent"] = 100.0

        # Extraction automatique si c'est une archive .rar / .7z / .zip
        lower_fn = filename.lower()
        extracted_files = []
        if lower_fn.endswith((".rar", ".7z", ".zip")) and os.path.exists(SEVEN_ZIP_BIN):
            state["status"] = "extracting"
            state["message"] = f"Extraction de {filename} vers {target_dir}…"
            update_job_state(state_file, state)

            before = set(os.listdir(target_dir))
            p_ext = subprocess.run(
                [SEVEN_ZIP_BIN, "x", "-y", f"-o{target_dir}", dest_archive],
                capture_output=True,
                text=True,
                timeout=1800,
            )
            if p_ext.returncode == 0:
                os.remove(dest_archive)
                after = set(os.listdir(target_dir))
                extracted_files = sorted(list(after - before))
            else:
                # Si l'extraction échoue (ex: archive multi-part incomplète), déplacer l'archive dans target_dir
                final_path = os.path.join(target_dir, filename)
                shutil.move(dest_archive, final_path)
                extracted_files = [filename]
        else:
            final_path = os.path.join(target_dir, filename)
            shutil.move(dest_archive, final_path)
            extracted_files = [filename]

        state["status"] = "done"
        state["outputFiles"] = [os.path.join(target_dir, x) for x in extracted_files]
        state["message"] = f"Terminé ! Fichier(s) installé(s) dans {target_dir} : {', '.join(extracted_files) or filename}"
        update_job_state(state_file, state)
        print(json.dumps(state, ensure_ascii=False))
    except Exception as e:
        state["status"] = "error"
        state["message"] = f"Erreur : {e}"
        update_job_state(state_file, state)
        print(json.dumps(state, ensure_ascii=False))
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="NXBrew Direct Scraper & Downloader")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="Parse game page and return JSON mirrors")
    p_info.add_argument("--url", required=True)

    p_resolve = sub.add_parser("resolve", help="Resolve shortener and FileKeeper link")
    p_resolve.add_argument("--url", required=True)

    p_dl = sub.add_parser("download", help="Download and extract a mirror link")
    p_dl.add_argument("--url", required=True)
    p_dl.add_argument("--title", default="Switch Game")
    p_dl.add_argument("--type", default="Base Game")
    p_dl.add_argument("--state-file", default="")

    args = parser.parse_args()
    if args.cmd == "info":
        res = parse_game_page(args.url)
        print(json.dumps(res, ensure_ascii=False))
    elif args.cmd == "resolve":
        host_url = resolve_shortener(args.url)
        direct_url = host_url
        filename = ""
        if "filekeeper.net" in host_url:
            direct_url, filename = resolve_filekeeper_direct(host_url)
        print(json.dumps({"ok": True, "hostUrl": host_url, "directUrl": direct_url, "filename": filename}, ensure_ascii=False))
    elif args.cmd == "download":
        download_and_extract(args.url, args.title, args.type, args.state_file)


if __name__ == "__main__":
    main()
