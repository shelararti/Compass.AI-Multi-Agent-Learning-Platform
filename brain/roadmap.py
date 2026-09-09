"""
Turns a single decide_next() call into the multi-step plan from the
product brief (Weak Area -> Recommended Topic -> Video -> Exercise ->
Mini Project -> Quiz).

Deliberately does NOT call the LLM once per step -- that would be N slow
calls to build one roadmap. Instead it reuses decision_engine's
deterministic pre-filter (pick_topic_and_action) against a *simulated*
copy of mastery data: each simulated step assumes the learner completes
that step and reaches passing mastery on it, then the next step is
picked against that projected state. This is fast, cheap, and only
touches real persisted data at the very end (never during simulation).
"""

import copy

from .. import knowledge_base
from .. import memory_store
from .. import profile_store
from .. import resource_store
from . import decision_engine
from . import skill_graph
from .. import skills as skills_taxonomy
from .. import roadmap_store

# Mastery a simulated "completed" step is assumed to reach -- deliberately
# not 100, since real learners rarely ace everything; 85 keeps the
# projection realistic while still being enough to move on.
SIMULATED_COMPLETION_MASTERY = 85

DEFAULT_STEP_COUNT = 4


def _simulate_completion(memory: dict, topic_id: str) -> None:
    """Mutates a (already-copied) memory dict in place so mastery_percent
    on this topic reflects a passing quiz result, without touching
    quiz_attempts/code_scores in a way that would look real if this dict
    were ever accidentally persisted."""
    m = memory["mastery"].setdefault(
        topic_id, {"quiz_attempts": 0, "quiz_correct": 0, "code_scores": []}
    )
    m["quiz_attempts"] += 1
    m["quiz_correct"] += SIMULATED_COMPLETION_MASTERY / 100


def generate_roadmap(student_id: str, subject_id: str = None, step_count: int = DEFAULT_STEP_COUNT) -> dict:
    """Builds a fresh roadmap and persists it (roadmap_store), returning
    the same shape load_roadmap() would. Call this after onboarding and
    after every evaluation (see update_after_evaluation)."""
    profile = profile_store.load_profile(student_id)
    if not profile.get("onboarded"):
        steps = [{
            "action": decision_engine.ACTION_ONBOARD,
            "module": "profile",
            "reason": "Complete your profile so the AI Brain can plan your roadmap.",
        }]
        return roadmap_store.save_roadmap(student_id, steps)

    memory = copy.deepcopy(memory_store.load_memory(student_id))
    steps = []
    seen_topic_ids = set()

    for _ in range(step_count):
        weak_skill = skill_graph.weakest_skill(student_id, subject_id, memory=memory)
        if weak_skill is None:
            break

        topic, action = decision_engine.pick_topic_and_action(memory, weak_skill, subject_id)
        if topic is None or topic["id"] in seen_topic_ids:
            # Either nothing left tagged for this skill, or the simulation
            # looped back to a topic already planned -- stop rather than
            # padding the roadmap with a repeat.
            break
        seen_topic_ids.add(topic["id"])

        resources = resource_store.resources_for_skill(weak_skill, subject_id)
        levels = skill_graph.skill_levels(student_id, subject_id, memory=memory)

        steps.append({
            "action": action,
            "module": "quiz" if action == decision_engine.ACTION_QUIZ else "tutor",
            "skill_target": weak_skill,
            "skill_label": skills_taxonomy.SKILLS.get(weak_skill, weak_skill),
            "skill_level": levels[weak_skill],
            "topic_id": topic["id"],
            "topic_title": topic["title"],
            "subject_id": topic.get("subject_id"),
            "resource_id": resources[0]["id"] if resources else None,
            "resource_title": resources[0]["title"] if resources else None,
            "status": "pending",
        })

        _simulate_completion(memory, topic["id"])

    if steps:
        steps[0]["status"] = "current"

    return roadmap_store.save_roadmap(student_id, steps)


def get_or_generate(student_id: str, subject_id: str = None) -> dict:
    """Read the cached roadmap; build one if none exists yet (e.g. first
    call after onboarding). Doesn't auto-regenerate a stale roadmap --
    that only happens explicitly via update_after_evaluation, so a
    learner's in-progress plan doesn't shuffle underneath them on every
    page load."""
    roadmap = roadmap_store.load_roadmap(student_id)
    if not roadmap["steps"]:
        roadmap = generate_roadmap(student_id, subject_id)
    return roadmap


def update_after_evaluation(student_id: str, subject_id: str = None) -> dict:
    """Called after memory_update writes real mastery (see graph.py) --
    regenerates the roadmap against the learner's actual new state."""
    return generate_roadmap(student_id, subject_id)
