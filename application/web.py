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

import json
import logging
import os
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from .config import BASE_DIR
from .conversion import convert_to_glb
from .references import reference_roots, reference_tree
from sub_assembly_resolver import initialize_reference_index


LOGGER = logging.getLogger(__name__)


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
        return reference_tree(name, root_data, roots, parent_path=root_path)


@app.post("/convert")
async def convert(files: list[UploadFile] | None = File(None),
                  file: UploadFile | None = File(None)):
    """Convert uploaded .prt files and return a self-contained GLB."""
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
    try:
        glb, stats = convert_to_glb(
            data, part_name=part_name, extra_parts=extra_parts)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return Response(
        content=glb,
        media_type="model/gltf-binary",
        headers={
            "Content-Disposition": 'attachment; filename="%s.glb"'
            % part_name.replace('"', ""),
            "X-Conversion-Stats": json.dumps(stats),
        },
    )
