"""
API routes for the Brain layer. Starting scope: Learner Profile CRUD and
the skill-readiness readout the dashboard needs. decide_next() /
generate_roadmap() land here later as the deterministic pre-filter and
LLM rationale layer are built -- see ai_brain_architecture.md.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import profile_store
from .. import skills as skills_taxonomy
from . import decision_engine
from . import roadmap as roadmap_mod
from . import skill_graph

router = APIRouter(prefix="/api/brain", tags=["brain"])


class ProfileUpdateRequest(BaseModel):
    student_id: str
    education_level: Optional[str] = None
    existing_skills: Optional[list[str]] = None
    interests: Optional[list[str]] = None
    career_goal: Optional[str] = None
    preferred_language: Optional[str] = None


@router.get("/meta")
def meta():
    """Vocabulary the frontend needs to render the profile form / skill
    bars without hardcoding it -- same pattern as GET /api/resources/meta."""
    return {
        "education_levels": profile_store.EDUCATION_LEVELS,
        "languages": profile_store.LANGUAGES,
        "skills": skills_taxonomy.SKILLS,
    }


@router.get("/profile")
def get_profile(student_id: str):
    return profile_store.load_profile(student_id)


@router.post("/profile")
def update_profile(req: ProfileUpdateRequest):
    if req.education_level is not None and req.education_level not in profile_store.EDUCATION_LEVELS:
        raise HTTPException(
            400, f"education_level must be one of: {', '.join(profile_store.EDUCATION_LEVELS)}"
        )
    if req.preferred_language is not None and req.preferred_language not in profile_store.LANGUAGES:
        raise HTTPException(
            400, f"preferred_language must be one of: {', '.join(profile_store.LANGUAGES)}"
        )
    return profile_store.update_profile(
        req.student_id,
        education_level=req.education_level,
        existing_skills=req.existing_skills,
        interests=req.interests,
        career_goal=req.career_goal,
        preferred_language=req.preferred_language,
    )


@router.get("/skills")
def get_skills(student_id: str, subject_id: Optional[str] = None):
    return {
        "skills": skill_graph.skill_levels_labeled(student_id, subject_id),
        "employment_readiness": skill_graph.employment_readiness(student_id, subject_id),
    }


@router.get("/next")
def get_next(student_id: str, subject_id: Optional[str] = None):
    """The single call the frontend's 'AI-guided journey' screen polls
    after every completed step -- tells it which module to route to and
    why, without the learner ever picking from a menu."""
    return decision_engine.decide_next(student_id, subject_id)


@router.get("/roadmap")
def get_roadmap(student_id: str, subject_id: Optional[str] = None):
    """Cached multi-step plan. Doesn't regenerate on every call -- see
    POST /api/brain/roadmap/refresh for that."""
    return roadmap_mod.get_or_generate(student_id, subject_id)


@router.post("/roadmap/refresh")
def refresh_roadmap(student_id: str, subject_id: Optional[str] = None):
    """Force a fresh roadmap against the learner's current mastery --
    the graph's memory_update -> planner edge should call the equivalent
    roadmap_mod.update_after_evaluation() after every evaluation; this
    endpoint exists for the frontend to trigger the same thing manually
    (e.g. a 'refresh my roadmap' button)."""
    return roadmap_mod.update_after_evaluation(student_id, subject_id)
