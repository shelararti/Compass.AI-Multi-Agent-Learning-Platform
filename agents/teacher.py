"""Teacher Agent -- answers free-form student questions, grounded in the
resolved topic's course notes."""

from .. import llm_client


def teacher_node(state: dict) -> dict:
    level = state.get("level", "beginner")
    topic_notes = state.get("topic_notes", "")
    topic_title = state.get("topic_title", "the course")
    subject_title = state.get("subject_title", "your course")
    question = state.get("student_input", "")

    system = (
        f"You are a patient, encouraging tutor for a course called "
        f"'{subject_title}'. Explain things at a {level} level. "
        f"Ground your answer in this course content and stay accurate to it: "
        f"{topic_notes} Keep answers to a few short paragraphs."
    )
    try:
        reply = llm_client.call_llm(system, question)
    except RuntimeError as e:
        return {"error": str(e)}

    return {"chat_reply": reply, "topic_title": topic_title}
