"""
Root pytest configuration and import path resolver for Adobe Hackathon Round 3.
Guarantees unambiguous package and module resolution across all skill directories
without requiring brittle sys.path hacks or import-order tricks.
"""

import os
import sys

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))

SKILL_SCRIPT_DIRS = [
    ROOT_DIR,
    os.path.join(ROOT_DIR, "skills", "audit-orchestrator", "scripts"),
    os.path.join(ROOT_DIR, "skills", "crawl-render-audit", "scripts"),
    os.path.join(ROOT_DIR, "skills", "engagement-audit", "scripts"),
    os.path.join(ROOT_DIR, "skills", "multimodal-audit", "scripts"),
    os.path.join(ROOT_DIR, "skills", "freshness-corroboration-audit", "scripts"),
    os.path.join(ROOT_DIR, "skills", "visual-accessibility-audit", "scripts"),
]

for script_dir in SKILL_SCRIPT_DIRS:
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
