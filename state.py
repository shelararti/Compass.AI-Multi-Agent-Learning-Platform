"""
Shared state that flows through the LangGraph graph.

Every node reads what it needs and returns a partial dict of updates --
LangGraph merges those into the running state (last write wins per key).
Keeping one flat schema here, instead of each agent inventing its own
shape, is what lets the FastAPI layer serialize/deserialize state at the
edges without agent-specific glue code.
"""

from typing import Any, Optional, TypedDict


class TutorState(TypedDict, total=False):
    # --- input, set by the caller (FastAPI request) ---
    student_id: str
    level: str  # beginner | intermediate | advanced
    stage: str  # "generate" | "evaluate"
    intent: Optional[str]  # "chat" | "quiz" | "code" | "progress" (optional hint)
    student_input: str  # free-form question, or student's answer/submission
    topic_id: Optional[str]  # explicit topic pick from the client, if any
    subject_id: Optional[str]  # explicit subject scope from the client, if any

    # --- set by orchestrator ---
    resolved_intent: str
    topic_title: str
    topic_notes: str
    subject_title: str  # owning subject's title, for prompt phrasing
    context_sources: Optional[list]  # section labels of retrieved RAG chunks, if any

    # --- generation outputs ---
    chat_reply: str
    quiz: dict  # {"questions": [...]}
    code_task: dict  # {"task": "...", "rubric": [...]}

    # --- evaluation inputs (client sends these back on the "evaluate" stage) ---
    quiz_questions: list  # the quiz questions previously generated, for grading
    quiz_answers: list  # student's picked option indices
    code_task_description: str  # the task text previously generated
    code_rubric: list
    code_submission: str

    # --- evaluation outputs ---
    evaluation: dict  # normalized: {"score": int, "feedback": str, "details": {...}}

    # --- memory / planning ---
    memory: dict
    next_topic_suggestion: str
    planner_reason: str

    # --- error surface ---
    error: Optional[str]

    # scratch field agents can use freely without polluting typed keys
    extra: dict[str, Any]
