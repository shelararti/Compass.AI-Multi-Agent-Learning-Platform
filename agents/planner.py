"""Learning Planner Agent -- looks across the student's mastery record and
suggests what to study next, with a short one-line rationale."""

from .. import knowledge_base
from .. import memory_store
from .. import llm_client


def planner_node(state: dict) -> dict:
    if state.get("error"):
        return {}

    student_id = state.get("student_id", "default")
    memory = state.get("memory") or memory_store.load_memory(student_id)

    subject_id = state.get("subject_id")
    topics = knowledge_base.all_topics(subject_id) if subject_id else knowledge_base.all_topics()
    if not topics:
        topics = knowledge_base.all_topics()
    if not topics:
        return {"memory": memory}

    ranked = sorted(topics, key=lambda t: memory_store.mastery_percent(memory, t["id"]))
    weakest = ranked[0]

    summary = ", ".join(
        f"{t['title']}: {memory_store.mastery_percent(memory, t['id'])}" for t in topics
    )
    subject = knowledge_base.get_subject(subject_id) if subject_id else None
    subject_title = subject["title"] if subject else "their course"
    system = f"You are a concise, encouraging tutor for a course on {subject_title}."
    user = (
        f"The student's mastery by topic (0-100) is: {summary}. "
        f'In one short encouraging sentence, say why "{weakest["title"]}" '
        "is a good next topic to study."
    )
    try:
        reason = llm_client.call_llm(system, user)
    except RuntimeError:
        reason = "This is the topic with the most room to grow right now."

    return {
        "memory": memory,
        "next_topic_suggestion": weakest["id"],
        "planner_reason": reason,
    }
