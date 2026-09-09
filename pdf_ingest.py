"""
PDF ingestion pipeline: turns an uploaded PDF into (1) a set of topics for
a new subject, used for UI navigation and per-topic mastery tracking, and
(2) a set of finer-grained retrieval chunks that get embedded into a
per-subject vector index (see vector_store.py) so the tutor can ground
answers in whatever part of the PDF is actually relevant to the student's
question -- real retrieval-augmented generation, not just a fixed section.

Parsing and chunking here are heuristic-only (no LLM call), so ingestion
still works even if Ollama isn't running; building the embedding index is
a separate, best-effort step that degrades gracefully if it is.
"""

import io
import re
import uuid

from pypdf import PdfReader

from . import config

MIN_CHUNK_CHARS = 300
MAX_CHUNK_CHARS = 2200
MAX_TOPICS = 20

# Retrieval chunks are deliberately smaller than topic chunks -- finer
# granularity gives the embedding search something more precise to match
# a specific question against, instead of a whole multi-paragraph section.
RAG_MIN_CHUNK_CHARS = 120
RAG_MAX_CHUNK_CHARS = 700

_HEADING_RE = re.compile(
    r"^(chapter\s+\d+|module\s+\d+|section\s+\d+|unit\s+\d+|lesson\s+\d+|\d+(\.\d+)*[\s.:-]+\S)",
    re.IGNORECASE,
)


def _looks_like_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 90:
        return False
    words = line.split()
    if len(words) > 12:
        return False
    if _HEADING_RE.match(line):
        return True
    # Short, mostly-capitalized lines with no terminal punctuation read
    # like a heading (e.g. "Neural Network Basics").
    if line.endswith((".", ",", ";")):
        return False
    cap_words = sum(1 for w in words if w[:1].isupper())
    return cap_words / len(words) > 0.6


def extract_text(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def _split_into_sections(text: str):
    """Split raw text into (title_or_None, body_lines) sections based on
    detected heading lines."""
    sections = []
    current_title = None
    current_lines = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if _looks_like_heading(line):
            if "\n".join(current_lines).strip():
                sections.append((current_title, current_lines))
            current_title = line.strip()
            current_lines = []
        else:
            current_lines.append(line)
    if "\n".join(current_lines).strip():
        sections.append((current_title, current_lines))
    return sections


def _chunk_long_text(text: str, max_chars: int = MAX_CHUNK_CHARS):
    """Break long text into paragraph-aligned chunks under max_chars."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    chunks, current = [], ""
    for p in paragraphs:
        if current and len(current) + len(p) > max_chars:
            chunks.append(current.strip())
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _slugify(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or fallback


def _build_topics(sections: list, subject_id_prefix: str) -> list:
    """Coarse chunks -- one per detected section -- used for UI navigation
    (topology nodes) and per-topic mastery tracking."""
    topics = []
    used_ids = set()

    def add_topic(title: str, body: str):
        body = body.strip()
        if len(body) < MIN_CHUNK_CHARS:
            return
        topic_id = _slugify(f"{subject_id_prefix}-{title}", f"{subject_id_prefix}-{len(topics) + 1}")
        while topic_id in used_ids:
            topic_id = f"{topic_id}-{uuid.uuid4().hex[:4]}"
        used_ids.add(topic_id)
        # Keep prompt-sized notes -- non-RAG subjects paste this whole into
        # the system prompt for every chat/quiz/code turn on this topic.
        topics.append({"id": topic_id, "title": title[:80].strip() or "Overview", "notes": body[:4000]})

    for title, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        section_title = title or "Overview"
        if len(body) <= MAX_CHUNK_CHARS:
            add_topic(section_title, body)
        else:
            chunks = _chunk_long_text(body, MAX_CHUNK_CHARS)
            for i, chunk in enumerate(chunks, start=1):
                label = section_title if len(chunks) == 1 else f"{section_title} ({i}/{len(chunks)})"
                add_topic(label, chunk)

    if not topics:
        # No detectable headings -- fall back to blind paragraph chunking
        # of the whole document.
        whole_text = "\n".join(l for _, lines in sections for l in lines)
        for i, chunk in enumerate(_chunk_long_text(whole_text, MAX_CHUNK_CHARS), start=1):
            add_topic(f"Section {i}", chunk)

    return topics[:MAX_TOPICS]


def _build_chunks(sections: list, subject_id_prefix: str) -> list:
    """Fine-grained chunks -- several per section -- used to build the
    retrieval (RAG) index. Each chunk keeps its section title so retrieved
    excerpts can be labeled/cited back to where they came from."""
    chunks = []
    used_ids = set()

    def add_chunk(section_title: str, body: str):
        body = body.strip()
        if len(body) < RAG_MIN_CHUNK_CHARS:
            return
        chunk_id = _slugify(
            f"{subject_id_prefix}-chunk-{section_title}-{len(chunks) + 1}",
            f"{subject_id_prefix}-chunk-{len(chunks) + 1}",
        )
        while chunk_id in used_ids:
            chunk_id = f"{chunk_id}-{uuid.uuid4().hex[:4]}"
        used_ids.add(chunk_id)
        chunks.append({"id": chunk_id, "topic_title": section_title[:80].strip() or "Excerpt", "text": body})

    for title, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        section_title = title or "Overview"
        for piece in _chunk_long_text(body, RAG_MAX_CHUNK_CHARS):
            add_chunk(section_title, piece)

    return chunks[: config.RAG_MAX_CHUNKS_PER_SUBJECT]


def pdf_to_topics(file_bytes: bytes, subject_id_prefix: str) -> list:
    """Parse PDF bytes into a list of topic dicts: {id, title, notes}."""
    topics, _ = pdf_to_course(file_bytes, subject_id_prefix)
    return topics


def pdf_to_course(file_bytes: bytes, subject_id_prefix: str):
    """Parse PDF bytes once into both:
    - topics: coarse chunks for UI navigation/mastery tracking
    - chunks: fine-grained chunks for embedding-based retrieval (RAG)

    Returns (topics, chunks).
    """
    text = extract_text(file_bytes)
    if not text.strip():
        raise ValueError(
            "Could not find any selectable text in this PDF -- it may be "
            "scanned images rather than real text."
        )

    sections = _split_into_sections(text)
    topics = _build_topics(sections, subject_id_prefix)
    if not topics:
        raise ValueError("This PDF didn't contain enough text to build any topics from.")

    chunks = _build_chunks(sections, subject_id_prefix)
    return topics, chunks
