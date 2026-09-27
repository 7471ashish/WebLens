#!/usr/bin/env python3
"""
WebLens Cross-Browser Extension Builder & Packager (Python Edition)
Generates unpacked directories and production zip archives for:
  - Google Chrome (dist/chrome/ + dist/weblens-chrome-v1.0.0.zip)
  - Mozilla Firefox (dist/firefox/ + dist/weblens-firefox-v1.0.0.zip)
"""

import os
import shutil
import zipfile

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXT_DIR = os.path.join(ROOT_DIR, "extension")
DIST_DIR = os.path.join(ROOT_DIR, "dist")
CHROME_DIST = os.path.join(DIST_DIR, "chrome")
FIREFOX_DIST = os.path.join(DIST_DIR, "firefox")

SHARED_FILES = [
    "side_panel.html",
    "side_panel.css",
    "side_panel.js",
    "popup.html",
    "popup.js",
    "background.js",
]

SHARED_DIRS = ["icons"]


def build_target(target_name: str, target_dir: str, manifest_source: str) -> None:
    print(f"[WebLens Build] Building {target_name}...")
    if os.path.exists(target_dir):
        shutil.rmtree(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    # Copy shared files
    for f in SHARED_FILES:
        src = os.path.join(EXT_DIR, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(target_dir, f))
        else:
            print(f"[Warning] Missing shared file: {f}")

    # Copy shared directories
    for d in SHARED_DIRS:
        src = os.path.join(EXT_DIR, d)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(target_dir, d))

    # Copy target manifest
    manifest_src = os.path.join(EXT_DIR, manifest_source)
    shutil.copy2(manifest_src, os.path.join(target_dir, "manifest.json"))
    print(f"[WebLens Build] -> Created {target_dir}")


def create_zip(source_dir: str, zip_name: str) -> None:
    zip_path = os.path.join(DIST_DIR, zip_name)
    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(source_dir):
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, source_dir)
                zipf.write(full, rel)
    print(f"[WebLens Build] -> Generated zip: {zip_name}")


def main() -> None:
    os.makedirs(DIST_DIR, exist_ok=True)

    build_target("Chrome (MV3)", CHROME_DIST, "manifest.chrome.json")
    build_target("Firefox (MV3 / Gecko)", FIREFOX_DIST, "manifest.firefox.json")

    import json
    with open(os.path.join(EXT_DIR, "manifest.chrome.json"), "r", encoding="utf-8") as f:
        chrome_ver = json.load(f).get("version", "1.0.1")
    with open(os.path.join(EXT_DIR, "manifest.firefox.json"), "r", encoding="utf-8") as f:
        firefox_ver = json.load(f).get("version", "1.0.1")

    chrome_zip = f"weblens-chrome-v{chrome_ver}.zip"
    firefox_zip = f"weblens-firefox-v{firefox_ver}.zip"

    create_zip(CHROME_DIST, chrome_zip)
    create_zip(FIREFOX_DIST, firefox_zip)

    print("\n[WebLens Build] Build complete!")
    print("Unpacked directories:")
    print("  - Chrome:  dist/chrome/ (Load in chrome://extensions)")
    print("  - Firefox: dist/firefox/ (Load in about:debugging#/runtime/this-firefox)")
    print("Store Packages:")
    print(f"  - Chrome Web Store: dist/{chrome_zip}")
    print(f"  - Mozilla AMO:      dist/{firefox_zip}\n")


if __name__ == "__main__":
    main()
