"""
Canonical skill taxonomy.

This is the vocabulary that ties everything together: topics (Tutor),
resources (Resources page), and eventually visualizations all get tagged
with one or more of these skill ids. The Brain layer (brain/skill_graph.py)
rolls per-topic mastery up into per-skill readiness using these tags, which
is what turns "78% quiz score" into "Python ████████░░ 82%".

Deliberately a flat, fixed list (not free-text) -- same reasoning as
resource_store.TAGS: free-text tags fragment into near-duplicates
("python" vs "Python basics" vs "python-programming") and stop being
aggregatable. Add new skills here first, then tag content with them.
"""

SKILLS = {
    "python": "Python",
    "ai_ml": "AI & LLMs",
    "data": "Data & SQL",
    "problem_solving": "Problem Solving",
    "communication": "Communication",
    "digital_literacy": "Digital Literacy",
}


def is_valid_skill(skill_id: str) -> bool:
    return skill_id in SKILLS


def validate_skills(skill_ids: list) -> list:
    """Filter out unrecognized skill ids rather than raising, so a caller
    tagging content with one bad id doesn't lose the whole list -- mirrors
    how knowledge_base/resource_store degrade gracefully on bad input
    elsewhere in this codebase."""
    return [s for s in (skill_ids or []) if is_valid_skill(s)]
