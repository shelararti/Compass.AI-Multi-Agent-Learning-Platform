"""
Progress & Memory persistence.

One JSON file per student, keyed by student_id. This is intentionally a
thin file-backed store for now -- the FastAPI layer can swap this out for
a real database later without agents needing to change, since they only
ever see a plain memory dict.
"""

import json
import os

from . import config
from . import knowledge_base


def _path_for(student_id: str) -> str:
    os.makedirs(config.MEMORY_DIR, exist_ok=True)
    safe_id = "".join(c for c in student_id if c.isalnum() or c in ("-", "_")) or "default"
    return os.path.join(config.MEMORY_DIR, f"{safe_id}.json")


def default_memory() -> dict:
    return {
        "mastery": {
            t["id"]: {"quiz_attempts": 0, "quiz_correct": 0, "code_scores": []}
            for t in knowledge_base.all_topics()
        },
        "last_topic": None,
    }


def load_memory(student_id: str) -> dict:
    path = _path_for(student_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # backfill mastery entries for any topics added since last save
            # (e.g. new topics from a PDF upload)
            for t in knowledge_base.all_topics():
                data.setdefault("mastery", {}).setdefault(
                    t["id"], {"quiz_attempts": 0, "quiz_correct": 0, "code_scores": []}
                )
            return data
        except (json.JSONDecodeError, KeyError):
            pass
    return default_memory()


def save_memory(student_id: str, memory: dict) -> None:
    with open(_path_for(student_id), "w") as f:
        json.dump(memory, f, indent=2)


def mastery_percent(memory: dict, topic_id: str) -> int:
    m = memory["mastery"].get(
        topic_id, {"quiz_attempts": 0, "quiz_correct": 0, "code_scores": []}
    )
    parts = []
    if m["quiz_attempts"] > 0:
        parts.append(100 * m["quiz_correct"] / m["quiz_attempts"])
    if m["code_scores"]:
        parts.append(sum(m["code_scores"]) / len(m["code_scores"]))
    if not parts:
        return 0
    return round(sum(parts) / len(parts))


def record_quiz_result(memory: dict, topic_id: str, correct: int, total: int) -> None:
    m = memory["mastery"].setdefault(
        topic_id, {"quiz_attempts": 0, "quiz_correct": 0, "code_scores": []}
    )
    m["quiz_attempts"] += total
    m["quiz_correct"] += correct
    memory["last_topic"] = topic_id


def record_code_score(memory: dict, topic_id: str, score: float) -> None:
    # Clamp defensively -- a model-graded score should be 0-100, but nothing
    # currently enforces that upstream.
    score = max(0, min(100, score))
    m = memory["mastery"].setdefault(
        topic_id, {"quiz_attempts": 0, "quiz_correct": 0, "code_scores": []}
    )
    m["code_scores"].append(score)
    memory["last_topic"] = topic_id
