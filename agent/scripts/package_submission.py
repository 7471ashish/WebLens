"""
Submission Packaging & Staging Script (scripts/package_submission.py)
====================================================================
Builds a clean, self-contained staging directory and creates the final
hackathon submission ZIP archive.

Usage:
    python scripts/package_submission.py
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path

# Add current scripts directory to path to import check_submission
sys.path.insert(0, os.path.dirname(__file__))
from check_submission import check_submission

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DIST_DIR = WORKSPACE_ROOT / "dist"
STAGING_PARENT = DIST_DIR / "submission"
PACKAGE_NAME = "brand-ai-readiness-audit"
STAGING_DIR = STAGING_PARENT / PACKAGE_NAME
FINAL_ZIP_PATH = DIST_DIR / f"{PACKAGE_NAME}.zip"

# Required root files
ROOT_FILES = [
    "marketplace.json",
    "README.md",
    "LICENSE",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    "run_master_audit.py",
    "llm_client.py",
    "conftest.py",
    "test_master_integration.py",
    "test_multipage_audit.py",
    "test_local_mock_site.py",
    "test_network_resilience.py",
    "test_final_output_schema.py",
    "test_offline_fallback.py",
    "test_title_parsing.py",
    "test_quality_gating.py",
]

# Required directories to copy recursively
DIRECTORIES_TO_COPY = [
    ("skills", "skills"),
    ("scripts", "scripts"),
]

EXCLUDE_DIR_NAMES = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".git", ".idea", ".vscode", "venv", ".venv", "env", "node_modules",
    "scratch", "dist", "build", "htmlcov",
}

EXCLUDE_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".log", ".tmp",
}

EXCLUDE_FILE_NAMES = {
    ".DS_Store", "Thumbs.db", "output.json", ".env", ".env.local",
}


def build_clean_staging():
    print("==================================================")
    print("1. PREPARING CLEAN STAGING DIRECTORY")
    print(f"Destination: {STAGING_DIR}")
    print("==================================================")

    if STAGING_PARENT.exists():
        shutil.rmtree(STAGING_PARENT)
    STAGING_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Copy root files
    for rf in ROOT_FILES:
        src = WORKSPACE_ROOT / rf
        if src.is_file():
            dst = STAGING_DIR / rf
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"  + Copied root file: {rf}")
        else:
            print(f"  ! Warning: Root file {rf} not found at {src}")

    # 2. Copy directories cleanly (skipping cache/dev artifacts)
    for src_rel, dst_rel in DIRECTORIES_TO_COPY:
        src_dir = WORKSPACE_ROOT / src_rel
        dst_dir = STAGING_DIR / dst_rel
        if not src_dir.is_dir():
            print(f"  ! Warning: Source directory {src_dir} not found")
            continue

        for current_root, dirs, files in os.walk(src_dir):
            # Prune excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES and not d.endswith(".egg-info")]

            rel_path = Path(current_root).relative_to(src_dir)
            target_current_dir = dst_dir / rel_path
            target_current_dir.mkdir(parents=True, exist_ok=True)

            for f in files:
                f_path = Path(current_root) / f
                if f in EXCLUDE_FILE_NAMES or f_path.suffix.lower() in EXCLUDE_EXTENSIONS or f.startswith(".env"):
                    continue

                dst_file = target_current_dir / f
                shutil.copy2(f_path, dst_file)

        print(f"  + Cleanly copied directory: {src_rel} -> {dst_rel}")

    print("\nStaging complete.")


def create_submission_zip():
    print("\n==================================================")
    print("3. CREATING FINAL SUBMISSION ZIP ARCHIVE")
    print(f"Archive: {FINAL_ZIP_PATH}")
    print("==================================================")

    if FINAL_ZIP_PATH.exists():
        FINAL_ZIP_PATH.unlink()

    with zipfile.ZipFile(FINAL_ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for current_root, dirs, files in os.walk(STAGING_PARENT):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIR_NAMES]
            for f in files:
                f_path = Path(current_root) / f
                arcname = f_path.relative_to(STAGING_PARENT)
                zf.write(f_path, arcname)

    zip_size_bytes = FINAL_ZIP_PATH.stat().st_size
    zip_size_mb = zip_size_bytes / (1024 * 1024)
    print(f"Created: {FINAL_ZIP_PATH.name} ({zip_size_mb:.2f} MB)")
    return zip_size_mb


def scan_zip_archive(zip_path: Path):
    print("\n==================================================")
    print("4. SCANNING FINAL ZIP ARCHIVE INTEGRITY")
    print("==================================================")

    with zipfile.ZipFile(zip_path, "r") as zf:
        infolist = zf.infolist()
        print(f"Total entries in ZIP: {len(infolist)}")

        forbidden_found = []
        for info in infolist:
            name = info.filename
            if "__pycache__" in name or ".pytest_cache" in name or name.endswith(".pyc"):
                forbidden_found.append(f"Cache entry: {name}")
            if name.endswith("/.env") or name == f"{PACKAGE_NAME}/.env":
                forbidden_found.append(f"Secret file: {name}")
            if any(name.endswith(ext) for ext in [".pt", ".pth", ".bin", ".onnx", ".safetensors"]):
                forbidden_found.append(f"Model weight: {name}")

        if forbidden_found:
            print("CRITICAL: Forbidden entries found inside ZIP:")
            for item in forbidden_found:
                print(f"  - {item}")
            return False

    print("All ZIP integrity checks PASSED. Clean package!")
    return True


def main():
    build_clean_staging()

    print("\n==================================================")
    print("2. RUNNING VALIDATOR ON STAGED SUBMISSION")
    print("==================================================")
    is_valid, errors = check_submission(STAGING_DIR)
    if not is_valid:
        print("ERROR: Staged directory failed validation!")
        sys.exit(1)

    zip_size_mb = create_submission_zip()

    if not scan_zip_archive(FINAL_ZIP_PATH):
        print("ERROR: Final ZIP failed inspection!")
        sys.exit(1)

    print(f"\n==================================================")
    print("SUBMISSION PACKAGE READY!")
    print(f"Directory : {STAGING_DIR}")
    print(f"ZIP File  : {FINAL_ZIP_PATH}")
    print(f"Size      : {zip_size_mb:.2f} MB (Hackathon Limit: 50.0 MB)")
    print("==================================================")


if __name__ == "__main__":
    main()
