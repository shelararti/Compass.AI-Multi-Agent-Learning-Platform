"""
API routes for the "Resources" page -- students upload or link study
material (PDFs, slide decks, videos, YouTube links, ...) and other
students rate + classify it. Independent of the tutor graph, same as
visualize_api.py.
"""

import pathlib
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config
from . import knowledge_base
from . import resource_store as rs
from . import skills as skills_taxonomy

router = APIRouter(prefix="/api/resources", tags=["resources"])


class LinkRequest(BaseModel):
    student_id: str
    title: str
    description: str = ""
    subject_id: Optional[str] = None
    format: str
    url: str
    skills: list[str] = []


class RatingRequest(BaseModel):
    student_id: str
    stars: int
    comment: str = ""
    axes: dict[str, int] = {}


class ClassifyRequest(BaseModel):
    student_id: str
    level: str
    tags: list[str] = []


def _validate_subject(subject_id: Optional[str]) -> None:
    if subject_id and not knowledge_base.get_subject(subject_id):
        raise HTTPException(404, f"Unknown subject_id: {subject_id}")


def _public_resource(resource: dict, detail: bool = False) -> dict:
    out = {
        "id": resource["id"],
        "title": resource["title"],
        "description": resource["description"],
        "subject_id": resource["subject_id"],
        "format": resource["format"],
        "uploaded_by": resource["uploaded_by"],
        "uploaded_at": resource["uploaded_at"],
        "url": resource["url"],
        "has_file": resource["file_path"] is not None,
        "skills": resource.get("skills", []),
        **rs.summarize(resource),
    }
    if detail:
        out["ratings"] = resource["ratings"]
        out["classifications"] = resource["classifications"]
    return out


@router.get("/meta")
def meta():
    """Vocab the frontend needs to build the upload/rate/classify forms
    without hardcoding it twice."""
    return {"formats": rs.FORMATS, "tags": rs.TAGS, "levels": config.LEVELS, "axes": rs.AXES}


@router.get("")
def list_resources(
    subject_id: Optional[str] = None, format: Optional[str] = None, sort: str = "recent",
    q: Optional[str] = None,
):
    _validate_subject(subject_id)
    if format and format not in rs.FORMATS:
        raise HTTPException(400, f"format must be one of: {', '.join(rs.FORMATS)}")

    resources = rs.list_resources()
    if subject_id:
        resources = [r for r in resources if r["subject_id"] == subject_id]
    if format:
        resources = [r for r in resources if r["format"] == format]

    public = [_public_resource(r) for r in resources]

    if q:
        needle = q.strip().lower()
        def matches(r):
            haystack = " ".join([
                r["title"], r["description"] or "", r["uploaded_by"],
                " ".join(r["tag_counts"].keys()),
            ]).lower()
            return needle in haystack
        public = [r for r in public if matches(r)]

    if sort == "rating":
        public.sort(key=lambda r: (r["average_rating"] is None, -(r["average_rating"] or 0)))
    else:
        public.sort(key=lambda r: r["uploaded_at"], reverse=True)
    return public


@router.get("/{resource_id}")
def get_resource(resource_id: str):
    resource = rs.get_resource(resource_id)
    if resource is None:
        raise HTTPException(404, "Unknown resource_id.")
    return _public_resource(resource, detail=True)


@router.post("/upload")
async def upload_resource(
    file: UploadFile = File(...),
    student_id: str = Form(...),
    title: str = Form(...),
    description: str = Form(""),
    subject_id: Optional[str] = Form(None),
    format: str = Form(...),
    skills: str = Form(""),  # comma-separated skill ids, e.g. "python,data"
):
    _validate_subject(subject_id)
    if format not in rs.UPLOAD_FORMATS:
        raise HTTPException(
            400, f"For an uploaded file, format must be one of: {', '.join(sorted(rs.UPLOAD_FORMATS))} "
                 f"(use /api/resources/link for youtube/link)."
        )

    filename = file.filename or "resource"
    allowed_ext = rs.FORMAT_EXTENSIONS.get(format)
    if allowed_ext and not filename.lower().endswith(allowed_ext):
        raise HTTPException(400, f"'{format}' expects a file ending in: {', '.join(allowed_ext)}")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Uploaded file is empty.")

    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    resource = rs.create_resource(
        title=title.strip() or filename, description=description, subject_id=subject_id,
        fmt=format, student_id=student_id, skills=skill_list,
    )
    dest = pathlib.Path(rs._file_dir(resource["id"])) / filename
    dest.write_bytes(file_bytes)
    resource["file_path"] = str(dest)
    rs.save_resource(resource)
    return _public_resource(resource)


@router.post("/link")
def add_link_resource(req: LinkRequest):
    _validate_subject(req.subject_id)
    if req.format not in rs.LINK_FORMATS:
        raise HTTPException(
            400, f"For a link, format must be one of: {', '.join(sorted(rs.LINK_FORMATS))} "
                 f"(use /api/resources/upload for a file)."
        )
    if not (req.url.startswith("http://") or req.url.startswith("https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    if req.format == "youtube" and "youtube.com" not in req.url and "youtu.be" not in req.url:
        raise HTTPException(400, "That doesn't look like a YouTube URL.")

    resource = rs.create_resource(
        title=req.title.strip() or req.url, description=req.description, subject_id=req.subject_id,
        fmt=req.format, student_id=req.student_id, url=req.url, skills=req.skills,
    )
    return _public_resource(resource)


@router.get("/{resource_id}/file")
def resource_file(resource_id: str):
    resource = rs.get_resource(resource_id)
    if resource is None or not resource["file_path"]:
        raise HTTPException(404, "No file for this resource (it may be a link).")
    return FileResponse(resource["file_path"])


@router.post("/{resource_id}/rate")
def rate_resource(resource_id: str, req: RatingRequest):
    try:
        resource = rs.add_rating(resource_id, req.student_id, req.stars, req.comment, req.axes)
    except KeyError:
        raise HTTPException(404, "Unknown resource_id.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _public_resource(resource, detail=True)


@router.post("/{resource_id}/classify")
def classify_resource(resource_id: str, req: ClassifyRequest):
    try:
        resource = rs.add_classification(resource_id, req.student_id, req.level, req.tags)
    except KeyError:
        raise HTTPException(404, "Unknown resource_id.")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _public_resource(resource, detail=True)
