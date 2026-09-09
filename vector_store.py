"""
Per-subject vector store for uploaded-PDF subjects.

Each uploaded subject gets a flat set of retrieval chunks -- finer-grained
than the topics shown in the UI -- embedded via the local Ollama embedding
model and cached on disk as plain JSON (no external vector DB needed for
a single-course, single-machine tutor). At question time, the orchestrator
embeds the student's question and retrieves the most similar chunks, so
answers stay grounded in whatever part of the PDF is actually relevant
instead of just whichever topic happened to be selected.

Falls back gracefully: if no embedding model is available (build_index
returns False), the subject still works using its topic notes as before
-- see orchestrator.py.
"""

import json
import math
import os

from . import config
from . import embeddings

# In-memory cache: subject_id -> {"chunks": [...], "vectors": [[float]]}.
# Backed by a JSON file per subject so the index survives process restarts.
_INDEX = {}


def _path_for(subject_id: str) -> str:
    os.makedirs(config.UPLOADS_DIR, exist_ok=True)
    safe = "".join(c for c in subject_id if c.isalnum() or c in ("-", "_")) or "subject"
    return os.path.join(config.UPLOADS_DIR, f"{safe}.embeddings.json")


def build_index(subject_id: str, chunks: list) -> bool:
    """chunks: list of {"id", "topic_title", "text"}. Embeds each chunk and
    persists the index to disk. Returns True on success, False if
    embeddings weren't available (caller should degrade gracefully)."""
    if not chunks:
        return False
    texts = [c["text"] for c in chunks]
    try:
        vectors = embeddings.embed_texts(texts)
    except RuntimeError:
        return False
    data = {"chunks": chunks, "vectors": vectors}
    _INDEX[subject_id] = data
    with open(_path_for(subject_id), "w") as f:
        json.dump(data, f)
    return True


def _load_index(subject_id: str):
    if subject_id in _INDEX:
        return _INDEX[subject_id]
    path = _path_for(subject_id)
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        _INDEX[subject_id] = data
        return data
    return None


def has_index(subject_id: str) -> bool:
    return _load_index(subject_id) is not None


def delete_index(subject_id: str) -> None:
    _INDEX.pop(subject_id, None)
    path = _path_for(subject_id)
    if os.path.exists(path):
        os.remove(path)


def _cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def search(subject_id: str, query: str, top_k: int = None) -> list:
    """Return the top_k most similar chunks to `query` for this subject,
    highest similarity first. Empty list if there's no index or the
    embedding call fails."""
    top_k = top_k or config.RAG_TOP_K
    index = _load_index(subject_id)
    if not index or not index.get("chunks"):
        return []
    try:
        q_vec = embeddings.embed_texts([query])[0]
    except RuntimeError:
        return []
    scored = [
        (_cosine(q_vec, vec), chunk) for chunk, vec in zip(index["chunks"], index["vectors"])
    ]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk for _, chunk in scored[:top_k]]


def format_context(chunks: list) -> str:
    """Render retrieved chunks into a single grounding string for the
    LLM prompt, labeled by where each excerpt came from."""
    if not chunks:
        return ""
    parts = []
    for c in chunks:
        label = c.get("topic_title") or "Excerpt"
        parts.append(f"[{label}]\n{c['text']}")
    return "\n\n".join(parts)
