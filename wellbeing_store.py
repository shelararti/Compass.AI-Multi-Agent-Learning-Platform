"""
Persistence + safety-net logic for the Wellbeing page.

Same file-backed tradeoff as memory_store.py / resource_store.py: one JSON
file per student, swap for a real DB before scaling past a handful of
people on one machine. Layout per student:

    {
      "student_id": "...",
      "checkins": [{mood, note, at}],
      "journal": [{id, text, reflection, flagged, flag_reason, staff_status, staff_note, at}],
    }

Design intent: this module *supports and surfaces*, it never diagnoses and
never handles a safety risk alone. `scan_for_risk` is a coarse, deliberately
conservative keyword net -- it will over-flag sometimes, and that's the
correct failure mode here: a false positive costs a staff member two
minutes of review, a false negative could cost a lot more. It is not a
clinical screening tool and should never be marketed as one.
"""

import json
import os
import time
import uuid

from . import config

MOOD_SCALE = {
    1: "struggling",
    2: "low",
    3: "okay",
    4: "good",
    5: "great",
}

# Deliberately coarse and conservative -- see module docstring. Matches are
# substring-based on lowercased text, so short strings only (avoid
# hair-trigger words) and no clinical/diagnostic terms, just plain-language
# signals of acute risk or intent.
_RISK_PHRASES = [
    "kill myself", "end my life", "end it all", "suicide", "suicidal",
    "want to die", "don't want to live", "not worth living",
    "hurt myself", "harm myself", "self harm", "self-harm",
    "no way out", "better off dead", "can't go on", "cant go on",
]

# Static coping-exercise library. No LLM needed for these -- they're fixed,
# reviewed content, which matters when the audience may be in real distress
# and shouldn't get inconsistent LLM phrasing of a breathing exercise.
EXERCISES = [
    {
        "id": "box-breathing",
        "title": "Box breathing",
        "category": "grounding",
        "duration_min": 3,
        "steps": [
            "Breathe in slowly through your nose for 4 counts.",
            "Hold for 4 counts.",
            "Breathe out slowly through your mouth for 4 counts.",
            "Hold for 4 counts. Repeat for a few minutes.",
        ],
    },
    {
        "id": "5-4-3-2-1",
        "title": "5-4-3-2-1 grounding",
        "category": "grounding",
        "duration_min": 5,
        "steps": [
            "Name 5 things you can see around you.",
            "Name 4 things you can physically feel (e.g. your feet on the floor).",
            "Name 3 things you can hear right now.",
            "Name 2 things you can smell (or usually like the smell of).",
            "Name 1 thing you can taste, or one thing you're grateful for.",
        ],
    },
    {
        "id": "body-scan",
        "title": "Quick body scan",
        "category": "relaxation",
        "duration_min": 5,
        "steps": [
            "Sit or lie down comfortably and close your eyes if that feels okay.",
            "Bring attention to your feet -- notice any tension, let it soften.",
            "Move attention slowly upward: legs, stomach, chest, hands, shoulders, face.",
            "Take three slow breaths before opening your eyes.",
        ],
    },
    {
        "id": "reframe-thought",
        "title": "Reframe a stuck thought",
        "category": "cognitive",
        "duration_min": 5,
        "steps": [
            "Write down the thought that's bothering you, exactly as it sounds in your head.",
            "Ask: what evidence actually supports this? What evidence goes against it?",
            "Ask: what would I tell a friend who had this exact thought?",
            "Write one more balanced version of the thought.",
        ],
    },
    {
        "id": "future-letter",
        "title": "Letter to your future self",
        "category": "reflection",
        "duration_min": 10,
        "steps": [
            "Write to yourself a year from now: what do you want them to know?",
            "What's one small thing you're proud of from this week?",
            "What's one thing you're working toward right now?",
        ],
    },
]

# Always-visible. Deliberately generic + points to config.CRISIS_CONTACT,
# which each deployment should set to something real for its setting
# (facility duty officer, on-site counselor, local emergency number) --
# see the note in config.py.
def crisis_resources() -> dict:
    return {
        "immediate_contact": config.CRISIS_CONTACT,
        "note": (
            "If you are in immediate danger or thinking about harming "
            "yourself, please reach out to a real person right now -- "
            f"{config.CRISIS_CONTACT}. This page is a support tool, not "
            "a substitute for a counselor or emergency help."
        ),
    }


def scan_for_risk(text: str) -> str | None:
    """Returns a matched phrase if the text contains a risk signal, else
    None. Deliberately simple/conservative -- see module docstring."""
    lowered = (text or "").lower()
    for phrase in _RISK_PHRASES:
        if phrase in lowered:
            return phrase
    return None


def _safe_id(raw: str) -> str:
    return "".join(c for c in raw if c.isalnum() or c in ("-", "_")) or "default"


def _path(student_id: str) -> str:
    os.makedirs(config.WELLBEING_DIR, exist_ok=True)
    return os.path.join(config.WELLBEING_DIR, f"{_safe_id(student_id)}.json")


def _load(student_id: str) -> dict:
    path = _path(student_id)
    if not os.path.exists(path):
        return {"student_id": student_id, "checkins": [], "journal": [], "chat": []}
    try:
        with open(path, "r") as f:
            record = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"student_id": student_id, "checkins": [], "journal": [], "chat": []}
    record.setdefault("chat", [])  # older files predate the chat companion
    return record


def _save(record: dict) -> None:
    with open(_path(record["student_id"]), "w") as f:
        json.dump(record, f, indent=2)


def add_checkin(student_id: str, mood: int, note: str = "") -> dict:
    if mood not in MOOD_SCALE:
        raise ValueError(f"mood must be an integer 1-5 ({', '.join(f'{k}={v}' for k, v in MOOD_SCALE.items())})")
    record = _load(student_id)
    entry = {"mood": mood, "note": (note or "").strip(), "at": time.time()}
    record["checkins"].append(entry)
    _save(record)
    return entry


def list_checkins(student_id: str) -> list:
    return _load(student_id)["checkins"]


def add_journal_entry(student_id: str, text: str, reflection: str, flag_reason: str | None) -> dict:
    record = _load(student_id)
    entry = {
        "id": uuid.uuid4().hex[:12],
        "text": text,
        "reflection": reflection,
        "flagged": flag_reason is not None,
        "flag_reason": flag_reason,
        # Staff-review lifecycle for flagged entries only.
        "staff_status": "open" if flag_reason else None,
        "staff_note": None,
        "at": time.time(),
    }
    record["journal"].append(entry)
    _save(record)
    return entry


def list_journal(student_id: str) -> list:
    return _load(student_id)["journal"]


def add_chat_message(student_id: str, role: str, text: str, flag_reason: str | None = None) -> dict:
    """Appends one turn (role is 'user' or 'assistant') to the student's
    chat companion log. Only 'user' turns are ever flagged -- the
    assistant's replies are either the fixed safety response or an LLM
    reply already generated with the flag decision in mind."""
    record = _load(student_id)
    entry = {
        "id": uuid.uuid4().hex[:12],
        "role": role,
        "text": text,
        "flagged": flag_reason is not None,
        "flag_reason": flag_reason,
        "staff_status": "open" if flag_reason else None,
        "staff_note": None,
        "at": time.time(),
    }
    record["chat"].append(entry)
    _save(record)
    return entry


def list_chat(student_id: str) -> list:
    return _load(student_id)["chat"]


def list_flagged_entries() -> list:
    """Across all students and both the journal and the chat companion --
    backs the staff review queue. Fine at file-backed scale (see module
    docstring); a real deployment would index this in a DB instead of
    scanning every student file."""
    if not os.path.isdir(config.WELLBEING_DIR):
        return []
    out = []
    for name in os.listdir(config.WELLBEING_DIR):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(config.WELLBEING_DIR, name), "r") as f:
                record = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for entry in record.get("journal", []):
            if entry.get("flagged"):
                out.append({**entry, "student_id": record["student_id"], "source": "journal"})
        for entry in record.get("chat", []):
            if entry.get("flagged"):
                out.append({**entry, "student_id": record["student_id"], "source": "chat"})
    out.sort(key=lambda e: e["at"], reverse=True)
    return out


def resolve_flag(student_id: str, entry_id: str, staff_status: str, staff_note: str = "") -> dict:
    if staff_status not in ("open", "reviewing", "resolved"):
        raise ValueError("staff_status must be one of: open, reviewing, resolved")
    record = _load(student_id)
    for source in ("journal", "chat"):
        for entry in record[source]:
            if entry["id"] == entry_id:
                entry["staff_status"] = staff_status
                entry["staff_note"] = staff_note.strip() or entry["staff_note"]
                _save(record)
                return entry
    raise KeyError(entry_id)
