import sys
from unittest.mock import patch

sys.path.insert(0, "/home/claude")

from tutor_backend import config
config.MEMORY_DIR = "/tmp/tutor_memory_api_test"

import os
os.chdir("/home/claude/tutor_backend")  # so StaticFiles("frontend") resolves

from fastapi.testclient import TestClient
from tutor_backend.api import app


def fake_call_llm(system, user, timeout=120):
    return "Mocked explanation."


def fake_call_llm_json(system, user, attempts=3):
    if "quiz" in system.lower():
        return {
            "questions": [
                {"question": "Q1?", "options": ["A", "B", "C", "D"], "correct_index": 0, "explanation": "because A"},
                {"question": "Q2?", "options": ["A", "B", "C", "D"], "correct_index": 1, "explanation": "because B"},
            ]
        }
    if "grader" in system.lower():
        return {"score": 90, "strengths": ["good"], "improvements": ["more tests"], "feedback": "Nice work."}
    return {"task": "Write a chunker.", "rubric": ["r1", "r2"]}


def run():
    with patch("tutor_backend.llm_client.call_llm", side_effect=fake_call_llm), \
         patch("tutor_backend.llm_client.call_llm_json", side_effect=fake_call_llm_json):

        client = TestClient(app)

        print("=== GET / ===")
        r = client.get("/")
        assert r.status_code == 200 and "Tutor" in r.text
        print("ok")

        print("=== GET /static/style.css ===")
        r = client.get("/static/style.css")
        assert r.status_code == 200
        print("ok")

        print("=== GET /api/topics ===")
        r = client.get("/api/topics")
        assert r.status_code == 200
        topics = r.json()
        assert any(t["id"] == "rag" for t in topics)
        print(len(topics), "topics")

        print("=== POST /api/chat ===")
        r = client.post("/api/chat", json={"student_id": "bob", "level": "beginner", "topic_id": "rag", "question": "explain rag"})
        assert r.status_code == 200
        assert r.json()["reply"] == "Mocked explanation."
        print("ok")

        print("=== POST /api/quiz/start (sanitized -- no answers leaked) ===")
        r = client.post("/api/quiz/start", json={"student_id": "bob", "level": "beginner", "topic_id": "rag"})
        assert r.status_code == 200
        qdata = r.json()
        assert "correct_index" not in str(qdata), "leaked answer key to client!"
        attempt_id = qdata["attempt_id"]
        print("attempt_id:", attempt_id, "| questions sanitized: OK")

        print("=== POST /api/quiz/submit ===")
        r = client.post("/api/quiz/submit", json={"attempt_id": attempt_id, "answers": [0, 0]})
        assert r.status_code == 200
        ev = r.json()["evaluation"]
        assert ev["score"] == 50  # got Q1 right, Q2 wrong
        print("score:", ev["score"], "next:", r.json()["next_topic_suggestion"])

        print("=== replay same attempt_id should now fail (single use) ===")
        r = client.post("/api/quiz/submit", json={"attempt_id": attempt_id, "answers": [0, 0]})
        assert r.status_code == 404
        print("ok -- attempt correctly expired after grading")

        print("=== POST /api/code/start + submit ===")
        r = client.post("/api/code/start", json={"student_id": "bob", "level": "beginner", "topic_id": "rag"})
        assert r.status_code == 200
        cdata = r.json()
        r = client.post("/api/code/submit", json={"attempt_id": cdata["attempt_id"], "submission": "def f(): pass"})
        assert r.status_code == 200
        assert r.json()["evaluation"]["score"] == 90
        print("ok")

        print("=== GET /api/progress ===")
        r = client.get("/api/progress", params={"student_id": "bob", "level": "beginner"})
        assert r.status_code == 200
        pdata = r.json()
        rag_entry = next(m for m in pdata["mastery"] if m["topic_id"] == "rag")
        assert rag_entry["percent"] > 0
        print("rag mastery:", rag_entry["percent"], "%  next:", pdata["next_topic_suggestion"])

    print("\nALL API CHECKS PASSED")


if __name__ == "__main__":
    run()
