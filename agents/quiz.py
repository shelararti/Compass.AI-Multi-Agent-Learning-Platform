"""Quiz Agent -- generates a short multiple-choice quiz grounded in the
resolved topic's course notes. Grading happens later in the Evaluation
Agent, once the client sends back the student's chosen answers."""

from .. import llm_client


def quiz_node(state: dict) -> dict:
    level = state.get("level", "beginner")
    topic_title = state.get("topic_title", "")
    topic_notes = state.get("topic_notes", "")
    subject_title = state.get("subject_title", "your course")

    system = (
        f"You write short multiple-choice quizzes for a course on {subject_title}. "
        "Respond with ONLY valid JSON, no markdown fences, no extra text."
    )

    user = (
        f'Generate exactly 3 multiple-choice questions on "{topic_title}" '
        f"at a {level} level, grounded in this content: {topic_notes}\n"
        "Return this exact JSON shape:\n"
        '{"questions":[{"question":"...","options":["...","...","...","..."],'
        '"correct_index":0,"explanation":"..."}]}'
    )
    try:
        data = llm_client.call_llm_json(system, user)
        questions = data["questions"]
    except Exception as e:
        return {"error": f"Could not generate a quiz: {e}"}

    return {"quiz": {"questions": questions}}
