"""
Orchestrator Agent.

Two jobs:
1. On the "generate" stage: work out which specialist (teacher/quiz/coding)
   should handle this turn, and which course topic it's grounded in.
2. On the "evaluate" stage: nothing to resolve -- just hand straight to the
   Evaluation Agent, since the topic was already fixed during generation.

Intent resolution prefers an explicit hint from the client (a UI button:
"Chat" / "Quiz me" / "Give me a coding task" / "My progress") over guessing.
Guessing is a last-resort fallback for a bare free-text question.
"""

from .. import knowledge_base
from .. import vector_store

VALID_INTENTS = {"chat", "quiz", "code", "progress"}


def orchestrator_node(state: dict) -> dict:
    if state.get("stage") == "evaluate":
        # Topic was already resolved during generation; nothing to do here.
        return {}

    intent = state.get("intent")
    if intent not in VALID_INTENTS:
        # No explicit hint -- default to chat for free-form questions.
        intent = "chat"

    if intent == "progress":
        # Progress doesn't need a single topic; the Planner looks across all of them.
        return {"resolved_intent": intent}

    topic_id = state.get("topic_id")
    if topic_id:
        topic = knowledge_base.get_topic(topic_id)
        if topic is None:
            return {"error": f"Unknown topic_id: {topic_id}"}
    else:
        # Fall back to matching the free-text question against course notes,
        # scoped to the selected subject when one was given.
        topic = knowledge_base.pick_topic(state.get("student_input", ""), state.get("subject_id"))

    subject = knowledge_base.get_subject(topic.get("subject_id")) if topic.get("subject_id") else None
    subject_title = subject["title"] if subject else "your course"

    topic_notes = topic["notes"]
    context_sources = None
    if subject and subject.get("rag") and vector_store.has_index(subject["id"]):
        # Real retrieval: embed the question (or the topic title, when
        # there's no free-text question yet, e.g. quiz/code generation)
        # and pull the most relevant chunks from across the whole PDF,
        # not just whatever section the topic happens to be.
        query = state.get("student_input") or topic["title"]
        retrieved = vector_store.search(subject["id"], query)
        if retrieved:
            topic_notes = vector_store.format_context(retrieved)
            context_sources = sorted({c.get("topic_title", "Excerpt") for c in retrieved})

    return {
        "resolved_intent": intent,
        "topic_id": topic["id"],
        "topic_title": topic["title"],
        "topic_notes": topic_notes,
        "subject_id": topic.get("subject_id"),
        "subject_title": subject_title,
        "context_sources": context_sources,
    }


def route_after_orchestrator(state: dict) -> str:
    if state.get("error"):
        return "end"
    if state.get("stage") == "evaluate":
        return "evaluation_agent"
    intent = state.get("resolved_intent", "chat")
    return {
        "chat": "teacher_agent",
        "quiz": "quiz_agent",
        "code": "coding_agent",
        "progress": "planner_agent",
    }.get(intent, "teacher_agent")
