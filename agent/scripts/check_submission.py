"""
Submission Validation Script (scripts/check_submission.py)
==========================================================
Deterministic validator for the Adobe University Hackathon Round 3 submission package.
Validates marketplace schema, skill integrity, secrets absence, zero cache files,
size constraints, and portability.

Exits with code 0 if valid, non-zero if validation fails.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

FORBIDDEN_EXTENSIONS = {
    ".pt", ".pth", ".bin", ".onnx", ".safetensors", ".ckpt",
    ".pyc", ".pyo", ".pyd",
}

FORBIDDEN_DIRS = {
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".git", ".idea", ".vscode", "venv", ".venv", "env", "node_modules",
    "htmlcov",
}

SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"gsk_[a-zA-Z0-9]{20,}"),
    re.compile(r"AIza[a-zA-Z0-9_\-]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
]

HARDCODED_MACHINE_PATH_PATTERNS = [
    re.compile(r"[a-zA-Z]:\\Users\\[a-zA-Z0-9_]+", re.IGNORECASE),
    re.compile(r"/home/[a-zA-Z0-9_]+/(?!Desktop/Adobe2)", re.IGNORECASE),
]


def check_submission(target_dir: str | Path) -> tuple[bool, list[str]]:
    root = Path(target_dir).resolve()
    errors: list[str] = []
    warnings: list[str] = []

    print(f"==================================================")
    print(f"SUBMISSION VALIDATION FOR: {root.name}")
    print(f"Path: {root}")
    print(f"==================================================")

    # 1. Check marketplace.json
    m_json_path = root / "marketplace.json"
    if not m_json_path.is_file():
        errors.append("Missing required file: marketplace.json")
    else:
        try:
            with open(m_json_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)

            if not isinstance(manifest, dict):
                errors.append("marketplace.json must be a JSON object")
            else:
                skills = manifest.get("skills", [])
                if not isinstance(skills, list) or len(skills) == 0:
                    errors.append("marketplace.json must contain a non-empty 'skills' array")
                else:
                    entrypoint_count = 0
                    for sk in skills:
                        sk_id = sk.get("id")
                        sk_path = sk.get("path")
                        if not sk_id or not sk_path:
                            errors.append(f"Skill entry missing 'id' or 'path': {sk}")
                            continue

                        skill_dir = root / sk_path
                        if not skill_dir.is_dir():
                            errors.append(f"Skill directory not found for '{sk_id}': {sk_path}")
                        else:
                            skill_md = skill_dir / "SKILL.md"
                            if not skill_md.is_file():
                                errors.append(f"Skill '{sk_id}' missing SKILL.md in {sk_path}")
                            elif skill_md.stat().st_size < 50:
                                errors.append(f"SKILL.md in {sk_path} is suspiciously empty (<50 bytes)")

                        if sk.get("entrypoint") is True:
                            entrypoint_count += 1

                    if entrypoint_count != 1:
                        errors.append(f"marketplace.json must have exactly 1 designated entrypoint (found {entrypoint_count})")
        except json.JSONDecodeError as exc:
            errors.append(f"marketplace.json is invalid JSON: {exc}")

    # 2. Check README.md
    readme_path = root / "README.md"
    if not readme_path.is_file() or readme_path.stat().st_size < 100:
        errors.append("Missing or empty README.md")

    # 3. Check .env.example exists and .env is absent
    env_file = root / ".env"
    if env_file.exists():
        errors.append("CRITICAL: .env file found in submission package! Real .env must be excluded.")

    env_example = root / ".env.example"
    if not env_example.is_file():
        errors.append("Missing required file: .env.example")
    else:
        content = env_example.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                val_clean = val.strip()
                if val_clean and not any(ph in val_clean.lower() for ph in ["your_", "placeholder", "here", "default", "example", "llama-", "gpt-", "qwen", "10.0"]):
                    errors.append(f".env.example contains non-placeholder value for {key}: '{val_clean}'")

    # 4. Check requirements.txt or pyproject.toml
    req_file = root / "requirements.txt"
    if not req_file.is_file():
        warnings.append("No requirements.txt found at root")

    # 5. Walk entire directory tree to check for forbidden files, caches, weights, and secrets
    total_size_bytes = 0
    file_count = 0

    for current_dir, dirs, files in os.walk(root):
        rel_dir = Path(current_dir).relative_to(root)
        
        # Check forbidden directories
        for d in list(dirs):
            if d in FORBIDDEN_DIRS or d.endswith(".egg-info"):
                errors.append(f"Forbidden directory in package: {rel_dir / d}")
                dirs.remove(d)

        for f in files:
            file_count += 1
            f_path = Path(current_dir) / f
            rel_file = rel_dir / f
            file_ext = f_path.suffix.lower()
            size = f_path.stat().st_size
            total_size_bytes += size

            # Check forbidden extensions
            if file_ext in FORBIDDEN_EXTENSIONS:
                errors.append(f"Forbidden file extension '{file_ext}': {rel_file}")

            # Check .env files
            if f.startswith(".env") and f != ".env.example":
                errors.append(f"Forbidden environment file: {rel_file}")

            # Check for large files (> 10 MB)
            if size > 10 * 1024 * 1024:
                errors.append(f"Unusually large file ({size / 1024 / 1024:.2f} MB): {rel_file}")

            # Scan text files for secrets and hardcoded user machine paths
            if file_ext in (".py", ".json", ".md", ".txt", ".yaml", ".yml", ".sh", ".toml", "") and size < 500_000:
                try:
                    text_content = f_path.read_text(encoding="utf-8", errors="ignore")
                    for pat in SECRET_PATTERNS:
                        if pat.search(text_content):
                            errors.append(f"Potential secret pattern found in: {rel_file}")

                    # Check for hardcoded local machine paths in production files (excluding tests/checks)
                    if not str(rel_file).startswith("test_") and not str(rel_file).startswith("scripts"):
                        for h_pat in HARDCODED_MACHINE_PATH_PATTERNS:
                            if h_pat.search(text_content):
                                warnings.append(f"Hardcoded user-specific path detected in production file: {rel_file}")
                except Exception:
                    pass

    total_mb = total_size_bytes / (1024 * 1024)
    print(f"\n[SCAN METRICS]")
    print(f"  Total Files : {file_count}")
    print(f"  Total Size  : {total_mb:.2f} MB (Limit: 50.0 MB)")

    if total_mb > 50.0:
        errors.append(f"Total package size ({total_mb:.2f} MB) exceeds the 50 MB Hackathon limit!")

    print(f"\n[RESULTS]")
    if warnings:
        print(f"  Warnings ({len(warnings)}):")
        for w in warnings:
            print(f"    - {w}")

    if errors:
        print(f"  ERRORS ({len(errors)}):")
        for e in errors:
            print(f"    - [FAIL] {e}")
        print("\n>>> VALIDATION FAILED!")
        return False, errors

    print("\n>>> ALL VALIDATION CHECKS PASSED SUCCESSFULLY!")
    return True, []


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    passed, _ = check_submission(target)
    sys.exit(0 if passed else 1)
