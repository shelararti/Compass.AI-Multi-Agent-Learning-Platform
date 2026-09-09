# Tutor Agent Backend -- Agent Graph

This is the first piece of the multi-agent rebuild: a LangGraph graph that
implements the architecture diagram (Orchestrator -> Teacher/Quiz/Coding ->
Evaluation -> Progress & Memory DB -> Learning Planner), adapted for a
stateless request/response backend instead of an interactive CLI loop.

## Layout

```
tutor_backend/
  config.py            # model, endpoint, storage settings
  llm_client.py         # Ollama calls + JSON coaxing/retries
  knowledge_base.py      # course topics; add_topic() for PDF ingestion later
  memory_store.py        # per-student JSON progress persistence
  state.py               # shared TutorState schema passed through the graph
  agents/
    orchestrator.py       # resolves intent + topic, routes to a specialist
    teacher.py             # free-form Q&A grounded in course notes
    quiz.py                 # generates a multiple-choice quiz
    coding.py                # generates a coding exercise + rubric
    evaluation.py             # grades quiz answers or code submissions
    memory_update.py          # persists evaluation results into mastery record
    planner.py                 # suggests the next topic to study
  graph.py                # wires all agents into the compiled StateGraph
  test_graph_wiring.py    # smoke test with mocked LLM calls (no Ollama needed)
```

## Two stages, one graph

Because a real backend answers one HTTP request at a time rather than
looping interactively like the original CLI script, each turn is one of:

- **`stage="generate"`**: student asks a question, requests a quiz, or
  requests a coding task. Orchestrator resolves the topic and intent, then
  routes to Teacher / Quiz / Coding, which returns content to show the
  student.
- **`stage="evaluate"`**: student submits quiz answers or code. Orchestrator
  routes straight to the Evaluation Agent, which grades the submission,
  then Memory persists the result and the Planner suggests what's next.

`intent="progress"` is a read-only shortcut straight to the Planner --
no new evaluation, just "how am I doing and what should I study next".

## Running the smoke test

No Ollama required -- LLM calls are mocked to check routing/state-passing:

```bash
python3 test_graph_wiring.py
```

With Ollama running (`ollama serve`, model pulled per the original script's
README), the same graph works unmodified against the real model -- just
call `tutor_graph.invoke({...})` with real input instead of mocks.

## Example invocation

```python
from tutor_backend.graph import tutor_graph

result = tutor_graph.invoke({
    "student_id": "alice",
    "level": "beginner",
    "stage": "generate",
    "intent": "quiz",
    "topic_id": "rag",
})
questions = result["quiz"]["questions"]

# ... student answers, then:
graded = tutor_graph.invoke({
    "student_id": "alice",
    "level": "beginner",
    "stage": "evaluate",
    "topic_id": "rag",
    "quiz_questions": questions,
    "quiz_answers": [0, 2, 1],
})
print(graded["evaluation"]["score"], graded["next_topic_suggestion"])
```

## Running the full app (API + frontend)

```bash
pip install -r requirements.txt
ollama serve                 # if not already running as a background service
ollama pull llama3.2:1b      # if not already pulled
ollama pull nomic-embed-text # optional -- powers RAG for uploaded PDF subjects
cd tutor_backend             # so relative paths (frontend/, tutor_memory/) resolve
python -m uvicorn api:app --reload --port 8000
```

Then open **http://localhost:8000** in a browser.

Note the `cd tutor_backend` before running uvicorn -- `api.py` serves the
static frontend from a relative `frontend/` path and writes memory to a
relative `tutor_memory/` directory, so it needs to be run from inside the
package folder (unlike `test_graph_wiring.py`, which is run from one level
above with `python -m tutor_backend.test_graph_wiring`).

### Subjects & PDF-upload RAG

The tutor now supports multiple **subjects** instead of one fixed course:
built-in subjects (`knowledge_base.py`) plus subjects you build yourself by
uploading a PDF (`POST /api/subjects/upload`).

Uploading a PDF does two things:
1. Splits it into coarse **topics** (`pdf_ingest.py`) for the topology map
   and per-topic mastery tracking, same as the built-in subjects.
2. Splits it into smaller **retrieval chunks**, embeds each with the
   `nomic-embed-text` model via Ollama's `/api/embeddings` endpoint, and
   saves the vectors to `tutor_uploads/<subject>.embeddings.json`
   (`vector_store.py`).

At question time, if a subject has an embedding index, the orchestrator
embeds the student's question and retrieves the most similar chunks from
across the whole PDF -- not just whatever topic happens to be selected --
and grounds the chat/quiz/code prompt in those excerpts (real RAG). If the
embedding model isn't pulled, upload still succeeds; the subject just
falls back to grounding on each topic's fixed section text instead.

## API endpoints

| Method | Path              | Purpose                                      |
|--------|-------------------|-----------------------------------------------|
| GET    | `/api/topics`     | list of course topics                          |
| GET    | `/api/progress`   | mastery per topic + next-topic suggestion      |
| POST   | `/api/chat`       | ask a free-form question                       |
| POST   | `/api/quiz/start` | generate a quiz (sanitized -- no answer key)   |
| POST   | `/api/quiz/submit`| grade a quiz attempt by `attempt_id`           |
| POST   | `/api/code/start` | generate a coding exercise + rubric            |
| POST   | `/api/code/submit`| grade a code submission by `attempt_id`        |
| GET    | `/api/wellbeing/meta`     | mood scale, exercise library, crisis contact info      |
| POST   | `/api/wellbeing/checkin`  | log a mood check-in                                     |
| GET    | `/api/wellbeing/checkins` | check-in history for a student                          |
| POST   | `/api/wellbeing/journal`  | submit a journal entry, get a reflection back           |
| GET    | `/api/wellbeing/journal`  | journal history for a student                           |
| GET    | `/api/wellbeing/exercises`| coping exercise library                                 |
| GET    | `/api/wellbeing/staff/flags` | staff review queue of flagged journal entries        |
| POST   | `/api/wellbeing/staff/flags/{student_id}/{entry_id}` | update a flag's staff status |

**Security note:** `/quiz/start` and `/code/start` cache the full generated
content (including correct answers) server-side under a one-time
`attempt_id`, and only send the student a sanitized version. Grading looks
the attempt back up server-side rather than trusting anything the client
sends back about what's "correct" -- so a curious student poking at the
network tab can't just read the answer key.

## Frontend

Plain HTML/CSS/JS, no build step, served directly by FastAPI from
`frontend/`. The signature visual is the **topology strip**: a row of
nodes, one per topic, connected by a line and colored on a grey-to-green
scale by mastery percent -- doubling as topic navigation (click a node to
select that topic) and a progress-at-a-glance view. Node positions are
computed evenly at render time rather than hardcoded, so it keeps working
as PDF-ingested topics get added later.

## Visualize page

A second top-level page (`/visualize`, linked from the Home/Visualize nav
at the top of every page) lets a student upload a lecture **video**
instead of a PDF. It gets transcribed and each chunk classified into a
visual domain (process, formula, data, narrative, spatial, conceptual,
scene) with its actual content extracted — shown live as a mindmap while
it's still processing. You can correct a misclassified chunk right there
(same idea as the old `review_analysis.py` CLI, now a dropdown in the
browser), then render a final video with visuals overlaid on top of the
original footage.

This reuses the transcribe → classify → extract → render pipeline that
used to be a standalone `render_video.py` script, now living at
`tutor_backend/visualizer/render_video.py` and driven by
`visualize_service.py` (background job per upload) + `visualize_api.py`
(the `/api/visualize/*` routes). Classification and extraction go through
the same `llm_client.py` / `config.py` Ollama endpoint every other agent
uses — no separate model config to keep in sync.

**Extra requirements** on top of the base install: `ffmpeg` on PATH, plus
`faster-whisper`, `matplotlib`, `numpy`, and `pillow` (already in
`requirements.txt`). Whisper transcription runs on CPU and is the slow
part — expect roughly real-time-or-slower per minute of video on a
typical laptop. Jobs are processed one at a time (`visualize_service.py`'s
`_PROCESSING_LOCK`) since Whisper + Ollama don't like concurrent load on
a single dev machine; concurrent uploads just queue.

Job state lives in memory (`visualize_service.JOBS`), same tradeoff as
`ATTEMPTS` in `api.py` — fine for one process, swap for a real queue +
store before running more than one worker or surviving restarts.

## Resources page

A third page (`/resources`) for peer-shared study material — students
upload a file (PDF, PPT/PPTX, DOC/DOCX, video) or add a link (YouTube or
any URL), tag it with a subject, and other students rate it 1-5 stars and
classify it (difficulty level + a fixed tag vocabulary like
`beginner-friendly`, `exam-prep`, `visual-heavy`). Average rating and tag
counts are computed on read from the raw ratings/classifications, so
they're never stale.

Backed by `resource_store.py` (one JSON file per resource under
`config.RESOURCES_DIR`, files under `UPLOADS_DIR/resources/<id>/` — file-
backed like `memory_store.py`, and meant to persist across restarts,
unlike Visualize's in-memory job queue) and `resources_api.py` (the
`/api/resources/*` routes). A student can re-rate or re-classify a
resource — later submissions replace their earlier one rather than
stacking duplicates.

## Wellbeing page

A fourth page (`/wellbeing`) for mental-health support. **Scope, on
purpose:** this is a support-and-surface tool, not a clinical one. It
never diagnoses, and anything that looks like an acute safety risk is
routed to a real person rather than handled by the LLM alone.

- **Mood check-ins** -- a 1-5 self-report + optional note, charted as a
  simple history so a student (or, with their check-ins visible, staff)
  can see trends over time.
- **Reflective journaling** -- free-text entry; a supportive, explicitly
  non-diagnostic LLM companion reflects it back (active-listening style,
  not therapy). Every entry is scanned by `wellbeing_store.scan_for_risk`
  first: if it matches a plain-language risk signal (e.g. suicidal
  intent), the LLM is **skipped entirely** and a fixed, reviewed safety
  response is used instead, pointing the student to `config.CRISIS_CONTACT`
  and flagging the entry for staff. This is deliberate -- a small local
  model (`llama3.2:1b`) is not something to trust with the exact wording
  of a response to someone in crisis, so that one message is the one
  thing in this module that is *not* LLM-generated.
- **Coping exercises** -- a small fixed library (breathing, grounding,
  reframing, reflection prompts). Static content on purpose, same
  reasoning as above: consistency matters more than personalization here.
- **Staff review queue** -- flagged journal entries land in
  `GET /api/wellbeing/staff/flags` for a human to triage
  (`open` → `reviewing` → `resolved`).

Backed by `wellbeing_store.py` (one JSON file per student under
`config.WELLBEING_DIR`, file-backed like `memory_store.py` /
`resource_store.py`) and `wellbeing_api.py` (the `/api/wellbeing/*`
routes). The reflection itself is generated in `wellbeing_agent.py`.

**Before using this with real students:**
1. Set `TUTOR_CRISIS_CONTACT` to something real for your deployment (a
   facility's on-call counselor/duty-officer line, a local crisis line) --
   the default is a deliberately generic placeholder.
2. `wellbeing_store.scan_for_risk` is a coarse, conservative keyword net,
   not a clinical screening tool -- expect false positives (a student
   venting in dramatic language) and don't market it as more than it is.
   It should never be the *only* safety net; pair it with real staff
   presence and a way for students to reach a person directly.
3. **No real auth in front of `/api/wellbeing/staff/*`** -- same
   hackathon-scope caveat as the rest of the app (see below). Flagged
   journal entries are sensitive; put real authentication + role checks
   in front of the staff routes before this touches real students.

## Next steps

1. **PDF ingestion**: parse uploaded course material and call
   `knowledge_base.add_topic(id, title, notes)` so agents ground answers in
   real material instead of only the built-in topics. A natural spot for
   the upload button is next to the level selector in the topbar.
2. **Swap the in-memory `ATTEMPTS` cache** in `api.py` for Redis or a DB
   table before running more than one worker process or surviving restarts.
3. **Swap file-based memory** (`memory_store.py`) for a real database if
   this needs to scale past a handful of students on one machine.
4. **Visualize jobs**: same swap as #2/#3 applies to `visualize_service.JOBS`
   and the per-job files under `tutor_uploads/visualize/` — fine for now,
   won't survive a restart or multiple workers.
