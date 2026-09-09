"""
The AI Brain's decision engine -- answers "what should this learner do
next?" across the whole platform, not just within the Tutor.

Two layers, same split as agents/planner.py already uses:

1. Deterministic pre-filter (no LLM, always available): find the
   learner's weakest skill, then the best-matching topic and resource for
   it via the shared skill tags. This alone is enough to drive the
   frontend -- it's cheap, fast, and works even if the LLM is down.

2. LLM rationale layer (optional, can fail gracefully): turns the
   pre-filter's pick into a short, encouraging, personalized sentence
   using the learner's profile (career goal, interests) -- same
   call_llm + fallback-string pattern as planner_node.

decide_next() is intentionally *read-only* -- it doesn't write memory,
mastery, or roadmap state. Callers (the API layer, or roadmap.py's
simulation loop) decide what to do with its answer.
"""

from .. import knowledge_base
from .. import llm_client
from .. import memory_store
from .. import profile_store
from .. import resource_store
from .. import skills as skills_taxonomy
from . import skill_graph

# When a learner has no scored skills at all yet (brand-new account),
# there's nothing for the weakest-skill pre-filter to rank -- send them
# to onboarding/profile completion instead of guessing.
ACTION_ONBOARD = "onboard"
ACTION_LEARN = "learn"           # go to the Tutor for a topic
ACTION_RESOURCE = "resource"     # go to the Resources page for material
ACTION_QUIZ = "quiz"             # go check mastery on a topic already studied
ACTION_ADVANCE = "advance"       # every skill is solid -- nothing urgent to fix


def _fallback_reason(topic_title: str, skill_label: str) -> str:
    return f"{topic_title} is a great next step to build up your {skill_label} skills."


def pick_topic_and_action(memory: dict, weak_skill: str, subject_id: str = None) -> tuple:
    """The deterministic core: given a mastery dict and a target skill,
    pick the best topic to study next and whether that means learning it
    fresh or just confirming mastery with a quiz. Pulled out of
    decide_next() so brain/roadmap.py can reuse the exact same ranking
    logic against a *simulated* mastery dict, instead of drifting out of
    sync with a re-implementation."""
    candidate_topics = knowledge_base.topics_for_skill(weak_skill, subject_id)
    if not candidate_topics:
        return None, ACTION_ADVANCE

    untried = [
        t for t in candidate_topics
        if memory["mastery"].get(t["id"], {}).get("quiz_attempts", 0) == 0
        and not memory["mastery"].get(t["id"], {}).get("code_scores")
    ]
    ranked = sorted(
        untried or candidate_topics,
        key=lambda t: memory_store.mastery_percent(memory, t["id"]),
    )
    topic = ranked[0]
    topic_mastery = memory_store.mastery_percent(memory, topic["id"])
    action = ACTION_QUIZ if topic_mastery >= 70 else ACTION_LEARN
    return topic, action


def decide_next(student_id: str, subject_id: str = None) -> dict:
    profile = profile_store.load_profile(student_id)

    if not profile.get("onboarded"):
        return {
            "action": ACTION_ONBOARD,
            "module": "profile",
            "reason": "Let's start by learning a bit about you and your goals.",
        }

    weak_skill = skill_graph.weakest_skill(student_id, subject_id)

    if weak_skill is None:
        # No skill has any content in the knowledge base / resources yet
        # for this subject scope -- nothing meaningful to recommend.
        return {
            "action": ACTION_ADVANCE,
            "module": None,
            "reason": "There's nothing to assess yet -- try a quiz or coding task to get started.",
        }

    levels = skill_graph.skill_levels(student_id, subject_id)
    skill_label = _skill_label(weak_skill)

    memory = memory_store.load_memory(student_id)
    topic, action = pick_topic_and_action(memory, weak_skill, subject_id)

    if topic is None:
        return {
            "action": ACTION_ADVANCE,
            "module": None,
            "skill_target": weak_skill,
            "reason": f"No content is tagged for {skill_label} yet -- nothing to recommend there.",
        }

    resources = resource_store.resources_for_skill(weak_skill, subject_id)
    resource = resources[0] if resources else None

    try:
        system = (
            "You are a concise, encouraging coach helping someone build "
            "job-ready skills. Keep replies to one short sentence."
        )
        goal_clause = f' toward their goal of becoming a {profile["career_goal"]}' if profile.get("career_goal") else ""
        user = (
            f"The learner's skill levels (0-100) are: "
            f"{', '.join(f'{_skill_label(s)}: {v}' for s, v in levels.items())}. "
            f'Their weakest is {skill_label} ({levels[weak_skill]}%). '
            f'In one short encouraging sentence, tell them why studying '
            f'"{topic["title"]}" next is a good move{goal_clause}.'
        )
        reason = llm_client.call_llm(system, user)
    except RuntimeError:
        reason = _fallback_reason(topic["title"], skill_label)

    return {
        "action": action,
        "module": "quiz" if action == ACTION_QUIZ else "tutor",
        "skill_target": weak_skill,
        "skill_label": skill_label,
        "skill_level": levels[weak_skill],
        "topic_id": topic["id"],
        "topic_title": topic["title"],
        "subject_id": topic.get("subject_id"),
        "resource_id": resource["id"] if resource else None,
        "resource_title": resource["title"] if resource else None,
        "reason": reason,
    }


def _skill_label(skill_id: str) -> str:
    return skills_taxonomy.SKILLS.get(skill_id, skill_id)
