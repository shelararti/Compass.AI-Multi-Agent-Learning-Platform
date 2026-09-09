"""
Central configuration for the tutor backend.
Keeping this in one place means swapping models, endpoints, or storage
locations later doesn't require touching agent logic.
"""

import os
from pathlib import Path

OLLAMA_URL = os.environ.get("TUTOR_OLLAMA_URL", "http://localhost:11434/api/chat")
MODEL_NAME = os.environ.get("TUTOR_MODEL_NAME", "llama3.2:1b")

# Embedding model used to build a per-subject vector index for uploaded
# PDFs, so answers can be grounded in retrieved excerpts (RAG) instead of
# a whole fixed section. Must be pulled separately: `ollama pull nomic-embed-text`.
EMBED_MODEL_NAME = os.environ.get("TUTOR_EMBED_MODEL_NAME", "nomic-embed-text")
OLLAMA_EMBED_URL = os.environ.get(
    "TUTOR_OLLAMA_EMBED_URL", OLLAMA_URL.replace("/api/chat", "/api/embeddings")
)
RAG_TOP_K = int(os.environ.get("TUTOR_RAG_TOP_K", "4"))
RAG_MAX_CHUNKS_PER_SUBJECT = int(os.environ.get("TUTOR_RAG_MAX_CHUNKS", "80"))

LEVELS = ["beginner", "intermediate", "advanced"]

# Resolve storage paths relative to this package's location, not whatever
# directory the process happens to be launched from -- this keeps behavior
# consistent whether you run `uvicorn api:app` from inside tutor_backend/
# or `uvicorn tutor_backend.api:app` from its parent directory.
BASE_DIR = Path(__file__).resolve().parent

# Where per-student progress JSON lives. In the FastAPI layer this will be
# keyed per student_id (e.g. memory/<student_id>.json).
MEMORY_DIR = os.environ.get("TUTOR_MEMORY_DIR", str(BASE_DIR / "tutor_memory"))

# Where uploaded course-material PDFs get ingested into extra topics.
UPLOADS_DIR = os.environ.get("TUTOR_UPLOADS_DIR", str(BASE_DIR / "tutor_uploads"))

# Student-shared resources (Resources page): one JSON file per resource
# (metadata + ratings + classifications), uploaded files live under
# UPLOADS_DIR/resources/<resource_id>/.
RESOURCES_DIR = os.environ.get("TUTOR_RESOURCES_DIR", str(BASE_DIR / "tutor_resources"))

# Where per-student Learner Profiles live (education level, existing
# skills, interests, career goal, preferred language). Separate from
# MEMORY_DIR: memory is *derived* (mastery computed from quiz/code
# activity), a profile is *declared* by the learner and edited directly.
PROFILE_DIR = os.environ.get("TUTOR_PROFILE_DIR", str(BASE_DIR / "tutor_profiles"))

# Where per-student roadmaps live (ordered list of upcoming steps the
# Brain has planned, regenerated after each evaluation). Same file-backed
# tradeoff as MEMORY_DIR/PROFILE_DIR/RESOURCES_DIR.
ROADMAP_DIR = os.environ.get("TUTOR_ROADMAP_DIR", str(BASE_DIR / "tutor_roadmaps"))

# Where per-student wellbeing data lives (mood check-ins, journal entries).
# Same file-backed tradeoff as the other *_DIR stores above.
WELLBEING_DIR = os.environ.get("TUTOR_WELLBEING_DIR", str(BASE_DIR / "tutor_wellbeing"))

LLM_TIMEOUT_SECONDS = 120
JSON_CALL_ATTEMPTS = 3

# ---------------------------------------------------------------------------
# Wellbeing / mental health support module
# ---------------------------------------------------------------------------
# This module is a *support and surface* tool, not a clinical one: it never
# diagnoses, and anything that looks like a safety risk gets routed to a
# real person rather than handled by the LLM alone. CRISIS_CONTACT should
# be set per-deployment (e.g. the facility's on-call counselor / duty
# officer line) -- the placeholder below is deliberately generic.
CRISIS_CONTACT = os.environ.get(
    "TUTOR_CRISIS_CONTACT",
    "facility staff or the on-site counselor immediately, or a local emergency line",
)
