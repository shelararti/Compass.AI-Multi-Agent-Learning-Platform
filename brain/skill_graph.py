"""
Rolls per-topic mastery (memory_store.py) up into per-skill readiness,
using the skill tags added to knowledge_base topics. This is the piece
that turns "78% quiz score on py-functions" into "Python: 82%" -- the
progress-bar view the product brief asks for, instead of a bare mark.

Deliberately no LLM call here: this is pure aggregation over data that
already exists, so it's cheap, deterministic, and testable without a
model running. The Brain's decision_engine (next) is where LLM reasoning
comes in, grounded in these numbers rather than guessing at them.
"""

from .. import knowledge_base
from .. import memory_store
from .. import skills as skills_taxonomy


def skill_levels(student_id: str, subject_id: str = None, memory: dict = None) -> dict:
    """{"python": 82, "communication": 45, ...} for every skill that has
    at least one tagged topic. A skill with zero tagged topics anywhere
    in the knowledge base is omitted rather than reported as 0 -- there's
    a real difference between "untested" and "no content exists yet",
    and collapsing them would make an untaught skill look like a weakness.

    `memory` lets a caller pass a simulated mastery dict instead of the
    persisted one (used by brain/roadmap.py to project future skill
    levels without touching real student data)."""
    memory = memory if memory is not None else memory_store.load_memory(student_id)
    levels = {}
    for skill_id in skills_taxonomy.SKILLS:
        topics = knowledge_base.topics_for_skill(skill_id, subject_id)
        if not topics:
            continue
        scores = [memory_store.mastery_percent(memory, t["id"]) for t in topics]
        levels[skill_id] = round(sum(scores) / len(scores))
    return levels


def skill_levels_labeled(student_id: str, subject_id: str = None) -> list:
    """Same data as skill_levels(), shaped for direct frontend rendering
    (ordered list with display labels, instead of a dict the frontend
    has to re-sort and re-label itself)."""
    levels = skill_levels(student_id, subject_id)
    return [
        {"skill_id": sid, "label": skills_taxonomy.SKILLS[sid], "level": pct}
        for sid, pct in sorted(levels.items(), key=lambda kv: kv[0])
    ]


def employment_readiness(student_id: str, subject_id: str = None) -> int:
    """Single headline number for the dashboard: the average across every
    skill that currently has data. Returns 0 (not None) when nothing has
    been attempted yet, since the frontend's progress ring needs a number
    to render even for a brand-new learner."""
    levels = skill_levels(student_id, subject_id)
    if not levels:
        return 0
    return round(sum(levels.values()) / len(levels))


def weakest_skill(student_id: str, subject_id: str = None, memory: dict = None) -> str:
    """The skill_id with the lowest score, or None if the learner has no
    scored skills at all yet (brand-new account) -- callers like
    decision_engine.py need to handle that case explicitly rather than
    assume a weakest skill always exists."""
    levels = skill_levels(student_id, subject_id, memory=memory)
    if not levels:
        return None
    return min(levels, key=levels.get)
