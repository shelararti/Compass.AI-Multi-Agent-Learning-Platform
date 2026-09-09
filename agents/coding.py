"""Coding Agent -- generates a short coding exercise with a rubric,
grounded in the resolved topic's course notes. Grading happens later in
the Evaluation Agent, once the client sends back the student's submission."""

from .. import llm_client


def coding_node(state: dict) -> dict:
    level = state.get("level", "beginner")
    topic_title = state.get("topic_title", "")
    topic_notes = state.get("topic_notes", "")
    subject_title = state.get("subject_title", "your course")

    system = (
        f"You design short hands-on coding exercises for a course on "
        f"{subject_title}. Respond with ONLY valid JSON, no markdown fences, no extra text."
    )

    user = (
        f'Create one coding exercise for "{topic_title}" at a {level} level, '
        f"grounded in: {topic_notes}\n"
        "Return this exact JSON shape:\n"
        '{"task":"clear description of what to build or write","rubric":'
        '["criterion 1","criterion 2","criterion 3","criterion 4"]}'
    )
    try:
        data = llm_client.call_llm_json(system, user)
    except Exception as e:
        return {"error": f"Could not generate a coding task: {e}"}

    return {"code_task": data}
