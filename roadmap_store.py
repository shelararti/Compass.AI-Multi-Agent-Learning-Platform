"""
Roadmap persistence -- one JSON file per student holding the Brain's
current ordered plan (a list of steps, each shaped like a decide_next()
result plus a "status"). Same file-backed tradeoff as the other _store
modules in this package.

The roadmap is a *cached plan*, not a source of truth -- skill_graph.py
and memory_store.py remain the ground truth for mastery. If this file
were deleted, brain/roadmap.generate_roadmap() can always rebuild it.
"""

import json
import os
import time

from . import config


def _path_for(student_id: str) -> str:
    os.makedirs(config.ROADMAP_DIR, exist_ok=True)
    safe_id = "".join(c for c in student_id if c.isalnum() or c in ("-", "_")) or "default"
    return os.path.join(config.ROADMAP_DIR, f"{safe_id}.json")


def load_roadmap(student_id: str) -> dict:
    path = _path_for(student_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"student_id": student_id, "steps": [], "generated_at": None}


def save_roadmap(student_id: str, steps: list) -> dict:
    roadmap = {"student_id": student_id, "steps": steps, "generated_at": time.time()}
    with open(_path_for(student_id), "w") as f:
        json.dump(roadmap, f, indent=2)
    return roadmap
