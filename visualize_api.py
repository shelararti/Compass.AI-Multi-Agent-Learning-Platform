"""
API routes for the "Visualize" page (upload a lecture video, get a
content-aware visuals-overlaid render back). Kept separate from api.py's
tutor-graph routes since this is a fully independent feature -- it doesn't
touch students, subjects, or the agent graph at all.
"""

from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import visualize_service as vs

router = APIRouter(prefix="/api/visualize", tags=["visualize"])

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")


class ChunkUpdateRequest(BaseModel):
    domain: Optional[str] = None
    ignore: bool = False


class RenderRequest(BaseModel):
    layout: str = "full"


@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    filename = file.filename or "lecture.mp4"
    if not filename.lower().endswith(VIDEO_EXTENSIONS):
        raise HTTPException(400, f"Please upload a video file ({', '.join(VIDEO_EXTENSIONS)}).")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Uploaded file is empty.")

    job_id = vs.start_job(file_bytes, filename)
    return {"job_id": job_id}


@router.get("/status/{job_id}")
def status(job_id: str):
    job = vs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job_id.")
    return vs.public_job(job)


@router.post("/chunk/{job_id}/{index}")
def update_chunk(job_id: str, index: int, req: ChunkUpdateRequest):
    try:
        chunk = vs.update_chunk(job_id, index, domain=req.domain, ignore=req.ignore)
    except KeyError:
        raise HTTPException(404, "Unknown job_id.")
    except IndexError:
        raise HTTPException(404, "Unknown chunk index.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return vs._public_chunk(chunk, index)


@router.post("/render/{job_id}")
def render(job_id: str, req: RenderRequest):
    if req.layout not in ("full", "pip", "splitscreen"):
        raise HTTPException(400, "layout must be one of: full, pip, splitscreen")
    try:
        vs.start_render(job_id, layout=req.layout)
    except KeyError:
        raise HTTPException(404, "Unknown job_id.")
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"status": "rendering"}


@router.get("/source/{job_id}")
def source_video(job_id: str):
    job = vs.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job_id.")
    return FileResponse(job["video_path"])


@router.get("/video/{job_id}")
def rendered_video(job_id: str):
    job = vs.get_job(job_id)
    if job is None or not job["output_video"]:
        raise HTTPException(404, "No rendered video for this job yet.")
    return FileResponse(job["output_video"], filename=f"{job_id}_visualized.mp4")
