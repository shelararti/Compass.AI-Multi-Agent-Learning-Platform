"""
FastAPI layer over the tutor agent graph.

Two things this layer is responsible for that the graph itself doesn't do:

1. **Session caching for quiz/code attempts.** The graph's evaluation step
   needs the full quiz (with correct_index) or the full rubric to grade
   against. We never want that sent to the browser before grading -- a
   student could just read the answers out of the network tab. So
   /quiz/start and /code/start cache the full generated content server-side
   under an attempt_id, and only return a *sanitized* version (no answers)
   to the client. /quiz/submit and /code/submit look the attempt back up
   by id rather than trusting whatever the client sends back.

2. **Serving the frontend.** The static frontend is mounted here too, so
   the whole thing runs as one process with no CORS setup needed.
"""

import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .graph import tutor_graph
from . import config
from . import knowledge_base
from . import memory_store
from . import pdf_ingest
from . import vector_store
from . import visualize_api
from . import resources_api
from . import wellbeing_api
from .brain import api as brain_api

# Absolute path so the frontend is found regardless of the working
# directory uvicorn was launched from.
FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

app = FastAPI(title="Agentic AI & LLMs Tutor")

# In-memory attempt cache: attempt_id -> full server-side record.
# Fine for a single-process dev server; swap for Redis/DB before scaling
# past one process or across restarts.
ATTEMPTS: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    student_id: str
    level: str = "beginner"
    subject_id: Optional[str] = None
    topic_id: Optional[str] = None
    question: str


class StartRequest(BaseModel):
    student_id: str
    level: str = "beginner"
    subject_id: Optional[str] = None
    topic_id: str


class QuizSubmitRequest(BaseModel):
    attempt_id: str
    answers: list[int]


class CodeSubmitRequest(BaseModel):
    attempt_id: str
    submission: str


# ---------------------------------------------------------------------------
# Subjects, topics & progress
# ---------------------------------------------------------------------------
@app.get("/api/subjects")
def list_subjects():
    return [
        {
            "id": s["id"],
            "title": s["title"],
            "description": s.get("description", ""),
            "source": s.get("source", "builtin"),
            "rag": s.get("rag", False),
            "topic_count": len(s["topics"]),
        }
        for s in knowledge_base.all_subjects()
    ]


@app.post("/api/subjects/upload")
async def upload_subject(file: UploadFile = File(...), title: Optional[str] = Form(None)):
    filename = file.filename or "uploaded.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF file.")

    file_bytes = await file.read()
    subject_title = (title or Path(filename).stem or "Uploaded course").strip() or "Uploaded course"

    base_id = re.sub(r"[^a-z0-9]+", "-", subject_title.lower()).strip("-") or "uploaded"
    subject_id = base_id
    suffix = 2
    while knowledge_base.get_subject(subject_id):
        subject_id = f"{base_id}-{suffix}"
        suffix += 1

    try:
        topics, chunks = pdf_ingest.pdf_to_course(file_bytes, subject_id)
    except ValueError as e:
        raise HTTPException(400, str(e))

    rag_ok = vector_store.build_index(subject_id, chunks)

    subject = knowledge_base.add_subject(
        subject_id,
        subject_title,
        f"Uploaded from {filename}.",
        topics,
        source="uploaded",
        rag=rag_ok,
    )
    response = {
        "subject": {
            "id": subject["id"],
            "title": subject["title"],
            "description": subject["description"],
            "source": subject["source"],
            "rag": subject["rag"],
            "topic_count": len(subject["topics"]),
        },
        "topics": [{"id": t["id"], "title": t["title"]} for t in topics],
    }
    if not rag_ok:
        response["note"] = (
            f"Couldn't reach the '{config.EMBED_MODEL_NAME}' embedding model on Ollama, "
            "so this subject is using section-based grounding instead of full retrieval. "
            f"Run `ollama pull {config.EMBED_MODEL_NAME}` and re-upload for retrieval-grounded answers."
        )
    return response


@app.get("/api/topics")
def list_topics(subject_id: Optional[str] = None):
    if subject_id and not knowledge_base.get_subject(subject_id):
        raise HTTPException(404, f"Unknown subject_id: {subject_id}")
    return [{"id": t["id"], "title": t["title"]} for t in knowledge_base.all_topics(subject_id)]


@app.get("/api/progress")
def get_progress(student_id: str, level: str = "beginner", subject_id: Optional[str] = None):
    if subject_id and not knowledge_base.get_subject(subject_id):
        raise HTTPException(404, f"Unknown subject_id: {subject_id}")

    result = tutor_graph.invoke(
        {
            "student_id": student_id,
            "level": level,
            "stage": "generate",
            "intent": "progress",
            "subject_id": subject_id,
        }
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])

    memory = result["memory"]
    topics = knowledge_base.all_topics(subject_id) if subject_id else knowledge_base.all_topics()
    mastery = [
        {
            "topic_id": t["id"],
            "title": t["title"],
            "percent": memory_store.mastery_percent(memory, t["id"]),
        }
        for t in topics
    ]
    return {
        "mastery": mastery,
        "next_topic_suggestion": result.get("next_topic_suggestion"),
        "planner_reason": result.get("planner_reason"),
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
@app.post("/api/chat")
def chat(req: ChatRequest):
    result = tutor_graph.invoke(
        {
            "student_id": req.student_id,
            "level": req.level,
            "stage": "generate",
            "intent": "chat",
            "subject_id": req.subject_id,
            "topic_id": req.topic_id,
            "student_input": req.question,
        }
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])
    return {
        "topic_id": result["topic_id"],
        "topic_title": result["topic_title"],
        "reply": result["chat_reply"],
        "sources": result.get("context_sources"),
    }


# ---------------------------------------------------------------------------
# Quiz
# ---------------------------------------------------------------------------
@app.post("/api/quiz/start")
def quiz_start(req: StartRequest):
    result = tutor_graph.invoke(
        {
            "student_id": req.student_id,
            "level": req.level,
            "stage": "generate",
            "intent": "quiz",
            "subject_id": req.subject_id,
            "topic_id": req.topic_id,
        }
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])

    questions = result["quiz"]["questions"]
    attempt_id = str(uuid.uuid4())
    ATTEMPTS[attempt_id] = {
        "kind": "quiz",
        "student_id": req.student_id,
        "topic_id": result["topic_id"],
        "questions": questions,
    }

    # Sanitized: strip correct_index and explanation so the browser never
    # sees the answer key before grading.
    sanitized = [
        {"question": q["question"], "options": q["options"]} for q in questions
    ]
    return {
        "attempt_id": attempt_id,
        "topic_title": result["topic_title"],
        "questions": sanitized,
        "sources": result.get("context_sources"),
    }


@app.post("/api/quiz/submit")
def quiz_submit(req: QuizSubmitRequest):
    attempt = ATTEMPTS.get(req.attempt_id)
    if not attempt or attempt["kind"] != "quiz":
        raise HTTPException(404, "Unknown or expired quiz attempt.")

    result = tutor_graph.invoke(
        {
            "student_id": attempt["student_id"],
            "stage": "evaluate",
            "topic_id": attempt["topic_id"],
            "quiz_questions": attempt["questions"],
            "quiz_answers": req.answers,
        }
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])

    del ATTEMPTS[req.attempt_id]
    return {
        "evaluation": result["evaluation"],
        "next_topic_suggestion": result.get("next_topic_suggestion"),
        "planner_reason": result.get("planner_reason"),
    }


# ---------------------------------------------------------------------------
# Coding task
# ---------------------------------------------------------------------------
@app.post("/api/code/start")
def code_start(req: StartRequest):
    result = tutor_graph.invoke(
        {
            "student_id": req.student_id,
            "level": req.level,
            "stage": "generate",
            "intent": "code",
            "subject_id": req.subject_id,
            "topic_id": req.topic_id,
        }
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])

    task = result["code_task"]
    attempt_id = str(uuid.uuid4())
    ATTEMPTS[attempt_id] = {
        "kind": "code",
        "student_id": req.student_id,
        "topic_id": result["topic_id"],
        "task": task["task"],
        "rubric": task["rubric"],
    }
    return {
        "attempt_id": attempt_id,
        "topic_title": result["topic_title"],
        "task": task["task"],
        "rubric": task["rubric"],
        "sources": result.get("context_sources"),
    }


@app.post("/api/code/submit")
def code_submit(req: CodeSubmitRequest):
    attempt = ATTEMPTS.get(req.attempt_id)
    if not attempt or attempt["kind"] != "code":
        raise HTTPException(404, "Unknown or expired code attempt.")

    result = tutor_graph.invoke(
        {
            "student_id": attempt["student_id"],
            "stage": "evaluate",
            "topic_id": attempt["topic_id"],
            "code_task_description": attempt["task"],
            "code_rubric": attempt["rubric"],
            "code_submission": req.submission,
        }
    )
    if result.get("error"):
        raise HTTPException(400, result["error"])

    del ATTEMPTS[req.attempt_id]
    return {
        "evaluation": result["evaluation"],
        "next_topic_suggestion": result.get("next_topic_suggestion"),
        "planner_reason": result.get("planner_reason"),
    }


# ---------------------------------------------------------------------------
# Frontend static files
# ---------------------------------------------------------------------------
app.include_router(visualize_api.router)
app.include_router(resources_api.router)
app.include_router(wellbeing_api.router)
app.include_router(brain_api.router)

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/visualize")
def visualize_page():
    return FileResponse(str(FRONTEND_DIR / "visualize.html"))


@app.get("/resources")
def resources_page():
    return FileResponse(str(FRONTEND_DIR / "resources.html"))


@app.get("/wellbeing")
def wellbeing_page():
    return FileResponse(str(FRONTEND_DIR / "wellbeing.html"))
