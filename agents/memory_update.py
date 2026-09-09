"""Progress & Memory node -- takes the Evaluation Agent's output and folds
it into the student's persisted mastery record."""

from .. import memory_store
from ..brain import roadmap as brain_roadmap


def memory_update_node(state: dict) -> dict:
    if state.get("error"):
        return {}

    student_id = state.get("student_id", "default")
    topic_id = state.get("topic_id")
    subject_id = state.get("subject_id")
    evaluation = state.get("evaluation")
    memory = memory_store.load_memory(student_id)

    if evaluation and topic_id:
        kind = evaluation.get("details", {}).get("kind")
        if kind == "quiz":
            details = evaluation["details"]
            memory_store.record_quiz_result(
                memory, topic_id, details["correct"], details["total"]
            )
        elif kind == "code":
            memory_store.record_code_score(memory, topic_id, evaluation["score"])

    memory_store.save_memory(student_id, memory)

    # Regenerate the Brain's roadmap against the mastery we just wrote,
    # so the "AI Brain Updates Profile" step in the product flow actually
    # happens here rather than only on the next explicit request. Best
    # effort -- a roadmap hiccup shouldn't fail the evaluation the
    # student is waiting on.
    try:
        brain_roadmap.update_after_evaluation(student_id, subject_id)
    except Exception:
        pass

    return {"memory": memory}
