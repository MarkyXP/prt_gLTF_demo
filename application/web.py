"""FastAPI endpoints for the .prt to glTF web application.

Inputs:
  * GET requests for the viewer and its static assets.
  * Multipart .prt uploads via ``files`` or legacy ``file`` fields.

Outputs:
  * The static viewer.
  * JSON reference trees and conversion-job progress.
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
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import BASE_DIR
from .conversion import _collection_to_glb, convert_to_glb
from .reference_index import initialize_reference_index
from .references import reference_roots, reference_tree


LOGGER = logging.getLogger(__name__)
_conversion_jobs: dict[str, dict] = {}


def _build_reference_tree(name: str,
                          payloads: list[tuple[str, bytes]]) -> dict:
    """Build a reference tree without blocking the async request loop.

    > example
    > _build_reference_tree("car.prt", [("car.prt", part_bytes)])
      -> {"name": "car.prt", "found": bool, "children": [...]}
    """
    root_data = payloads[0][1]
    with tempfile.TemporaryDirectory() as temp_dir:
        for filename, payload in payloads:
            safe_name = os.path.basename(filename)
            if safe_name:
                Path(temp_dir, safe_name).write_bytes(payload)
        roots = reference_roots(root_data, temp_dir)
        root_path = Path(temp_dir) / os.path.basename(name)
        return reference_tree(name, root_data, roots, parent_path=root_path)


async def _run_conversion(job_id: str, data: bytes, part_name: str,
                          extra_parts: list[tuple[str, bytes]]):
    """Run one conversion job in a worker thread and record progress.

    > example
    > asyncio.create_task(_run_conversion(job_id, part_bytes, part_name, []))
      -> the job dict transitions queued -> running -> complete|failed
    """
    job = _conversion_jobs[job_id]

    def progress(event: str, part: str):
        with job["lock"]:
            if event == "started":
                job["current"] = part
            elif event == "completed":
                job["completed"] += 1
                job["completed_parts"].append(part)
            elif event == "missing":
                job["completed"] += 1
                job["missing_parts"].append(part)

    def artifact(path: str, part: str, collection: list[dict]):
        key = os.path.normcase(os.path.abspath(path))
        glb = _collection_to_glb(collection, part)
        artifact_id = uuid4().hex
        with job["lock"]:
            if key in job["artifact_keys"]:
                return
            job["artifact_keys"].add(key)
            job["artifacts"].append({
                "id": artifact_id,
                "part": part,
                "status": "available",
                "url": "/convert/%s/artifacts/%s" % (job_id, artifact_id),
                "glb": glb,
            })

    with job["lock"]:
        job["current"] = None
        job["status"] = "running"
    try:
        glb, stats = await asyncio.to_thread(
            convert_to_glb, data, part_name, extra_parts, progress, artifact)
        with job["lock"]:
            job.update(status="complete", current=None, glb=glb, stats=stats)
        LOGGER.info(
            "conversion job=%s file=%s status=complete",
            job_id, part_name)
    except Exception as error:
        with job["lock"]:
            job.update(status="failed", current=None, error=str(error))
        LOGGER.exception(
            "conversion job=%s file=%s status=failed", job_id, part_name)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Build the configured assembly index before serving requests.

    > example
    > async with lifespan(app):
      -> the reference index is loaded before any request is served
    """
    LOGGER.info("Application lifespan starting")
    initialize_reference_index(BASE_DIR)
    LOGGER.info("Application lifespan startup complete")
    yield


app = FastAPI(title="prt-gltf-demo", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/")
def index():
    """Serve the browser viewer.

    > example
    > GET /
      -> 200 with the viewer markup from static/index.html
    """
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/reindex")
def reindex():
    """Force a rebuild of the configured assembly reference index.

    > example
    > POST /reindex
      -> {"file_count": int, "unique_names": int}
    """
    index = initialize_reference_index(BASE_DIR, force_rebuild=True)
    return {
        "file_count": index.file_count,
        "unique_names": len(index.matches),
    }


@app.post("/references")
async def references(files: list[UploadFile] | None = File(None),
                     file: UploadFile | None = File(None)):
    """Return the component tree for all uploaded assembly files.

    > example
    > POST /references with a .prt upload
      -> {"name": ..., "found": bool, "children": [child nodes, ...]}
    """
    uploads = files or ([file] if file is not None else [])
    if not uploads:
        raise HTTPException(status_code=400, detail="no .prt files uploaded")
    root = uploads[0]
    name = root.filename or "part.prt"
    started = time.perf_counter()
    payloads = [(upload.filename or "", await upload.read())
                for upload in uploads]
    result = await asyncio.to_thread(_build_reference_tree, name, payloads)
    LOGGER.info(
        "request=/references file=%s uploads=%d elapsed=%.3fs",
        name, len(payloads), time.perf_counter() - started)
    return result


@app.post("/convert")
async def convert(files: list[UploadFile] | None = File(None),
                  file: UploadFile | None = File(None)):
    """Start an asynchronous .prt conversion job.

    > example
    > POST /convert with a .prt upload
      -> {"job_id": "..."} for the status/result endpoints
    """
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
        "artifacts": [],
        "artifact_keys": set(),
        "lock": Lock(),
    }
    LOGGER.info(
        "request=/convert file=%s job=%s uploads=%d",
        root.filename or "part.prt", job_id, len(uploads))
    asyncio.create_task(_run_conversion(
        job_id, data, part_name, extra_parts))
    return {"job_id": job_id}


@app.get("/convert/{job_id}/status")
async def conversion_status(job_id: str):
    """Return progress for an asynchronous conversion job.

    > example
    > GET /convert/<job_id>/status
      -> {"status": "running", "completed": int, "current": name, ...}
    """
    job = _conversion_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="conversion job not found")
    with job["lock"]:
        state = {
            key: value for key, value in job.items()
            if key not in {"glb", "error", "artifact_keys", "artifacts", "lock"}
        }
        state["artifacts"] = [
            {key: value for key, value in item.items() if key != "glb"}
            for item in job["artifacts"]
        ]
        if "error" in job:
            state["error"] = job["error"]
    return state


@app.get("/convert/{job_id}/artifacts/{artifact_id}")
async def conversion_artifact(job_id: str, artifact_id: str):
    """Return one completed per-part GLB artifact.

    > example
    > GET /convert/<job_id>/artifacts/<artifact_id>
      -> the completed ``model/gltf-binary`` body
    """
    job = _conversion_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="conversion job not found")
    with job["lock"]:
        artifact = next(
            (item for item in job["artifacts"] if item["id"] == artifact_id),
            None)
    if artifact is None:
        raise HTTPException(status_code=404, detail="conversion artifact not found")
    return Response(
        content=artifact["glb"],
        media_type="model/gltf-binary",
        headers={"Content-Disposition": 'inline; filename="%s.glb"' % artifact_id},
    )


@app.get("/convert/{job_id}/result")
async def conversion_result(job_id: str):
    """Return the completed GLB for a conversion job.

    > example
    > GET /convert/<job_id>/result
      -> the ``model/gltf-binary`` body with X-Conversion-Stats attached,
      404/400/409 for unknown/failed/incomplete jobs
    """
    job = _conversion_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="conversion job not found")
    with job["lock"]:
        status = job["status"]
        error = job.get("error")
        glb = job.get("glb")
        stats = job.get("stats")
    if status == "failed":
        raise HTTPException(status_code=400, detail=error)
    if status != "complete":
        raise HTTPException(status_code=409, detail="conversion is not complete")
    return Response(
        content=glb,
        media_type="model/gltf-binary",
        headers={
            "Content-Disposition": 'attachment; filename="%s.glb"'
            % job_id,
            "X-Conversion-Stats": json.dumps(stats),
        },
    )
