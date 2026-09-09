"""
API routes for the "Wellbeing" page -- mood check-ins, a reflective
journal, coping exercises, and a staff review queue for flagged entries.
Independent of the tutor graph, same as visualize_api.py / resources_api.py.

Note on "staff" routes: there's no real auth in this hackathon build (same
as the rest of the app -- student_id is just a client-supplied string).
A real deployment MUST put actual authentication + role checks in front
of the /staff/* routes before this touches real students, since flagged
journal entries are sensitive.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import wellbeing_agent
from . import wellbeing_store as ws

router = APIRouter(prefix="/api/wellbeing", tags=["wellbeing"])


class CheckinRequest(BaseModel):
    student_id: str
    mood: int
    note: str = ""


class JournalRequest(BaseModel):
    student_id: str
    text: str


class ChatRequest(BaseModel):
    student_id: str
    text: str


class ResolveFlagRequest(BaseModel):
    staff_status: str
    staff_note: str = ""


@router.get("/meta")
def meta():
    """Vocab the frontend needs -- mood scale + exercise library + the
    always-visible crisis contact info."""
    return {
        "mood_scale": ws.MOOD_SCALE,
        "exercises": ws.EXERCISES,
        "crisis": ws.crisis_resources(),
    }


@router.post("/checkin")
def checkin(req: CheckinRequest):
    try:
        entry = ws.add_checkin(req.student_id, req.mood, req.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return entry


@router.get("/checkins")
def checkins(student_id: str):
    return ws.list_checkins(student_id)


@router.post("/journal")
def journal(req: JournalRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "Journal entry text cannot be empty.")
    return wellbeing_agent.reflect(req.student_id, text)


@router.get("/journal")
def journal_history(student_id: str):
    entries = ws.list_journal(student_id)
    # Strip staff-only fields from the student-facing history view.
    return [
        {"id": e["id"], "text": e["text"], "reflection": e["reflection"],
         "flagged": e["flagged"], "at": e["at"]}
        for e in entries
    ]


@router.post("/chat")
def chat(req: ChatRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(400, "Message cannot be empty.")
    return wellbeing_agent.chat(req.student_id, text)


@router.get("/chat")
def chat_history(student_id: str):
    entries = ws.list_chat(student_id)
    # Strip staff-only fields from the student-facing history view, same as
    # the journal history route.
    return [
        {"id": e["id"], "role": e["role"], "text": e["text"], "flagged": e["flagged"], "at": e["at"]}
        for e in entries
    ]


@router.get("/exercises")
def exercises(category: Optional[str] = None):
    if category:
        return [e for e in ws.EXERCISES if e["category"] == category]
    return ws.EXERCISES


# ---------------------------------------------------------------------------
# Staff review queue (flagged journal entries) -- see module docstring
# about the missing auth layer before this is used for real.
# ---------------------------------------------------------------------------
@router.get("/staff/flags")
def staff_flags():
    return ws.list_flagged_entries()


@router.post("/staff/flags/{student_id}/{entry_id}")
def staff_resolve_flag(student_id: str, entry_id: str, req: ResolveFlagRequest):
    try:
        return ws.resolve_flag(student_id, entry_id, req.staff_status, req.staff_note)
    except KeyError:
        raise HTTPException(404, "Unknown entry_id for that student.")
    except ValueError as e:
        raise HTTPException(400, str(e))
