"""
Resources persistence -- students upload/link study material (PDFs, slide
decks, videos, YouTube links, ...) and other students rate + classify it.

Same tradeoff as memory_store.py: one JSON file per resource, file-backed
for now, swap for a real DB later without the API layer needing to change.
Unlike the Visualize page's in-memory JOBS, this data is meant to persist
across restarts -- it's shared, student-contributed content, not a
transient processing job.
"""

import json
import os
import time
import uuid

from . import config
from . import skills as skills_taxonomy

FORMATS = ["pdf", "ppt", "doc", "video", "youtube", "link", "other"]

# Formats that arrive as an uploaded file vs. an external URL.
UPLOAD_FORMATS = {"pdf", "ppt", "doc", "video", "other"}
LINK_FORMATS = {"youtube", "link"}

FORMAT_EXTENSIONS = {
    "pdf": (".pdf",),
    "ppt": (".ppt", ".pptx"),
    "doc": (".doc", ".docx"),
    "video": (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"),
    # "other" accepts any extension.
}

# Fixed tag vocabulary classifiers pick from, so aggregation is meaningful
# (free-text tags would fragment into near-duplicates across students).
TAGS = [
    "beginner-friendly",
    "exam-prep",
    "deep-dive",
    "visual-heavy",
    "practice-problems",
    "concise-summary",
    "well-explained",
    "outdated",
]

# Structured evaluation axes -- captured alongside the overall star rating
# so the compare table can show *why* one resource beats another, not just
# a single aggregate score.
AXES = {
    "clarity": "Clarity",
    "depth": "Depth",
    "usefulness": "Practical usefulness",
    "accuracy": "Accuracy",
}


def _safe_id(raw: str) -> str:
    return "".join(c for c in raw if c.isalnum() or c in ("-", "_")) or "default"


def _meta_path(resource_id: str) -> str:
    os.makedirs(config.RESOURCES_DIR, exist_ok=True)
    return os.path.join(config.RESOURCES_DIR, f"{_safe_id(resource_id)}.json")


def _file_dir(resource_id: str) -> str:
    d = os.path.join(config.UPLOADS_DIR, "resources", _safe_id(resource_id))
    os.makedirs(d, exist_ok=True)
    return d


def create_resource(
    *, title: str, description: str, subject_id, fmt: str, student_id: str,
    file_path: str = None, url: str = None, skills: list = None,
) -> dict:
    resource = {
        "id": uuid.uuid4().hex[:12],
        "title": title,
        "description": description,
        "subject_id": subject_id,
        "format": fmt,
        "uploaded_by": student_id,
        "uploaded_at": time.time(),
        "file_path": file_path,
        "url": url,
        "skills": skills_taxonomy.validate_skills(skills),  # for Brain recommendations
        "ratings": [],          # [{student_id, stars, comment, at}]
        "classifications": [],  # [{student_id, level, tags, at}]
    }
    save_resource(resource)
    return resource


def save_resource(resource: dict) -> None:
    with open(_meta_path(resource["id"]), "w") as f:
        json.dump(resource, f, indent=2)


def get_resource(resource_id: str) -> dict:
    path = _meta_path(resource_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def list_resources() -> list:
    if not os.path.isdir(config.RESOURCES_DIR):
        return []
    resources = []
    for name in os.listdir(config.RESOURCES_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(config.RESOURCES_DIR, name), "r") as f:
                resources.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return resources


def resources_for_skill(skill_id: str, subject_id=None) -> list:
    """Resources tagged with the given skill id, used by the Brain layer's
    recommendation engine. Resources with no 'skills' key (e.g. anything
    created before this field existed) are treated as untagged rather than
    raising -- same graceful-degradation approach as get_topic/all_topics
    in knowledge_base.py."""
    matches = [r for r in list_resources() if skill_id in r.get("skills", [])]
    if subject_id:
        matches = [r for r in matches if r.get("subject_id") == subject_id]
    return matches


def add_rating(resource_id: str, student_id: str, stars: int, comment: str = "", axes: dict = None) -> dict:
    resource = get_resource(resource_id)
    if resource is None:
        raise KeyError(resource_id)
    if not isinstance(stars, int) or not (1 <= stars <= 5):
        raise ValueError("stars must be an integer from 1 to 5.")

    axes = axes or {}
    bad_axes = [a for a in axes if a not in AXES]
    if bad_axes:
        raise ValueError(f"unrecognized axis/axes: {', '.join(bad_axes)}. Choose from: {', '.join(AXES)}")
    for a, v in axes.items():
        if not isinstance(v, int) or not (1 <= v <= 5):
            raise ValueError(f"axis '{a}' must be an integer from 1 to 5.")

    entry = {
        "student_id": student_id, "stars": stars, "comment": comment.strip(),
        "axes": axes, "at": time.time(),
    }
    ratings = [r for r in resource["ratings"] if r["student_id"] != student_id]
    ratings.append(entry)
    resource["ratings"] = ratings
    save_resource(resource)
    return resource


def add_classification(resource_id: str, student_id: str, level: str, tags: list) -> dict:
    resource = get_resource(resource_id)
    if resource is None:
        raise KeyError(resource_id)
    if level not in config.LEVELS:
        raise ValueError(f"level must be one of: {', '.join(config.LEVELS)}")
    bad_tags = [t for t in tags if t not in TAGS]
    if bad_tags:
        raise ValueError(f"unrecognized tag(s): {', '.join(bad_tags)}. Choose from: {', '.join(TAGS)}")

    entry = {"student_id": student_id, "level": level, "tags": tags, "at": time.time()}
    classifications = [c for c in resource["classifications"] if c["student_id"] != student_id]
    classifications.append(entry)
    resource["classifications"] = classifications
    save_resource(resource)
    return resource


def summarize(resource: dict) -> dict:
    """Derived, read-only aggregates -- computed on read rather than stored,
    so they're never stale relative to the raw ratings/classifications."""
    ratings = resource["ratings"]
    avg_rating = round(sum(r["stars"] for r in ratings) / len(ratings), 2) if ratings else None

    axis_averages = {}
    for axis in AXES:
        vals = [r["axes"][axis] for r in ratings if r.get("axes", {}).get(axis) is not None]
        axis_averages[axis] = round(sum(vals) / len(vals), 2) if vals else None

    tag_counts: dict = {}
    level_counts: dict = {}
    for c in resource["classifications"]:
        level_counts[c["level"]] = level_counts.get(c["level"], 0) + 1
        for t in c["tags"]:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top_level = max(level_counts, key=level_counts.get) if level_counts else None

    return {
        "average_rating": avg_rating,
        "rating_count": len(ratings),
        "axis_averages": axis_averages,
        "tag_counts": tag_counts,
        "classification_count": len(resource["classifications"]),
        "consensus_level": top_level,
    }
