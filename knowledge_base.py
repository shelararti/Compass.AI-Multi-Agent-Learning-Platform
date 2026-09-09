"""
Course knowledge base -- the "notes" agents ground their answers in.

Organized as **subjects**, each holding a list of **topics**. This module
ships with built-in subjects, and the PDF ingestion pipeline
(`pdf_ingest.py`) calls `add_subject` to register additional subjects
parsed out of instructor/student-uploaded material, so agents can be
grounded in real course content instead of only these defaults.

Uploaded subjects are persisted to disk (alongside their embedding index
in vector_store.py) and reloaded at import time, so they survive a
server restart instead of only lasting for the current process.

Topic ids are unique *globally* (not just within a subject) because
per-student mastery in memory_store.py is keyed by topic id alone.
"""

import json
import os
import re

from . import config
from . import skills as skills_taxonomy

SUBJECTS = [
    {
        "id": "agentic-ai-llms",
        "title": "Agentic AI & LLMs",
        "description": (
            "How large language models work under the hood, how to prompt and "
            "ground them, and how to wrap them into tool-using agents and "
            "multi-agent systems."
        ),
        "source": "builtin",
        "topics": [
            {
                "id": "foundations",
                "title": "LLM foundations",
                "notes": (
                    "Large language models are transformer neural networks trained to predict "
                    "the next token in a sequence. Text is broken into tokens (subword units), "
                    "converted to embeddings, and processed through stacked layers of "
                    "self-attention and feed-forward blocks. Self-attention lets every token "
                    "weigh every other token when building its representation, which is how "
                    "the model captures context and meaning. At inference time the model "
                    "samples one token at a time, using controls like temperature and top-p "
                    "to trade off determinism versus creativity. Context window size limits "
                    "how much text the model can consider at once."
                ),
                "skills": ["ai_ml", "digital_literacy"],
            },
            {
                "id": "prompting",
                "title": "Prompting techniques",
                "notes": (
                    "Prompting is how you steer a model's behavior without changing its weights. "
                    "Zero-shot prompting asks directly; few-shot prompting includes 2-5 examples "
                    "of the desired input-output pattern. Chain-of-thought prompting asks the "
                    "model to reason step by step before answering, which improves accuracy on "
                    "multi-step problems. System prompts set persistent context, tone, and "
                    "constraints for a whole conversation. Good prompts are specific, give "
                    "relevant context, and clarify the desired output format."
                ),
                "skills": ["ai_ml", "communication"],
            },
            {
                "id": "rag",
                "title": "Retrieval-augmented generation (RAG)",
                "notes": (
                    "RAG grounds an LLM's answers in an external knowledge source instead of "
                    "relying only on what it memorized during training. Documents are split "
                    "into chunks, converted into vector embeddings, and stored in a vector "
                    "database. When a question arrives, the system embeds the question and "
                    "retrieves the most similar chunks, inserting them into the prompt as "
                    "context before generation. This reduces hallucination and lets the model "
                    "use up-to-date or private information. Chunking strategy and retrieval "
                    "quality matter as much as the generation model itself."
                ),
                "skills": ["ai_ml", "data"],
            },
            {
                "id": "tools",
                "title": "Tool use & function calling",
                "notes": (
                    "Tool use lets an LLM take actions instead of only producing text: calling "
                    "a calculator, querying a database, hitting an API, or running code. The "
                    "model is given a list of available tools with structured schemas "
                    "describing their inputs. When it decides a tool is needed, it emits a "
                    "structured call instead of a natural-language answer; the calling program "
                    "executes the real function and returns the result to the model, which "
                    "continues reasoning or responds to the user."
                ),
                "skills": ["ai_ml", "problem_solving"],
            },
            {
                "id": "agents",
                "title": "Agent architectures",
                "notes": (
                    "An agent is an LLM wrapped in a loop that lets it plan, act, observe "
                    "results, and revise its plan, rather than producing one response and "
                    "stopping. A common pattern is ReAct: the model alternates between "
                    "reasoning ('what should I do next') and acting (calling a tool), using "
                    "each tool result to inform the next step. Reflection patterns have the "
                    "agent critique its own output and retry. Good agent design bounds the "
                    "loop with step or cost limits and keeps memory of what's been tried."
                ),
                "skills": ["ai_ml", "problem_solving"],
            },
            {
                "id": "multiagent",
                "title": "Multi-agent systems",
                "notes": (
                    "Instead of one agent doing everything, complex tasks can be split across "
                    "multiple specialized agents that communicate, for example a planner "
                    "agent, a researcher agent, and a writer agent. An orchestrator agent "
                    "typically coordinates the others, assigning subtasks and combining "
                    "results. Multi-agent systems can parallelize work and let each agent "
                    "specialize, but add coordination overhead, error propagation between "
                    "agents, and harder debugging."
                ),
                "skills": ["ai_ml", "problem_solving"],
            },
            {
                "id": "evalsafety",
                "title": "Evaluation & safety",
                "notes": (
                    "Evaluating agents means checking not just whether individual answers are "
                    "correct, but whether the agent's actions and plans lead to correct and "
                    "safe outcomes over multi-step tasks. Hallucination -- the model stating "
                    "false information confidently -- is a central risk, which is why "
                    "grounding methods like RAG matter. Guardrails constrain what an agent can "
                    "do: which tools it may call, what data it can access, how many steps it "
                    "may take autonomously. Good evaluation includes held-out test tasks, "
                    "human review, and monitoring for failures discovered after deployment."
                ),
                "skills": ["ai_ml", "digital_literacy"],
            },
        ],
    },
    {
        "id": "python-basics",
        "title": "Python Programming Basics",
        "description": (
            "Core Python syntax and idioms: variables, control flow, functions, "
            "data structures, and error handling."
        ),
        "source": "builtin",
        "topics": [
            {
                "id": "py-syntax",
                "title": "Variables & syntax",
                "notes": (
                    "Python is dynamically typed: a variable is just a name bound to an "
                    "object, and that name can be rebound to a different type later. "
                    "Indentation (not braces) defines code blocks, so consistent whitespace "
                    "is syntactically meaningful. Basic built-in types include int, float, "
                    "str, bool, and None. f-strings (f'{value}') are the standard way to "
                    "interpolate values into strings."
                ),
                "skills": ["python", "digital_literacy"],
            },
            {
                "id": "py-control-flow",
                "title": "Control flow",
                "notes": (
                    "if/elif/else branches on conditions. for loops iterate directly over "
                    "items in a sequence (list, string, range, etc.) rather than counting "
                    "indices manually. while loops repeat until a condition becomes false. "
                    "break exits a loop early, continue skips to the next iteration, and a "
                    "loop's optional else clause runs only if the loop completed without a "
                    "break."
                ),
                "skills": ["python", "problem_solving"],
            },
            {
                "id": "py-functions",
                "title": "Functions",
                "notes": (
                    "Functions are defined with def and can take positional args, keyword "
                    "args, default values, *args (extra positional args as a tuple), and "
                    "**kwargs (extra keyword args as a dict). Functions are first-class "
                    "objects: they can be passed around, stored in variables, and returned "
                    "from other functions. Lambda creates small anonymous functions for "
                    "one-off use, most commonly as a sort key or callback."
                ),
                "skills": ["python", "problem_solving"],
            },
            {
                "id": "py-datastructures",
                "title": "Data structures",
                "notes": (
                    "Lists are ordered and mutable; tuples are ordered and immutable; sets "
                    "are unordered collections of unique, hashable items; dicts map hashable "
                    "keys to values and preserve insertion order. List and dict comprehensions "
                    "(e.g. [x*2 for x in items if x > 0]) build a new collection from an "
                    "existing iterable in one concise expression instead of a manual loop."
                ),
                "skills": ["python", "problem_solving"],
            },
            {
                "id": "py-errors",
                "title": "Errors & exceptions",
                "notes": (
                    "Exceptions signal that something went wrong at runtime. try/except "
                    "catches specific exception types so a program can recover instead of "
                    "crashing; else runs only if no exception occurred, and finally always "
                    "runs for cleanup. raise throws an exception, including custom exception "
                    "classes that subclass Exception. Catching overly broad exceptions (bare "
                    "except:) hides bugs, so code should target specific exception types."
                ),
                "skills": ["python", "digital_literacy"],
            },
        ],
    },
]


def _reindex():
    """Rebuild the lookup indexes and stamp each topic with its owning
    subject_id, so a topic dict alone is enough to find its subject
    (used by agents to phrase prompts around the right course title)."""
    global SUBJECT_BY_ID, TOPIC_BY_ID
    SUBJECT_BY_ID = {s["id"]: s for s in SUBJECTS}
    TOPIC_BY_ID = {}
    for s in SUBJECTS:
        for t in s["topics"]:
            t["subject_id"] = s["id"]
            TOPIC_BY_ID[t["id"]] = t


_reindex()

DEFAULT_SUBJECT_ID = SUBJECTS[0]["id"]


def _subject_path(subject_id: str) -> str:
    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    safe = "".join(c for c in subject_id if c.isalnum() or c in ("-", "_")) or "subject"
    return os.path.join(config.UPLOADS_DIR, f"{safe}.subject.json")


def _persist_subject(subject: dict) -> None:
    """Save an uploaded subject (metadata + topics) to disk so it survives
    a server restart. Its embedding index is persisted separately by
    vector_store.py under the same subject_id. Best-effort: a write
    failure just means this subject won't survive a restart, which
    shouldn't break the current process."""
    if subject.get("source") != "uploaded":
        return
    try:
        with open(_subject_path(subject["id"]), "w") as f:
            json.dump(subject, f)
    except OSError:
        pass


def _load_persisted_subjects() -> None:
    """Restore uploaded subjects saved by a previous run. Their embedding
    indexes are loaded lazily by vector_store.py from the matching
    *.embeddings.json file (same subject_id), so nothing else is needed
    here beyond re-registering the subject/topics themselves."""
    upload_dir = config.UPLOADS_DIR
    if not os.path.isdir(upload_dir):
        return
    for fname in sorted(os.listdir(upload_dir)):
        if not fname.endswith(".subject.json"):
            continue
        try:
            with open(os.path.join(upload_dir, fname)) as f:
                subject = json.load(f)
            if not subject.get("id") or not subject.get("topics"):
                continue
            if subject["id"] in SUBJECT_BY_ID:
                continue  # already present (e.g. a built-in id collision)
            SUBJECTS.append(subject)
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    _reindex()


_load_persisted_subjects()


def add_subject(subject_id: str, title: str, description: str, topics: list, source: str = "uploaded", rag: bool = False):
    """Register a new subject (e.g. parsed from an uploaded PDF). Overwrites
    any existing subject with the same id so re-uploads refresh the topics.
    `rag=True` means this subject also has a per-chunk embedding index
    (see vector_store.py) that agents should retrieve from instead of
    using a topic's fixed notes."""
    subject = {
        "id": subject_id,
        "title": title,
        "description": description,
        "source": source,
        "rag": rag,
        "topics": topics,
    }
    existing_idx = next((i for i, s in enumerate(SUBJECTS) if s["id"] == subject_id), None)
    if existing_idx is not None:
        SUBJECTS[existing_idx] = subject
    else:
        SUBJECTS.append(subject)
    _reindex()
    _persist_subject(subject)
    return subject


def add_topic(topic_id: str, title: str, notes: str, subject_id: str = None, skills: list = None):
    """Register a new topic under an existing subject (defaults to the
    first/default subject). Overwrites any existing topic with the same id
    so re-uploads refresh the notes.

    `skills` tags this topic into the shared skill taxonomy (skills.py) --
    unrecognized ids are dropped rather than raising, and an untagged topic
    (e.g. one auto-extracted from a PDF upload) just doesn't contribute to
    any skill's readiness score until someone tags it."""
    subject = SUBJECT_BY_ID.get(subject_id) if subject_id else None
    subject = subject or SUBJECTS[0]
    topic = {
        "id": topic_id,
        "title": title,
        "notes": notes,
        "subject_id": subject["id"],
        "skills": skills_taxonomy.validate_skills(skills),
    }
    existing_idx = next((i for i, t in enumerate(subject["topics"]) if t["id"] == topic_id), None)
    if existing_idx is not None:
        subject["topics"][existing_idx] = topic
    else:
        subject["topics"].append(topic)
    _reindex()
    return topic


def all_subjects():
    return list(SUBJECTS)


def get_subject(subject_id: str):
    return SUBJECT_BY_ID.get(subject_id)


def all_topics(subject_id: str = None):
    if subject_id is None:
        return [t for s in SUBJECTS for t in s["topics"]]
    subject = SUBJECT_BY_ID.get(subject_id)
    return list(subject["topics"]) if subject else []


def get_topic(topic_id: str):
    return TOPIC_BY_ID.get(topic_id)


def topics_for_skill(skill_id: str, subject_id: str = None) -> list:
    """Topics tagged with the given skill id, used by the Brain layer to
    turn 'communication is weak' into 'here's a concrete topic to study'.
    Topics with no (or not-yet-backfilled) 'skills' key are treated as
    untagged rather than raising, since older/uploaded topics may predate
    this field."""
    return [t for t in all_topics(subject_id) if skill_id in t.get("skills", [])]


def pick_topic(question: str, subject_id: str = None):
    """Bag-of-words match between a free-form question and topic notes.
    Good enough as a fallback; the orchestrator prefers an explicit
    topic_id from the client when one is available. Scoped to a subject
    when one is selected, otherwise searches across every subject."""
    candidates = all_topics(subject_id) if subject_id else all_topics()
    if not candidates:
        candidates = all_topics()
    q_words = [w for w in re.split(r"\W+", question.lower()) if len(w) > 3]
    best, best_score = candidates[0], -1
    for t in candidates:
        hay = (t["title"] + " " + t["notes"]).lower()
        score = sum(1 for w in q_words if w in hay)
        if score > best_score:
            best, best_score = t, score
    return best
