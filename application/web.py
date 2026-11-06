"""FastAPI endpoints for the .prt to glTF web application.

Inputs:
  * GET requests for the viewer.
  * Multipart .prt uploads via ``files`` or legacy ``file`` fields.

Outputs:
  * The static viewer.
  * JSON reference trees.
  * Self-contained ``model/gltf-binary`` responses.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .config import BASE_DIR
from .conversion import convert_to_glb
from .references import reference_roots, reference_tree
from sub_assembly_resolver import initialize_reference_index


LOGGER = logging.getLogger(__name__)
_conversion_jobs: dict[str, dict] = {}


async def _run_conversion(job_id: str, data: bytes, part_name: str,
                          extra_parts: list[tuple[str, bytes]]):
    job = _conversion_jobs[job_id]

    def progress(event: str, part: str):
        if event == "started":
            job["current"] = part
        elif event == "completed":
            job["completed"] += 1
            job["completed_parts"].append(part)
        elif event == "missing":
            job["completed"] += 1
            job["missing_parts"].append(part)

    job["status"] = "running"
    try:
        glb, stats = await asyncio.to_thread(
            convert_to_glb, data, part_name, extra_parts, progress)
        job.update(status="complete", current=None, glb=glb, stats=stats)
        LOGGER.info(
            "conversion job=%s file=%s status=complete",
            job_id, part_name)
    except Exception as error:
        job.update(status="failed", current=None, error=str(error))
        LOGGER.exception(
            "conversion job=%s file=%s status=failed", job_id, part_name)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Build the configured assembly index before serving requests."""
    LOGGER.info("Application lifespan starting")
    initialize_reference_index(BASE_DIR)
    LOGGER.info("Application lifespan startup complete")
    yield


app = FastAPI(title="prt-gltf-demo", version="1.0.0", lifespan=lifespan)


@app.get("/")
def index():
    """Serve the browser viewer."""
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/reindex")
def reindex():
    """Force a rebuild of the configured assembly reference index."""
    index = initialize_reference_index(BASE_DIR, force_rebuild=True)
    return {
        "file_count": index.file_count,
        "unique_names": len(index.matches),
    }


@app.post("/references")
async def references(files: list[UploadFile] | None = File(None),
                     file: UploadFile | None = File(None)):
    """Return the component tree for all uploaded assembly files."""
    uploads = files or ([file] if file is not None else [])
    if not uploads:
        raise HTTPException(status_code=400, detail="no .prt files uploaded")
    root = uploads[0]
    name = root.filename or "part.prt"
    started = time.perf_counter()
    payloads = [(upload.filename or "", await upload.read())
                for upload in uploads]
    root_data = payloads[0][1]
    with tempfile.TemporaryDirectory() as temp_dir:
        for filename, payload in payloads:
            safe_name = os.path.basename(filename)
            if safe_name:
                Path(temp_dir, safe_name).write_bytes(payload)
        roots = reference_roots(root_data, temp_dir)
        root_path = Path(temp_dir) / os.path.basename(name)
        result = reference_tree(name, root_data, roots, parent_path=root_path)
    LOGGER.info(
        "request=/references file=%s uploads=%d elapsed=%.3fs",
        name, len(payloads), time.perf_counter() - started)
    return result


@app.post("/convert")
async def convert(files: list[UploadFile] | None = File(None),
                  file: UploadFile | None = File(None)):
    """Start an asynchronous .prt conversion job."""
    uploads = files or ([file] if file is not None else [])
    if not uploads:
        raise HTTPException(status_code=400, detail="no .prt files uploaded")
    root = uploads[0]
    data = await root.read()
    extra_parts = [
        (upload.filename or "", await upload.read())
        for upload in uploads[1:]
    ]
    part_name = Path(root.filename or "part.prt").stem
    job_id = uuid4().hex
    _conversion_jobs[job_id] = {
        "status": "queued",
        "current": None,
        "completed": 0,
        "completed_parts": [],
        "missing_parts": [],
    }
    LOGGER.info(
        "request=/convert file=%s job=%s uploads=%d",
        root.filename or "part.prt", job_id, len(uploads))
    asyncio.create_task(_run_conversion(
        job_id, data, part_name, extra_parts))
    return {"job_id": job_id}


@app.get("/convert/{job_id}/status")
async def conversion_status(job_id: str):
    """Return progress for an asynchronous conversion job."""
    job = _conversion_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="conversion job not found")
    return {
        key: value for key, value in job.items()
        if key not in {"glb", "error"} or key == "error"
    }


@app.get("/convert/{job_id}/result")
async def conversion_result(job_id: str):
    """Return the completed GLB for a conversion job."""
    job = _conversion_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="conversion job not found")
    if job["status"] == "failed":
        raise HTTPException(status_code=400, detail=job["error"])
    if job["status"] != "complete":
        raise HTTPException(status_code=409, detail="conversion is not complete")
    stats = job["stats"]
    return Response(
        content=job["glb"],
        media_type="model/gltf-binary",
        headers={
            "Content-Disposition": 'attachment; filename="%s.glb"'
            % job_id,
            "X-Conversion-Stats": json.dumps(stats),
        },
    )
