"""
Learner Profile persistence.

One JSON file per student, same file-backed tradeoff as memory_store.py
(swap for a real DB later without callers changing). The distinction from
memory_store.py: memory is *derived* -- computed from quiz/code activity
the student didn't directly author. A profile is *declared* -- the learner
(or an onboarding flow) states it directly, and it changes rarely.

This is the "who is this person" input the Brain layer (brain/) reasons
over alongside "what have they mastered" (memory_store) and "what's
mastery worth in skill terms" (brain/skill_graph.py).
"""

import json
import os
import time

from . import config
from . import skills as skills_taxonomy

EDUCATION_LEVELS = ["none", "school", "diploma", "degree", "postgraduate"]

# Fixed vocabulary, same reasoning as resource_store.TAGS / skills.SKILLS --
# free-text would fragment ("communication" vs "comms" vs "Communication")
# and stop being usable by the Brain's rule-based pre-filter.
LANGUAGES = ["en", "hi", "mr", "es", "fr"]


def _path_for(student_id: str) -> str:
    os.makedirs(config.PROFILE_DIR, exist_ok=True)
    safe_id = "".join(c for c in student_id if c.isalnum() or c in ("-", "_")) or "default"
    return os.path.join(config.PROFILE_DIR, f"{safe_id}.json")


def default_profile(student_id: str) -> dict:
    now = time.time()
    return {
        "student_id": student_id,
        "education_level": None,
        "existing_skills": [],   # subset of skills.SKILLS ids the learner self-reports
        "interests": [],         # free text, shown back to the learner but not
                                  # used for hard filtering -- unlike existing_skills
                                  # and career_goal, it doesn't map to a fixed taxonomy
        "career_goal": None,     # free text, e.g. "backend developer"
        "preferred_language": "en",
        "onboarded": False,      # False until the learner completes the profile form;
                                  # the Brain can use this to route new users to
                                  # onboarding before anything else
        "created_at": now,
        "updated_at": now,
    }


def load_profile(student_id: str) -> dict:
    path = _path_for(student_id)
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # backfill any fields added to the schema since this profile
            # was last saved, same pattern as memory_store.load_memory's
            # mastery backfill
            for k, v in default_profile(student_id).items():
                data.setdefault(k, v)
            return data
        except (json.JSONDecodeError, KeyError):
            pass
    return default_profile(student_id)


def save_profile(student_id: str, profile: dict) -> dict:
    profile = dict(profile)
    profile["student_id"] = student_id
    profile["updated_at"] = time.time()
    profile.setdefault("created_at", profile["updated_at"])
    with open(_path_for(student_id), "w") as f:
        json.dump(profile, f, indent=2)
    return profile


def update_profile(student_id: str, **fields) -> dict:
    """Partial update -- merges `fields` onto the existing profile instead
    of requiring the caller to resend the whole thing, so a small edit
    (e.g. just career_goal) can't accidentally wipe the rest."""
    profile = load_profile(student_id)
    profile.update({k: v for k, v in fields.items() if v is not None})
    if "existing_skills" in fields and fields["existing_skills"] is not None:
        profile["existing_skills"] = skills_taxonomy.validate_skills(fields["existing_skills"])
    profile["onboarded"] = True
    return save_profile(student_id, profile)
