"""
Backs the "Visualize" page: upload a lecture video, watch it get
transcribed + classified chunk-by-chunk (shown live as a mindmap in the
browser), optionally correct a chunk's domain, then render the final
visuals-overlaid video.

This is a thin orchestration layer over tutor_backend/visualizer/render_video.py
-- it doesn't reimplement transcription/classification/rendering, just
drives that module job-by-job and exposes progress for polling.

Jobs live in memory (JOBS dict below), same tradeoff api.py already makes
for quiz/code ATTEMPTS: fine for a single-process dev server, swap for a
real job queue + persistent store before running more than one worker.

Whisper + Ollama don't handle concurrent load well on a single dev
machine, so _PROCESSING_LOCK serializes actual analysis/render work
across jobs -- uploads still queue up and each gets its turn.
"""

import json
import pathlib
import threading
import traceback
import uuid

from . import config
from .visualizer import render_video as vc

UPLOAD_ROOT = pathlib.Path(config.UPLOADS_DIR) / "visualize"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

JOBS: dict[str, dict] = {}
_PROCESSING_LOCK = threading.Lock()


def _job_dir(job_id: str) -> pathlib.Path:
    d = UPLOAD_ROOT / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def start_job(file_bytes: bytes, filename: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    job_dir = _job_dir(job_id)
    suffix = pathlib.Path(filename).suffix or ".mp4"
    video_path = job_dir / f"source{suffix}"
    video_path.write_bytes(file_bytes)

    JOBS[job_id] = {
        "id": job_id,
        "filename": filename,
        "video_path": str(video_path),
        "status": "queued",  # queued -> transcribing -> analyzed -> rendering -> done | error
        "progress": {"chunks_done": 0, "current_time": 0.0},
        "chunks": [],        # list of dicts: start,end,visual_domain,scene_action,confidence,keywords,content,input_text
        "error": None,
        "output_video": None,
        "layout": None,
    }
    threading.Thread(target=_run_analysis, args=(job_id,), daemon=True).start()
    return job_id


def get_job(job_id: str):
    return JOBS.get(job_id)


# ---------------------------------------------------------------------------
# Analysis phase: transcribe + classify + extract, streamed into job["chunks"]
# as each chunk finishes so the frontend's mindmap can grow live.
# ---------------------------------------------------------------------------
def _run_analysis(job_id: str) -> None:
    job = JOBS[job_id]
    with _PROCESSING_LOCK:
        try:
            job["status"] = "transcribing"

            if not vc.check_dependencies():
                raise RuntimeError(
                    "ffmpeg or the Ollama model isn't available on the server -- check server logs for details."
                )

            job_dir = _job_dir(job_id)
            audio_path = str(job_dir / "audio.wav")
            vc.extract_audio(job["video_path"], audio_path)

            model = vc.WhisperModel(vc.WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")

            prev_domain = None
            for start, end, text in vc.transcribe_chunks(audio_path, model):
                text = text.strip()
                if not text:
                    continue

                result = vc.classify(text, prev_domain)
                result["input_text"] = text
                if result["visual_domain"] != "ignore":
                    result["content"] = vc.extract_content(text, result["visual_domain"])
                    prev_domain = result["visual_domain"]
                else:
                    result["content"] = {}

                job["chunks"].append({"start": start, "end": end, **result})
                job["progress"] = {"chunks_done": len(job["chunks"]), "current_time": end}

            if not job["chunks"]:
                job["status"] = "error"
                job["error"] = "No speech was detected in this video."
                return

            _save_job_cache(job_id)
            job["status"] = "analyzed"
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
            traceback.print_exc()


def _save_job_cache(job_id: str) -> None:
    """Writes the job's chunks through render_video's own cache format, in
    its own job directory, so render_video.render_video() picks them up
    (with any hand-edits already applied) instead of re-transcribing."""
    job = JOBS[job_id]
    chunks = [
        (c["start"], c["end"], {k: v for k, v in c.items() if k not in ("start", "end")})
        for c in job["chunks"]
    ]
    old_output_dir = vc.OUTPUT_DIR
    vc.OUTPUT_DIR = _job_dir(job_id)
    try:
        vc.save_cache(vc.cache_path_for(job["video_path"]), chunks)
    finally:
        vc.OUTPUT_DIR = old_output_dir


# ---------------------------------------------------------------------------
# Chunk edits -- the web equivalent of review_analysis.py's d/i commands.
# ---------------------------------------------------------------------------
def update_chunk(job_id: str, index: int, domain: str | None = None, ignore: bool = False) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise KeyError(job_id)
    if index < 0 or index >= len(job["chunks"]):
        raise IndexError(index)

    chunk = job["chunks"][index]
    if ignore:
        chunk["visual_domain"] = "ignore"
        chunk["scene_action"] = "IGNORE"
    elif domain:
        domain = domain.lower().strip()
        if domain not in vc.DOMAINS:
            raise ValueError(f"'{domain}' isn't a recognized domain. Choose one of: {', '.join(vc.DOMAINS)}")
        chunk["visual_domain"] = domain
        if domain == "ignore":
            chunk["scene_action"] = "IGNORE"

    _save_job_cache(job_id)
    return chunk


# ---------------------------------------------------------------------------
# Render phase: turns the (possibly hand-corrected) analysis into the final
# visuals-overlaid MP4. Reuses render_video.render_video(), which loads the
# cache we already wrote rather than re-transcribing or re-classifying.
# ---------------------------------------------------------------------------
def start_render(job_id: str, layout: str = "full") -> None:
    job = JOBS.get(job_id)
    if job is None:
        raise KeyError(job_id)
    if job["status"] not in ("analyzed", "done", "error"):
        raise RuntimeError(f"This job isn't ready to render yet (status={job['status']}).")

    job["status"] = "rendering"
    job["error"] = None
    threading.Thread(target=_run_render, args=(job_id, layout), daemon=True).start()


def _run_render(job_id: str, layout: str) -> None:
    job = JOBS[job_id]
    with _PROCESSING_LOCK:
        job_dir = _job_dir(job_id)
        old_output_dir = vc.OUTPUT_DIR
        vc.OUTPUT_DIR = job_dir
        try:
            vc.render_video(job["video_path"], force_reanalyze=False, layout=layout)

            video_name = pathlib.Path(job["video_path"]).stem
            output_path = job_dir / f"{video_name}_visualized.mp4"
            if not output_path.exists():
                # compositing may have failed and fallen back to the silent,
                # no-audio copy -- still worth showing rather than an error.
                fallback = job_dir / f"{video_name}_visualized_NO_AUDIO.mp4"
                output_path = fallback if fallback.exists() else None

            if output_path is None:
                job["status"] = "error"
                job["error"] = "Render finished but produced no output file -- check server logs."
                return

            job["output_video"] = str(output_path)
            job["layout"] = layout
            job["status"] = "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)
            traceback.print_exc()
        finally:
            vc.OUTPUT_DIR = old_output_dir


# ---------------------------------------------------------------------------
# Client-facing serialization -- keep server filesystem paths out of it.
# ---------------------------------------------------------------------------
def _public_chunk(c: dict, index: int) -> dict:
    return {
        "index": index,
        "start": c["start"],
        "end": c["end"],
        "visual_domain": c.get("visual_domain", "ignore"),
        "scene_action": c.get("scene_action", "IGNORE"),
        "confidence": c.get("confidence", 0.0),
        "keywords": c.get("keywords", []),
        "content": c.get("content", {}),
        "transcript": c.get("input_text", ""),
    }


def public_job(job: dict) -> dict:
    return {
        "id": job["id"],
        "filename": job["filename"],
        "status": job["status"],
        "error": job["error"],
        "progress": job["progress"],
        "chunks": [_public_chunk(c, i) for i, c in enumerate(job["chunks"])],
        "has_video": job["output_video"] is not None,
        "domains": vc.DOMAINS,
    }
