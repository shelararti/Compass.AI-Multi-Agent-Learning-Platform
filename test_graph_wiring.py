"""
Smoke-tests the graph's routing and state-passing logic with the LLM calls
mocked out, so this can run without Ollama actually installed/running.
Once Ollama is available, the same graph works unmodified against the
real model.
"""

import sys
from unittest.mock import patch

sys.path.insert(0, "/home/claude")

from tutor_backend.graph import tutor_graph
from tutor_backend import memory_store, config

config.MEMORY_DIR = "/tmp/tutor_memory_test"


def fake_call_llm(system, user, timeout=120):
    return "This is a mocked tutor explanation grounded in the notes."


def fake_call_llm_json(system, user, attempts=3):
    if "quiz" in system.lower():
        return {
            "questions": [
                {
                    "question": "What does RAG stand for?",
                    "options": ["Retrieval-Augmented Generation", "Random Access Graph", "A", "B"],
                    "correct_index": 0,
                    "explanation": "RAG grounds generation in retrieved documents.",
                },
                {
                    "question": "What does chunking affect?",
                    "options": ["Retrieval quality", "Model weights", "C", "D"],
                    "correct_index": 0,
                    "explanation": "Chunking strategy affects what gets retrieved.",
                },
            ]
        }
    if "grader" in system.lower():
        return {
            "score": 85,
            "strengths": ["Correct chunking approach"],
            "improvements": ["Handle empty documents"],
            "feedback": "Solid first pass!",
        }
    return {"task": "Write a function that chunks text into 200-token windows.",
            "rubric": ["Handles edge cases", "Uses overlap", "Documented", "Tested"]}


def run():
    with patch("tutor_backend.llm_client.call_llm", side_effect=fake_call_llm), \
         patch("tutor_backend.llm_client.call_llm_json", side_effect=fake_call_llm_json):

        print("=== 1. CHAT generate ===")
        result = tutor_graph.invoke({
            "student_id": "alice",
            "level": "beginner",
            "stage": "generate",
            "intent": "chat",
            "student_input": "How does retrieval augmented generation work?",
        })
        assert result.get("resolved_intent") == "chat"
        assert result.get("topic_id") == "rag"
        assert "chat_reply" in result
        print("topic:", result["topic_title"])
        print("reply:", result["chat_reply"])

        print("\n=== 2. QUIZ generate ===")
        quiz_result = tutor_graph.invoke({
            "student_id": "alice",
            "level": "beginner",
            "stage": "generate",
            "intent": "quiz",
            "topic_id": "rag",
            "student_input": "",
        })
        assert quiz_result.get("resolved_intent") == "quiz"
        questions = quiz_result["quiz"]["questions"]
        assert len(questions) == 2
        print("generated", len(questions), "questions")

        print("\n=== 3. QUIZ evaluate ===")
        eval_result = tutor_graph.invoke({
            "student_id": "alice",
            "level": "beginner",
            "stage": "evaluate",
            "topic_id": "rag",
            "quiz_questions": questions,
            "quiz_answers": [0, 1],  # first correct, second wrong
        })
        assert eval_result["evaluation"]["score"] == 50
        assert eval_result["memory"]["mastery"]["rag"]["quiz_attempts"] == 2
        assert eval_result["memory"]["mastery"]["rag"]["quiz_correct"] == 1
        assert "next_topic_suggestion" in eval_result
        print("score:", eval_result["evaluation"]["score"])
        print("next topic suggestion:", eval_result["next_topic_suggestion"])
        print("planner reason:", eval_result["planner_reason"])

        print("\n=== 4. CODE generate + evaluate ===")
        code_gen = tutor_graph.invoke({
            "student_id": "alice",
            "level": "intermediate",
            "stage": "generate",
            "intent": "code",
            "topic_id": "rag",
            "student_input": "",
        })
        task = code_gen["code_task"]
        print("task:", task["task"])

        code_eval = tutor_graph.invoke({
            "student_id": "alice",
            "level": "intermediate",
            "stage": "evaluate",
            "topic_id": "rag",
            "code_task_description": task["task"],
            "code_rubric": task["rubric"],
            "code_submission": "def chunk(text): return [text[i:i+200] for i in range(0, len(text), 200)]",
        })
        assert code_eval["evaluation"]["score"] == 85
        assert code_eval["memory"]["mastery"]["rag"]["code_scores"] == [85]
        print("code score:", code_eval["evaluation"]["score"])

        print("\n=== 5. PROGRESS ===")
        progress_result = tutor_graph.invoke({
            "student_id": "alice",
            "level": "beginner",
            "stage": "generate",
            "intent": "progress",
            "student_input": "",
        })
        assert progress_result.get("resolved_intent") == "progress"
        assert "next_topic_suggestion" in progress_result
        print("suggested next:", progress_result["next_topic_suggestion"])

        print("\n=== 6. Unknown topic_id error path ===")
        err_result = tutor_graph.invoke({
            "student_id": "alice",
            "level": "beginner",
            "stage": "generate",
            "intent": "chat",
            "topic_id": "does-not-exist",
            "student_input": "hi",
        })
        assert err_result.get("error"), "expected an error for unknown topic_id"
        print("error surfaced correctly:", err_result["error"])

    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    run()
