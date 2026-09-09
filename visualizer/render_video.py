"""
Visual Compiler — content-aware automatic video renderer (no browser)

Each chunk is classified AND its actual content is extracted
(real terms, numbers, steps, events from the transcript — not
hardcoded examples). Renderers draw that extracted content.

Pipeline:
  video -> ffmpeg (extract audio) -> faster-whisper (transcribe)
        -> llama3.2:1b (classify + extract content per chunk)
        -> Pillow (draw frames using extracted content)
        -> ffmpeg (encode frames -> MP4, muxed with original audio)

Output: saved to <OUTPUT_DIR>/<video_name>_visualized.mp4 (OUTPUT_DIR defaults
to ./output for CLI use; the web app in tutor_backend/visualize_service.py
points it at a per-job folder instead).

Requirements:
    pip install faster-whisper pillow numpy matplotlib
    ffmpeg on PATH
    ollama pull llama3.2:1b (or whatever TUTOR_MODEL_NAME is set to)

Normally this module isn't run directly -- the "Visualize" page in the
tutor web app drives it via tutor_backend/visualize_service.py, going
through the same Ollama endpoint (config.py / llm_client.py) as the rest
of the tutor. For standalone CLI use, run it as part of the package from
the directory *above* tutor_backend/ (relative imports need the package
context):

Usage:
    python -m tutor_backend.visualizer.render_video lecture.mp4
    python -m tutor_backend.visualizer.render_video lecture.mp4 --rebuild
    python -m tutor_backend.visualizer.render_video lecture.mp4 --layout=pip
"""

import io
import json
import math
import re
import subprocess
import sys
import tempfile
import pathlib
import textwrap
import urllib.request
import urllib.error

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from faster_whisper import WhisperModel

import matplotlib
matplotlib.use("Agg")  # headless — this process never opens a display
import matplotlib.pyplot as plt

from .. import config
from .. import llm_client


# ── Config ────────────────────────────────────────────────────────────────────

# Chunk boundaries follow natural speech pauses instead of a blind fixed
# window. A 15s clock-based cut can slice a sentence — and the concept it's
# making — clean in half right when the classifier needs full context.
# Silence between Whisper segments is a much sturdier signal for "the
# teacher moved on" than an arbitrary timestamp.
MIN_CHUNK_SECONDS = 6       # never flush a chunk shorter than this on a pause alone
MAX_CHUNK_SECONDS = 25      # force a flush even mid-sentence if speech runs this long with no pause
PAUSE_THRESHOLD_SECONDS = 0.6  # gap between segments long enough to count as "the teacher paused"
WHISPER_MODEL_SIZE = "base"
FPS = 30
W, H = 1280, 720
OUTPUT_DIR = pathlib.Path("output")

# How the original lecture footage relates to the generated visuals in the
# final video. "full" discards the original picture entirely (only its
# audio survives) — the original behavior. "pip" keeps a small window of
# the original footage in the corner. "splitscreen" puts them side by side.
LAYOUTS = ["full", "pip", "splitscreen"]
DEFAULT_LAYOUT = "full"


# ── Colors ────────────────────────────────────────────────────────────────────

COLORS = {
    "process":    (239, 159, 39),
    "data":       (29, 158, 117),
    "narrative":  (216, 90, 48),
    "conceptual": (127, 119, 221),
    "spatial":    (55, 138, 221),
    "scene":      (212, 83, 126),
    "formula":    (64, 196, 180),
    "ignore":     (90, 90, 86),
}
BG = (15, 15, 18)
PANEL = (26, 26, 32)
TEXT_DIM = (110, 110, 110)
TEXT_BRIGHT = (232, 230, 223)


def load_font(size, bold=False):
    # Cross-platform candidates: Windows, then Linux (DejaVu, Liberation),
    # then macOS. Any single missing font used to be silently fine, but
    # running this on a non-Windows box meant EVERY font call fell through
    # to load_default() -> a tiny fixed-size bitmap font that made all six
    # visualizations unreadable at 1280x720. Now we actually find a real
    # scalable font on whatever OS this runs on.
    candidates = (
        [
            "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ] if bold else [
            "C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
        ]
    )
    for path in candidates:
        if pathlib.Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Last resort: still try to get a *scalable* default at the right size
    # (Pillow >= 10.1 supports this); only fall back to the tiny bitmap
    # font if that fails too.
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        print(f"  [font] WARNING: no scalable font found — text will render tiny. "
              f"Install fonts-dejavu (Linux) or similar.")
        return ImageFont.load_default()

FONT_XL = load_font(28, bold=True)
FONT_LG = load_font(22, bold=True)
FONT_MD = load_font(15)
FONT_SM = load_font(12)


# ── Step 1: classification (lean, proven reliable on its own) ──────────────

DOMAINS = ["conceptual", "data", "narrative", "process", "scene", "spatial", "formula", "ignore"]
ACTIONS = ["NEW_SCENE", "UPDATE", "CONTINUE", "IGNORE"]

CLASSIFY_PROMPT = """\
You classify lecture transcript chunks for a visual renderer.

OUTPUT: one JSON object only. No markdown. No explanation. No extra text.

DOMAIN RULES:
- process   : step-by-step algorithm or physical process
- conceptual: definitions, relationships, abstract ideas
- data       : numbers, statistics, comparisons
- narrative  : historical story or timeline
- spatial    : geometry, vectors, coordinates, transforms, cross/dot products
- scene      : real-world simulation with moving objects
- formula    : a named equation being derived, stated, or rearranged (e.g. F=ma, kinematics, Maxwell's equations)
- ignore     : filler, silence, off-topic

ACTION RULES:
- NEW_SCENE : topic changed from previous chunk
- UPDATE    : same topic, new information to add
- CONTINUE  : same topic, keep animating
- IGNORE    : filler — do not change the canvas

EXAMPLES:

Input: "Binary search cuts the array in half each time. Check the middle — go left if smaller, right if larger."
Output: {"visual_domain":"process","scene_action":"NEW_SCENE","confidence":0.92,"keywords":["binary search","cuts","half","middle","left","right"]}

Input: "Now we want the torque about point s due to gravity acting on particle j. The vector r_sj points from s to the particle."
Output: {"visual_domain":"spatial","scene_action":"NEW_SCENE","confidence":0.93,"keywords":["torque","r_sj","gravity","vector"]}

Input: "World population hit 8 billion in 2022. It took only 12 years to add the last billion."
Output: {"visual_domain":"data","scene_action":"NEW_SCENE","confidence":0.95,"keywords":["8 billion","2022","12 years"]}

Input: "Newton's second law says force equals mass times acceleration, F equals m a. Rearranging, acceleration is force over mass."
Output: {"visual_domain":"formula","scene_action":"NEW_SCENE","confidence":0.94,"keywords":["F=ma","force","mass","acceleration"]}

Input: "The French Revolution began in 1789. The Bastille fell on July 14th."
Output: {"visual_domain":"narrative","scene_action":"NEW_SCENE","confidence":0.91,"keywords":["1789","Bastille","July 14th"]}

Input: "Jupiter has 95 moons. The Great Red Spot is a storm lasting 350 years."
Output: {"visual_domain":"scene","scene_action":"NEW_SCENE","confidence":0.87,"keywords":["Jupiter","moons","storm"]}

Input: "Uh... so anyway. Let me check my notes. Hold on."
Output: {"visual_domain":"ignore","scene_action":"IGNORE","confidence":0.97,"keywords":["uh","check notes"]}

Now classify the input below. Output JSON only.\
"""


_FALLBACK_CLASSIFICATION = {"visual_domain": "ignore", "scene_action": "IGNORE",
                             "confidence": 0.0, "keywords": []}


def classify(text: str, previous_domain: str | None = None) -> dict:
    context = ""
    if previous_domain and previous_domain != "ignore":
        context = (
            f' (previous chunk was classified as "{previous_domain}". '
            f'Re-classify THIS chunk on its own merits.)'
        )

    # The Ollama call itself (daemon not running, timeout, model not pulled)
    # used to be unguarded — one bad chunk would throw and kill the entire
    # multi-minute render with everything already processed lost. Now a
    # failed call just degrades that one chunk to "ignore" and we keep going.
    try:
        raw = llm_client.call_llm(CLASSIFY_PROMPT, f'Input{context}: "{text}"').strip()
    except RuntimeError as e:
        print(f"  [classify] LLM call FAILED: {e}")
        return dict(_FALLBACK_CLASSIFICATION)

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    raw = match.group() if match else raw

    try:
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("classification response was not a JSON object")
    except (json.JSONDecodeError, ValueError):
        print(f"  [classify] PARSE FAILED — raw: {raw[:150]!r}")
        result = dict(_FALLBACK_CLASSIFICATION)

    result["visual_domain"] = str(result.get("visual_domain", "ignore")).lower().strip()
    result["scene_action"]  = str(result.get("scene_action",  "IGNORE")).upper().strip()
    try:
        result["confidence"] = round(float(result.get("confidence", 0.0)), 2)
    except (TypeError, ValueError):
        result["confidence"] = 0.0
    if not isinstance(result.get("keywords"), list):
        result["keywords"] = []
    if result["visual_domain"] not in DOMAINS: result["visual_domain"] = "ignore"
    if result["scene_action"]  not in ACTIONS: result["scene_action"]  = "IGNORE"
    return result


# ── Step 2: content extraction (separate call, schema scoped to ONE domain) ─
# A small model handles "extract these 2-3 fields" far more reliably than
# "classify AND extract a nested schema" in one shot. Splitting the work
# into two focused calls fixes the JSON truncation / YAML-drift seen when
# both tasks were combined.

CONTENT_SCHEMAS = {
    "process": {
        "instructions": "Extract the actual steps of the process described.",
        "example_input": "Binary search cuts the array in half. Check the middle, go left if smaller, right if larger.",
        "example_output": '{"title": "Binary Search", "steps": [{"label": "Check middle", "detail": "Compare target to middle element"}, {"label": "Go left", "detail": "If target is smaller than middle"}, {"label": "Go right", "detail": "If target is larger than middle"}]}',
    },
    "data": {
        "instructions": "Extract the actual numbers and what they represent.",
        "example_input": "World population hit 8 billion in 2022, up from 7 billion in 2011.",
        "example_output": '{"title": "World population growth", "points": [{"label": "2011", "value": "7 billion"}, {"label": "2022", "value": "8 billion"}]}',
    },
    "conceptual": {
        "instructions": "Extract the main term being defined and the terms it relates to.",
        "example_input": "Torque depends on the position vector and the force applied at that point.",
        "example_output": '{"central_term": "Torque", "related": [{"term": "position vector", "relation": "determines lever arm"}, {"term": "force", "relation": "applied at the point"}]}',
    },
    "spatial": {
        "instructions": "Extract the actual vector/variable names used in the transcript (e.g. r_sj, F, v) and what each represents. Never use placeholder names like 'A' or 'B' unless those exact letters were spoken.",
        "example_input": "The vector r_sj points from s to particle j. The force on it is m_j times g.",
        "example_output": '{"title": "Position and force vectors", "vectors": [{"name": "r_sj", "description": "position vector from s to particle j"}, {"name": "m_j * g", "description": "gravitational force on particle j"}]}',
    },
    "narrative": {
        "instructions": "Extract the actual events, dates, and names mentioned.",
        "example_input": "The French Revolution began in 1789. The Bastille fell on July 14th.",
        "example_output": '{"title": "French Revolution", "events": [{"marker": "1789", "label": "Revolution begins", "detail": "Food crisis and tax burden"}, {"marker": "Jul 14", "label": "Bastille falls", "detail": "Mob storms the prison"}]}',
    },
    "scene": {
        "instructions": "Extract the actual objects/entities described and a fact about each.",
        "example_input": "Jupiter has 95 moons. The Great Red Spot is a storm lasting 350 years.",
        "example_output": '{"title": "Jupiter", "objects": [{"name": "Jupiter", "detail": "has 95 known moons"}, {"name": "Great Red Spot", "detail": "storm lasting 350 years"}]}',
    },
    "formula": {
        "instructions": "Extract the actual equation(s) spoken, written in plain matplotlib-mathtext notation "
                         "(e.g. 'F = ma', 'a = F/m', 'x = x_0 + v t', 'E = mc^2') — use ^ for exponents and "
                         "_ for subscripts, no LaTeX backslash commands. Never invent an equation that wasn't said.",
        "example_input": "Newton's second law says force equals mass times acceleration, F equals m a. Rearranging, acceleration is force over mass.",
        "example_output": '{"title": "Newton\'s Second Law", "expressions": [{"latex": "F = ma", "description": "force equals mass times acceleration"}, {"latex": "a = F/m", "description": "rearranged for acceleration"}]}',
    },
}


def _try_parse_json(raw: str):
    """Attempt to parse JSON, repairing common small-model mistakes."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    # Find the first '{' and match braces to get a balanced object,
    # rather than a greedy regex that can overshoot or undershoot.
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    end = None
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
    if end is None:
        # Unterminated object (likely truncated by num_predict) —
        # try to close it off by trimming to the last complete
        # key/value pair and appending closing brackets.
        candidate = raw[start:]
    else:
        candidate = raw[start:end]

    # First attempt: parse as-is.
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    # Repair attempt: strip trailing commas before } or ], which is
    # the most common llama3.2:1b mistake.
    repaired = re.sub(r",\s*([}\]])", r"\1", candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Repair attempt: if truncated mid-object, trim back to the last
    # complete top-level field and close the structure.
    if end is None:
        last_comma = repaired.rfind(",")
        if last_comma != -1:
            trimmed = repaired[:last_comma]
            open_braces = trimmed.count("{") - trimmed.count("}")
            open_brackets = trimmed.count("[") - trimmed.count("]")
            trimmed += "]" * open_brackets + "}" * open_braces
            try:
                return json.loads(trimmed)
            except json.JSONDecodeError:
                pass

    return None


def extract_content(text: str, domain: str) -> dict:
    if domain not in CONTENT_SCHEMAS:
        return {}

    schema = CONTENT_SCHEMAS[domain]
    system = f"""\
{schema['instructions']}
Use ONLY terms, numbers, and names ACTUALLY said in the transcript below — never copy
the example's content, never invent unrelated facts. Output valid JSON only, matching
the exact field names from the example. No markdown, no explanation, no extra text.
Keep it to at most 4 items in any list field.

Example input: "{schema['example_input']}"
Example output: {schema['example_output']}\
"""
    user = f'Now extract from this transcript:\nTranscript: "{text}"\nOutput:'

    for attempt in range(2):  # one retry if the first parse fails
        try:
            raw = llm_client.call_llm(system, user).strip()
            content = _try_parse_json(raw)

            if content is None or not isinstance(content, dict):
                print(f"  [extract_content] parse failed (attempt {attempt+1}) for domain={domain} — raw: {raw[:150]!r}")
                continue

            if json.dumps(content, sort_keys=True) == json.dumps(json.loads(schema["example_output"]), sort_keys=True):
                print(f"  [extract_content] WARNING: model echoed the example for domain={domain}, discarding")
                return {}

            return content
        except RuntimeError as e:
            print(f"  [extract_content] FAILED (attempt {attempt+1}) for domain={domain}: {e}")

    return {}


# ── Audio extraction + transcription ────────────────────────────────────────

def extract_audio(video_path: str, out_path: str) -> None:
    cmd = ["ffmpeg", "-y", "-i", video_path, "-vn",
           "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", out_path]
    print("[ffmpeg] extracting audio ...")
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")
        raise RuntimeError(
            f"ffmpeg failed to extract audio from '{video_path}'. "
            f"This usually means the file isn't a valid/readable video, or has no "
            f"audio track. ffmpeg said:\n{stderr[-800:]}"
        )


def transcribe_chunks(audio_path: str, model: WhisperModel,
                       min_chunk: float = MIN_CHUNK_SECONDS,
                       max_chunk: float = MAX_CHUNK_SECONDS,
                       pause_threshold: float = PAUSE_THRESHOLD_SECONDS):
    """
    Yields (start, end, text) chunks cut on natural speech pauses rather
    than a fixed clock window. A chunk flushes when either:
      - there's a pause of at least `pause_threshold` seconds since the
        last segment ended, AND the chunk so far is at least `min_chunk`
        seconds (so we don't produce a string of tiny fragments every
        time the teacher takes a breath), or
      - the chunk has run past `max_chunk` seconds with no pause at all
        (continuous fast speech still needs to be cut sometime, or one
        chunk could span the whole lecture).
    """
    print("[whisper] transcribing ...")
    segments, info = model.transcribe(audio_path, beam_size=5)
    print(f"[whisper] language: {info.language} (p={info.language_probability:.2f})")

    buffer_text = []
    buffer_start = None
    prev_end = None

    try:
        for seg in segments:
            text = seg.text.strip()
            if not text:
                continue

            if buffer_start is None:
                buffer_start = seg.start

            gap = (seg.start - prev_end) if prev_end is not None else 0.0
            buffer_duration = (prev_end - buffer_start) if prev_end is not None else 0.0

            should_flush = buffer_text and (
                (gap >= pause_threshold and buffer_duration >= min_chunk)
                or buffer_duration >= max_chunk
            )
            if should_flush:
                yield (buffer_start, prev_end, " ".join(buffer_text).strip())
                buffer_text = []
                buffer_start = seg.start

            buffer_text.append(text)
            prev_end = seg.end
    except Exception as e:
        print(f"  [whisper] ERROR: {e}")

    if buffer_text:
        yield (buffer_start, prev_end, " ".join(buffer_text).strip())


# ── Text helpers ──────────────────────────────────────────────────────────────

def wrap_text(text, width):
    return textwrap.wrap(str(text), width=width) or [""]


# A 1B model extracting into a nested JSON schema will occasionally hand
# back a string where a dict was expected (e.g. a "step" as plain text
# instead of {"label":...,"detail":...}), or a number where a string was
# expected. Every render_* function calls .get()/str-slicing on these
# items, so one malformed item used to throw AttributeError/TypeError and
# kill the whole render loop for the rest of the video. These two helpers
# make that data access crash-proof everywhere it's used below.
def sd(item) -> dict:
    """Coerce to a dict-like item safely; wraps stray strings as a label."""
    if isinstance(item, dict):
        return item
    if item is None:
        return {}
    return {"label": str(item)}


def sl(value) -> list:
    """Coerce to a list safely."""
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def st(value, limit=None) -> str:
    """Coerce to a string safely, optionally truncated."""
    s = "" if value is None else str(value)
    return s[:limit] if limit else s


# ── Frame drawing — content-aware per domain ────────────────────────────────

def draw_hud(draw, result, t_in_chunk, chunk_duration):
    domain = result["visual_domain"]
    c = COLORS.get(domain, COLORS["ignore"])

    draw.rectangle([0, 0, W, 56], fill=PANEL)
    draw.rounded_rectangle([20, 14, 160, 42], radius=14, fill=tuple(int(x*0.25) for x in c))
    draw.text((30, 19), domain.upper(), font=FONT_SM, fill=c)
    draw.text((180, 19), result["scene_action"], font=FONT_SM, fill=TEXT_DIM)

    kw = " · ".join(st(k) for k in sl(result.get("keywords"))[:5])
    draw.text((W - 20, 19), kw, font=FONT_SM, fill=(74, 158, 255), anchor="ra")

    pct = t_in_chunk / chunk_duration if chunk_duration else 0
    draw.rectangle([0, 54, W, 56], fill=(30, 30, 36))
    draw.rectangle([0, 54, int(W * pct), 56], fill=c)


def render_process(img, draw, t, result):
    content = sd(result.get("content"))
    title = st(content.get("title", "Process"))
    steps = [sd(s) for s in sl(content.get("steps"))]
    if not steps:
        steps = [{"label": "No steps extracted", "detail": ""}]

    draw.text((W // 2, 90), title, font=FONT_LG, fill=COLORS["process"], anchor="ma")

    # cycle through steps over time, highlighting the active one
    cycle_time = 2.5
    active_idx = int(t / cycle_time) % len(steps)

    y0 = 160
    row_h = min(95, (H - 220) // max(len(steps), 1))
    box_x0, box_x1 = 140, W - 140

    for i, step in enumerate(steps):
        y = y0 + i * row_h
        is_active = (i == active_idx)
        fill = COLORS["process"] if is_active else (32, 32, 38)
        text_color = (20, 20, 20) if is_active else TEXT_BRIGHT
        detail_color = (40, 30, 10) if is_active else TEXT_DIM

        draw.rounded_rectangle([box_x0, y, box_x1, y + row_h - 14], radius=10, fill=fill)
        draw.text((box_x0 + 24, y + 14), f"{i+1}. {st(step.get('label',''))}", font=FONT_MD, fill=text_color)
        detail_lines = wrap_text(step.get("detail", ""), 90)
        for j, line in enumerate(detail_lines[:2]):
            draw.text((box_x0 + 24, y + 40 + j*18), line, font=FONT_SM, fill=detail_color)


def render_data(img, draw, t, result):
    content = sd(result.get("content"))
    title = st(content.get("title", "Data"))
    points = [sd(p) for p in sl(content.get("points"))]
    if not points:
        points = [{"label": "No data extracted", "value": ""}]

    draw.text((W // 2, 90), title, font=FONT_LG, fill=COLORS["data"], anchor="ma")

    chart_x, chart_y, chart_w, chart_h = 140, 160, W - 280, 400
    n = len(points)
    bar_w = chart_w / max(n, 1)

    # try to parse numeric values for bar height; fallback to equal bars
    nums = []
    for p in points:
        m = re.search(r"[-+]?\d*\.?\d+", st(p.get("value", "")))
        nums.append(float(m.group()) if m else 1.0)
    # Guard against ALL values being "0" (or non-numeric) — dividing by a
    # zero max_val used to raise ZeroDivisionError and abort the render.
    max_val = max(nums) if nums and max(nums) > 0 else 1.0

    grow = min(1.0, t / 1.2)

    for i, (point, val) in enumerate(zip(points, nums)):
        bar_h = (val / max_val) * chart_h * grow
        x0 = chart_x + i * bar_w + bar_w * 0.15
        x1 = chart_x + (i + 1) * bar_w - bar_w * 0.15
        y1 = chart_y + chart_h
        y0 = y1 - bar_h
        color = COLORS["data"] if i % 2 == 0 else COLORS["spatial"]
        draw.rounded_rectangle([x0, y0, x1, y1], radius=6, fill=color)
        draw.text(((x0+x1)/2, max(y0 - 18, chart_y)), st(point.get("value", "")), font=FONT_SM, fill=(200,200,200), anchor="ma")
        for j, line in enumerate(wrap_text(point.get("label",""), 14)[:2]):
            draw.text(((x0+x1)/2, y1 + 10 + j*16), line, font=FONT_SM, fill=(120,120,120), anchor="ma")


def render_narrative(img, draw, t, result):
    content = sd(result.get("content"))
    title = st(content.get("title", "Narrative"))
    events = [sd(e) for e in sl(content.get("events"))]
    if not events:
        events = [{"marker": "", "label": "No events extracted", "detail": ""}]

    draw.text((W // 2, 70), title, font=FONT_LG, fill=COLORS["narrative"], anchor="ma")

    y0 = 140
    row_h = min(95, (H - 200) // max(len(events), 1))
    reveal_count = min(len(events), int(t / 0.5) + 1)

    draw.line([180, y0, 180, y0 + row_h * (len(events)-1) + 20], fill=(40, 40, 46), width=2)

    palette = [COLORS["conceptual"], COLORS["narrative"], COLORS["data"], (226,75,74), COLORS["process"], COLORS["spatial"]]

    for i, ev in enumerate(events[:reveal_count]):
        y = y0 + i * row_h
        c = palette[i % len(palette)]
        draw.ellipse([175, y-5, 185, y+5], fill=c)
        draw.text((150, y), st(ev.get("marker","")), font=FONT_SM, fill=c, anchor="rm")
        draw.text((200, y - 14), st(ev.get("label","")), font=FONT_MD, fill=TEXT_BRIGHT, anchor="lm")
        for j, line in enumerate(wrap_text(ev.get("detail",""), 80)[:1]):
            draw.text((200, y + 10), line, font=FONT_SM, fill=(110,110,110), anchor="lm")


def _ease_out_back(p: float) -> float:
    """0..1 -> 0..1 with a slight overshoot, like d3's default enter feel."""
    p = max(0.0, min(1.0, p))
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def _lerp_color(c, bg, p):
    p = max(0.0, min(1.0, p))
    return tuple(int(bg[i] + (c[i] - bg[i]) * p) for i in range(3))


# Nodes stream in one at a time (like the addRelation()/playStream() loop in
# the D3 mock: one node+link added every STAGGER_SECONDS, each with its own
# short grow/fade-in). Central node is always present immediately.
STAGGER_SECONDS = 0.6
GROW_SECONDS = 0.5


def render_conceptual(img, draw, t, result):
    content = sd(result.get("content"))
    central = st(content.get("central_term", "Concept"))
    related = [sd(r) for r in sl(content.get("related"))]
    if not related:
        related = [{"term": "No related terms extracted", "relation": ""}]

    cx, cy = W // 2, H // 2 + 20
    n = len(related)
    radius_orbit = 230

    # Central node fades/grows in over the first GROW_SECONDS, same curve
    # every other node uses, so the whole thing reads as one animation style.
    central_p = _ease_out_back(t / GROW_SECONDS)
    central_r = 65 * max(central_p, 0.0)
    if central_r > 0.5:
        c0 = COLORS["conceptual"]
        draw.ellipse([cx-central_r, cy-central_r, cx+central_r, cy+central_r],
                     outline=_lerp_color(c0, BG, central_p), width=2,
                     fill=tuple(int(v*0.18*central_p) for v in c0))
    if central_p > 0.5:
        for line_i, line in enumerate(wrap_text(central, 14)[:2]):
            draw.text((cx, cy - 8 + line_i*18), line, font=FONT_MD,
                      fill=_lerp_color(COLORS["conceptual"], BG, central_p), anchor="mm")

    palette = [COLORS["data"], COLORS["process"], COLORS["narrative"], COLORS["spatial"], COLORS["scene"], (226,75,74)]

    for i, rel in enumerate(related):
        # this node's own entrance starts once the central node has settled
        # and its predecessor has had its turn — mirrors the 1-per-tick reveal.
        node_start = GROW_SECONDS + i * STAGGER_SECONDS
        local_t = t - node_start
        if local_t <= 0:
            continue  # not this node's turn yet — matches d3 .enter() not having run
        p = _ease_out_back(local_t / GROW_SECONDS)

        angle = (2 * math.pi * i / max(n, 1)) - math.pi/2
        x = cx + math.cos(angle) * radius_orbit
        y = cy + math.sin(angle) * radius_orbit
        c = palette[i % len(palette)]
        r = 48 * max(p, 0.0)

        link_p = min(1.0, local_t / (GROW_SECONDS * 0.6))
        lx = cx + (x - cx) * link_p
        ly = cy + (y - cy) * link_p
        draw.line([cx, cy, lx, ly], fill=(40, 40, 48), width=2)

        if r > 0.5:
            draw.ellipse([x-r, y-r, x+r, y+r], outline=_lerp_color(c, BG, p), width=2,
                         fill=tuple(int(v*0.15*p) for v in c))
        if p > 0.5:
            for line_i, line in enumerate(wrap_text(str(rel.get("term","")), 12)[:2]):
                draw.text((x, y - 6 + line_i*16), line, font=FONT_SM,
                          fill=_lerp_color(c, BG, p), anchor="mm")

            mx, my = (cx+x)/2, (cy+y)/2
            rel_text = str(rel.get("relation", ""))[:24]
            if rel_text:
                draw.text((mx, my), rel_text, font=FONT_SM,
                          fill=_lerp_color((90, 90, 90), BG, p), anchor="mm")


def render_spatial(img, draw, t, result):
    content = sd(result.get("content"))
    title = st(content.get("title", "Spatial relationship"))
    vectors = [sd(v) for v in sl(content.get("vectors"))]
    if not vectors:
        vectors = [{"name": "v", "description": "No vectors extracted"}]

    cx, cy = W // 2, H // 2 + 20

    draw.text((W // 2, 70), title, font=FONT_LG, fill=COLORS["spatial"], anchor="ma")

    for i in range(-6, 7):
        draw.line([cx + i*55, 110, cx + i*55, H-100], fill=(35, 35, 42), width=1)
        draw.line([100, cy + i*55, W-100, cy + i*55], fill=(35, 35, 42), width=1)
    draw.line([100, cy, W-100, cy], fill=(55, 55, 64), width=1)
    draw.line([cx, 110, cx, H-100], fill=(55, 55, 64), width=1)

    palette = [COLORS["conceptual"], COLORS["data"], COLORS["narrative"], COLORS["process"], (226,75,74)]
    n = len(vectors)

    for i, vec in enumerate(vectors[:5]):
        base_angle = (2 * math.pi * i / max(n, 1))
        wobble = math.sin(t * 0.6 + i) * 0.15
        length = 140 + (i % 2) * 40
        vx = math.cos(base_angle + wobble) * length
        vy = math.sin(base_angle + wobble) * length * 0.7

        c = palette[i % len(palette)]
        ex, ey = cx + vx, cy + vy
        draw.line([cx, cy, ex, ey], fill=c, width=3)
        ang = math.atan2(vy, vx)
        ah = 14
        p1 = (ex - ah*math.cos(ang-0.4), ey - ah*math.sin(ang-0.4))
        p2 = (ex - ah*math.cos(ang+0.4), ey - ah*math.sin(ang+0.4))
        draw.polygon([(ex,ey), p1, p2], fill=c)

        name = str(vec.get("name", f"v{i+1}"))
        draw.text((ex + 18*math.cos(ang), ey + 18*math.sin(ang)), name, font=FONT_MD, fill=c, anchor="mm")

    # single-line combined legend at bottom to avoid overflow
    legend_parts = [f"{st(v.get('name','?'))}: {st(v.get('description',''), 30)}" for v in vectors[:3]]
    draw.text((W//2, H-40), "   |   ".join(legend_parts), font=FONT_SM, fill=(140,140,140), anchor="ma")


def render_scene(img, draw, t, result):
    content = sd(result.get("content"))
    title = st(content.get("title", "Scene"))
    objects = [sd(o) for o in sl(content.get("objects"))]
    if not objects:
        objects = [{"name": "?", "detail": "No objects extracted"}]

    cx, cy = W // 2, H // 2 + 10
    draw.rectangle([0, 56, W, H], fill=(8, 8, 14))
    draw.text((W // 2, 80), title, font=FONT_LG, fill=COLORS["scene"], anchor="ma")

    for i in range(100):
        sx, sy = (i*137+42) % W, (i*97+13) % (H-130) + 130
        b = 60 + (i % 3) * 30
        draw.point((sx, sy), fill=(b, b, b))

    palette = [(255,230,110), COLORS["spatial"], COLORS["process"], COLORS["data"], COLORS["narrative"], COLORS["conceptual"]]
    n = len(objects)

    for i, obj in enumerate(objects[:6]):
        orbit = 100 + i * 70
        speed = 0.5 - i * 0.06
        ang = t * speed + i
        r = 14 + (4 if i == 0 else 0)
        color = palette[i % len(palette)]

        draw.ellipse([cx-orbit, cy-orbit, cx+orbit, cy+orbit], outline=(28,28,36), width=1)
        px = cx + math.cos(ang) * orbit
        py = cy + math.sin(ang) * orbit * 0.6
        draw.ellipse([px-r, py-r, px+r, py+r], fill=color)
        draw.text((px, py + r + 14), str(obj.get("name", "?")), font=FONT_SM, fill=(170,170,170), anchor="ma")

    # detail captions at bottom
    detail_parts = [f"{o.get('name','?')}: {str(o.get('detail',''))[:28]}" for o in objects[:3]]
    draw.text((W//2, H-30), "   |   ".join(detail_parts), font=FONT_SM, fill=(120,120,120), anchor="ma")


# matplotlib's mathtext renders $...$ expressions without needing a LaTeX
# install — good enough for the algebraic notation a lecture transcript
# extracts (superscripts, subscripts, greek letters, fractions). Each
# distinct (expression, color, size) is rasterized once to a transparent
# PNG and cached — re-running matplotlib every frame for a formula that's
# on screen for seconds at a time would be needless work.
_FORMULA_IMAGE_CACHE = {}


def render_formula_image(latex_str: str, color=(232, 230, 223), fontsize=40):
    """Rasterize a math expression to an RGBA PIL Image with a transparent
    background, or None if mathtext couldn't parse it (bad LLM output —
    the caller falls back to plain text rather than losing the frame)."""
    cache_key = (latex_str, color, fontsize)
    if cache_key in _FORMULA_IMAGE_CACHE:
        return _FORMULA_IMAGE_CACHE[cache_key]

    expr = latex_str.strip()
    if not expr:
        return None
    # mathtext needs $...$ delimiters; the model is inconsistent about
    # including them, so add them only if missing.
    if not (expr.startswith("$") and expr.endswith("$")):
        expr = f"${expr}$"

    img = None
    fig = plt.figure()
    try:
        text_artist = fig.text(0, 0, expr, fontsize=fontsize,
                                color=tuple(c / 255 for c in color))
        fig.canvas.draw()
        bbox = text_artist.get_window_extent()
        pad = 16
        width_px, height_px = int(bbox.width) + pad * 2, int(bbox.height) + pad * 2
        fig.set_size_inches(width_px / fig.dpi, height_px / fig.dpi)
        text_artist.set_position((pad / width_px, pad / height_px))

        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True, dpi=fig.dpi)
        buf.seek(0)
        img = Image.open(buf).convert("RGBA")
        img.load()  # decode fully now — the BytesIO buffer won't outlive this function
    except Exception as e:
        print(f"  [formula] mathtext render FAILED for {latex_str!r}: {e} — falling back to plain text")
    finally:
        plt.close(fig)

    _FORMULA_IMAGE_CACHE[cache_key] = img
    return img


def render_formula(img, draw, t, result):
    content = sd(result.get("content"))
    title = st(content.get("title", "Formula"))
    expressions = [sd(e) for e in sl(content.get("expressions"))]
    if not expressions:
        expressions = [{"latex": "", "description": "No formula extracted"}]

    draw.text((W // 2, 80), title, font=FONT_LG, fill=COLORS["formula"], anchor="ma")

    # Cycle through multiple expressions one at a time, same idea as
    # render_process's step-highlighting, rather than cramming them all on
    # screen together.
    cycle_time = 3.0
    active_idx = int(t / cycle_time) % len(expressions)
    local_t = t - active_idx * cycle_time
    expr = expressions[active_idx]
    latex_str = st(expr.get("latex", ""))
    description = st(expr.get("description", ""))

    cx, cy = W // 2, H // 2 - 20
    p = _ease_out_back(local_t / GROW_SECONDS) if local_t < GROW_SECONDS else 1.0
    p = max(0.0, p)

    formula_img = render_formula_image(latex_str, color=COLORS["formula"], fontsize=44) if latex_str else None

    if formula_img is not None and p > 0.02:
        fw, fh = formula_img.size
        scale = max(0.3, min(1.0, p))
        scaled = formula_img.resize((max(1, int(fw * scale)), max(1, int(fh * scale))))
        sw, sh = scaled.size
        paste_x, paste_y = cx - sw // 2, cy - sh // 2
        alpha = scaled.split()[3].point(lambda a: int(a * min(1.0, p)))
        img.paste(scaled, (paste_x, paste_y), alpha)
    elif not latex_str:
        draw.text((cx, cy), "No formula extracted", font=FONT_MD, fill=TEXT_DIM, anchor="mm")

    if description and p > 0.3:
        for j, line in enumerate(wrap_text(description, 80)[:2]):
            draw.text((cx, cy + 110 + j * 20), line, font=FONT_SM,
                      fill=_lerp_color(TEXT_DIM, BG, min(1.0, p)), anchor="ma")

    # dots marking position among multiple expressions, like a carousel
    if len(expressions) > 1:
        dot_y = H - 60
        total_w = len(expressions) * 20
        start_x = cx - total_w // 2
        for i in range(len(expressions)):
            dx = start_x + i * 20 + 10
            c = COLORS["formula"] if i == active_idx else (50, 50, 56)
            draw.ellipse([dx - 4, dot_y - 4, dx + 4, dot_y + 4], fill=c)


RENDERERS = {
    "process": render_process, "data": render_data, "narrative": render_narrative,
    "conceptual": render_conceptual, "spatial": render_spatial, "scene": render_scene,
    "formula": render_formula,
}


def render_idle_frame():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.text((W//2, H//2), "Visual Compiler", font=FONT_XL, fill=(60,60,66), anchor="mm")
    return img


# ── Analysis cache ───────────────────────────────────────────────────────────
# Transcription + classification + extraction is the expensive, slow part
# (whisper pass + one or two LLM calls per chunk). Rendering is cheap and
# you'll want to iterate on it repeatedly — colors, layout, fps, adding a
# domain — without re-running the whole front half every time. So the full
# per-chunk analysis gets written to a plain JSON file next to the output
# video. If it's still there (and settings haven't changed) on the next run,
# we load it straight in and skip transcription + every LLM call entirely.
#
# This also makes a human review step possible for free: open the JSON,
# fix a wrong classification or a bad extraction by hand, save, re-run —
# the render will pick up your edits without touching Whisper or Ollama.

def cache_path_for(video_path: str) -> pathlib.Path:
    video_name = pathlib.Path(video_path).stem
    return OUTPUT_DIR / f"{video_name}_analysis.json"


def load_cache(path: pathlib.Path):
    """Returns the cached chunk list, or None if missing/stale/unreadable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[cache] couldn't read '{path}' ({e}) — re-analyzing")
        return None

    meta = data.get("_meta", {})
    if (meta.get("min_chunk_seconds") != MIN_CHUNK_SECONDS
            or meta.get("max_chunk_seconds") != MAX_CHUNK_SECONDS
            or meta.get("pause_threshold_seconds") != PAUSE_THRESHOLD_SECONDS
            or meta.get("whisper_model") != WHISPER_MODEL_SIZE):
        print(f"[cache] '{path}' was built with different settings "
              f"(min/max/pause={meta.get('min_chunk_seconds')}/"
              f"{meta.get('max_chunk_seconds')}/{meta.get('pause_threshold_seconds')}, "
              f"whisper_model={meta.get('whisper_model')!r}) — re-analyzing")
        return None

    raw_chunks = data.get("chunks")
    if not raw_chunks:
        return None

    chunks = []
    for c in raw_chunks:
        if "start" not in c or "end" not in c:
            print(f"[cache] malformed entry in '{path}' (missing start/end) — re-analyzing")
            return None
        start, end = c["start"], c["end"]
        result = {k: v for k, v in c.items() if k not in ("start", "end")}

        # This file may have been hand-edited (that's the point — see
        # review_analysis.py). A typo in "visual_domain" or "scene_action"
        # here would otherwise slip past validation and either KeyError
        # deep in a renderer or just silently render as nothing. Coerce it
        # the same way classify() does for LLM output, so a bad edit just
        # falls back to "ignore" with a clear message instead of a crash
        # partway through a render.
        domain = str(result.get("visual_domain", "ignore")).lower().strip()
        if domain not in DOMAINS:
            print(f"[cache] chunk {start:.0f}-{end:.0f}s has unrecognized "
                  f"visual_domain '{domain}' — treating as 'ignore'")
            domain = "ignore"
        result["visual_domain"] = domain

        action = str(result.get("scene_action", "IGNORE")).upper().strip()
        if action not in ACTIONS:
            action = "IGNORE"
        result["scene_action"] = action

        if not isinstance(result.get("content"), dict):
            result["content"] = {}
        if not isinstance(result.get("keywords"), list):
            result["keywords"] = []

        chunks.append((start, end, result))
    return chunks


def save_cache(path: pathlib.Path, chunks: list) -> None:
    data = {
        "_meta": {
            "min_chunk_seconds": MIN_CHUNK_SECONDS,
            "max_chunk_seconds": MAX_CHUNK_SECONDS,
            "pause_threshold_seconds": PAUSE_THRESHOLD_SECONDS,
            "whisper_model": WHISPER_MODEL_SIZE,
            "llm_model": config.MODEL_NAME,
        },
        "chunks": [{"start": s, "end": e, **r} for s, e, r in chunks],
    }
    try:
        path.write_text(json.dumps(data, indent=2))
        print(f"[cache] saved analysis to '{path}' — edit this file and re-run "
              f"to change content without re-transcribing, or delete it / pass "
              f"--rebuild to force a fresh analysis")
    except OSError as e:
        print(f"[cache] WARNING: couldn't write cache file ({e}) — "
              f"next run will re-analyze from scratch")


# ── Main render loop ─────────────────────────────────────────────────────────

def check_dependencies() -> bool:
    """Fail fast with a clear message instead of dying deep inside the
    pipeline (or worse, mid-render after several minutes of work)."""
    import shutil as _shutil
    ok = True

    if _shutil.which("ffmpeg") is None:
        print("[setup] ERROR: ffmpeg not found on PATH. Install it and try again "
              "(e.g. 'winget install ffmpeg', 'brew install ffmpeg', or 'apt install ffmpeg').")
        ok = False

    tags_url = config.OLLAMA_URL.replace("/api/chat", "/api/tags")
    try:
        with urllib.request.urlopen(tags_url, timeout=5) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        available = {m.get("model") or m.get("name") for m in body.get("models", [])}
        if not any(config.MODEL_NAME in (name or "") for name in available):
            print(f"[setup] ERROR: model '{config.MODEL_NAME}' not found in Ollama. "
                  f"Run: ollama pull {config.MODEL_NAME}")
            ok = False
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"[setup] ERROR: couldn't reach Ollama at {config.OLLAMA_URL} ({e}). "
              f"Make sure 'ollama serve' is running.")
        ok = False

    return ok


def composite_video(silent_video_path: str, original_video_path: str,
                     output_path: pathlib.Path, layout: str):
    """
    Combines the rendered visual track with the original lecture footage
    (or not, for "full"), and muxes in the original audio either way.
    Returns (success: bool, stderr: str).
    """
    if layout == "full":
        # Original behavior: visuals only, original picture discarded,
        # original audio kept. Cheap — just a stream copy, no re-encode.
        cmd = [
            "ffmpeg", "-y",
            "-i", silent_video_path,   # stream 0: rendered visuals (video only)
            "-i", original_video_path, # stream 1: original lecture (for its audio)
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]

    elif layout == "pip":
        # Original footage shrinks to a small box in the bottom-right
        # corner, overlaid on top of the full-frame rendered visuals.
        # scale=...:-2 preserves aspect ratio with an even height (x264
        # requires even dimensions).
        pip_w = W // 4
        margin = 20
        filter_complex = (
            f"[1:v]scale={pip_w}:-2[pip];"
            f"[0:v][pip]overlay=W-w-{margin}:H-h-{margin}[outv]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", silent_video_path,
            "-i", original_video_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]

    elif layout == "splitscreen":
        # Both tracks scaled to half width, letterboxed (black bars) to
        # the full canvas height so differing aspect ratios don't stretch,
        # then placed side by side: original footage on the left, rendered
        # visuals on the right.
        half_w = W // 2
        filter_complex = (
            f"[1:v]scale={half_w}:-2,pad={half_w}:{H}:0:(oh-ih)/2:black[left];"
            f"[0:v]scale={half_w}:-2,pad={half_w}:{H}:0:(oh-ih)/2:black[right];"
            f"[left][right]hstack=inputs=2[outv]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", silent_video_path,
            "-i", original_video_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]

    else:
        raise ValueError(f"unknown layout: {layout!r} (expected one of {LAYOUTS})")

    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0, result.stderr


def render_video(video_path: str, force_reanalyze: bool = False, layout: str = DEFAULT_LAYOUT):
    if not check_dependencies():
        print("\n[setup] Fix the issue(s) above, then re-run.")
        return

    OUTPUT_DIR.mkdir(exist_ok=True)
    video_name = pathlib.Path(video_path).stem
    output_path = OUTPUT_DIR / f"{video_name}_visualized.mp4"
    analysis_path = cache_path_for(video_path)

    # ── Analysis phase: transcribe + classify + extract, or load from cache ──
    chunks = None if force_reanalyze else load_cache(analysis_path)

    if chunks is not None:
        print(f"[cache] loaded {len(chunks)} chunks from '{analysis_path}' — "
              f"skipping transcription and all LLM calls")
    else:
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = str(pathlib.Path(tmp) / "audio.wav")
            try:
                extract_audio(video_path, audio_path)
            except RuntimeError as e:
                print(f"[setup] ERROR: {e}")
                return

            print(f"[whisper] loading model '{WHISPER_MODEL_SIZE}' ...")
            try:
                model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
            except Exception as e:
                print(f"[setup] ERROR: failed to load Whisper model: {e}")
                return

            chunks = []
            prev_domain = None
            for start, end, text in transcribe_chunks(audio_path, model):
                text = text.strip()
                if not text:
                    continue
                print(f"\n[{start:.0f}s-{end:.0f}s] {text[:70]}...")
                result = classify(text, prev_domain)
                result["input_text"] = text
                print(f"  -> {result['visual_domain']} / {result['scene_action']} ({result['confidence']})")

                # Separate call, scoped to just this domain's schema — far more
                # reliable for a 1b model than asking for classification +
                # extraction in a single combined response.
                if result["visual_domain"] != "ignore":
                    result["content"] = extract_content(text, result["visual_domain"])
                else:
                    result["content"] = {}
                print(f"  content: {json.dumps(result['content'])[:150]}")

                chunks.append((start, end, result))
                if result["visual_domain"] != "ignore":
                    prev_domain = result["visual_domain"]

        if not chunks:
            print("No speech detected — nothing to render.")
            return

        save_cache(analysis_path, chunks)

    if not chunks:
        print("No speech detected — nothing to render.")
        return

    total_duration = chunks[-1][1]
    total_frames = int(total_duration * FPS)

    print(f"\n[render] {total_frames} frames at {FPS}fps ({total_duration:.0f}s) ...")

    with tempfile.TemporaryDirectory() as tmp:

        # ── Step A: encode the rendered frames to a video-only file ──────────
        # Single input (stdin pipe), nothing else for ffmpeg to wait on —
        # this avoids the stall that happens when ffmpeg has to sync a live
        # pipe against a second file input in the same pass.
        silent_video_path = str(pathlib.Path(tmp) / "silent.mp4")

        encode_cmd = [
            "ffmpeg", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
            "-i", "-",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20", "-pix_fmt", "yuv420p",
            silent_video_path,
        ]

        proc = subprocess.Popen(
            encode_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        last_domain = None
        domain_enter_time = 0.0

        # Pause-based chunking means real gaps of silence sit between
        # consecutive chunks' (start, end) — that's the point, it's what we
        # cut on. But the frame loop below needs continuous coverage: a
        # frame that lands inside one of those gaps must still show the
        # chunk that was just finishing, not fall through to chunks[-1].
        # So build a display-only copy where each chunk's active window
        # extends to the start of the next one (or total_duration for the
        # last chunk) — the cached start/end in `chunks` itself still
        # reflects the real speech span.
        render_chunks = []
        for i, (start, end, result) in enumerate(chunks):
            render_end = chunks[i + 1][0] if i + 1 < len(chunks) else total_duration
            render_chunks.append((start, render_end, result))

        for frame_idx in range(total_frames):
            t_global = frame_idx / FPS

            active = None
            for start, end, result in render_chunks:
                if start <= t_global < end:
                    active = (start, end, result)
                    break
            if active is None:
                active = render_chunks[-1]

            start, end, result = active
            domain = result["visual_domain"]

            if domain != last_domain:
                domain_enter_time = t_global
                last_domain = domain

            t_in_domain = t_global - domain_enter_time
            chunk_duration = end - start

            # A single malformed chunk (bad LLM output that slipped past the
            # defensive coercion above, or an edge case we haven't seen) used
            # to throw here and abort the WHOLE render — losing every frame
            # already piped to ffmpeg. Now that one frame degrades to the
            # idle frame and the render keeps going for every other chunk.
            try:
                if domain == "ignore" or domain not in RENDERERS:
                    img = render_idle_frame()
                else:
                    img = Image.new("RGB", (W, H), BG)
                    draw = ImageDraw.Draw(img)
                    RENDERERS[domain](img, draw, t_in_domain, result)
                    draw_hud(draw, result, t_global - start, chunk_duration)
            except Exception as e:
                print(f"  [render] frame {frame_idx} ({domain}) FAILED, using idle frame: {e}")
                img = render_idle_frame()

            try:
                proc.stdin.write(np.array(img).tobytes())
            except BrokenPipeError:
                # ffmpeg died mid-stream — stop feeding it and surface its
                # stderr instead of raising a confusing BrokenPipeError here.
                print("\n[ffmpeg] encoder process died mid-render — log below:\n")
                print(proc.stderr.read().decode(errors="replace")[-3000:])
                return

            if frame_idx % (FPS * 5) == 0:
                print(f"  [render] {t_global:.0f}s / {total_duration:.0f}s ...")

        proc.stdin.close()
        stderr_output = proc.stderr.read().decode(errors="replace")
        proc.wait()

        if proc.returncode != 0:
            print("\n[ffmpeg] VIDEO ENCODING FAILED — log below:\n")
            print(stderr_output[-3000:])
            return

        print(f"[render] video-only encode complete: {silent_video_path}")

        # ── Step B: composite with the original footage (if any) + audio ──────
        print(f"[ffmpeg] compositing (layout={layout}) and muxing original audio ...")

        composite_ok, composite_stderr = composite_video(
            silent_video_path, video_path, output_path, layout
        )

        if not composite_ok:
            print(f"\n[ffmpeg] COMPOSITING FAILED (layout={layout}) — log below:\n")
            print(composite_stderr[-3000:])
            print(f"\n[fallback] video-only output (visuals, no audio, no original footage) "
                  f"is still available.")
            print(f"[fallback] you can find it before cleanup at: {silent_video_path}")
            # Copy the silent video to the output dir so the user isn't left with nothing.
            import shutil
            fallback_path = OUTPUT_DIR / f"{video_name}_visualized_NO_AUDIO.mp4"
            shutil.copy(silent_video_path, fallback_path)
            print(f"[fallback] saved silent version to: {fallback_path.resolve()}")
            return

        # Quick sanity check: confirm the output file actually has an audio stream.
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(output_path)],
            capture_output=True, text=True,
        )
        has_audio = "audio" in probe.stdout

        print(f"\n[done] saved to: {output_path.resolve()}")
        print(f"[done] audio track present: {'YES' if has_audio else 'NO — check ffmpeg log above'}")


if __name__ == "__main__":
    args = sys.argv[1:]
    force_reanalyze = "--rebuild" in args
    args = [a for a in args if a != "--rebuild"]

    layout = DEFAULT_LAYOUT
    remaining = []
    for a in args:
        if a.startswith("--layout="):
            layout = a.split("=", 1)[1].strip().lower()
        else:
            remaining.append(a)
    args = remaining

    if len(args) < 1:
        print("Usage: python render_video.py <path-to-lecture-video> [--rebuild] [--layout=full|pip|splitscreen]")
        print("  --rebuild        ignore any cached analysis and re-transcribe + re-classify from scratch")
        print("  --layout=full         visuals only, original footage discarded (default)")
        print("  --layout=pip          small window of original footage in the corner")
        print("  --layout=splitscreen  original footage and visuals side by side")
        sys.exit(1)

    if layout not in LAYOUTS:
        print(f"Unknown --layout={layout!r}. Choose one of: {', '.join(LAYOUTS)}")
        sys.exit(1)

    video_path = args[0]
    if not pathlib.Path(video_path).exists():
        print(f"File not found: {video_path}")
        sys.exit(1)

    print("="*60)
    print("  Visual Compiler — content-aware automatic render")
    print(f"  Input  : {video_path}")
    print(f"  Layout : {layout}")
    if force_reanalyze:
        print("  Mode   : forcing fresh analysis (--rebuild)")
    print("="*60)

    render_video(video_path, force_reanalyze=force_reanalyze, layout=layout)
