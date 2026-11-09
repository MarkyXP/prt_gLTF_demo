"""Conversion services for CLI and HTTP callers.

Inputs:
  * Root .prt bytes or a local .prt path.
  * Optional uploaded child parts.

Outputs:
  * Self-contained glTF binary bytes or files.
  * Mesh, point, triangle, assembly-resolution, and skip statistics.
"""
from __future__ import annotations

import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from . import config as _config  # noqa: F401  (loads the bundled library path)

import prt_jt

from color_index import body_translucency, part_colour_index

from .config import conversion_workers
from .geometry import mesh_collection
from .references import copy_referenced_parts
from .resolver import convert_part_path


LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[str, str], None]


def convert_to_glb(data: bytes, part_name: str | None = None,
                   extra_parts: list[tuple[str, bytes]] | None = None,
                   progress: ProgressCallback | None = None):
    """Convert .prt bytes to ``(glb_bytes, stats)``.

    > example
    > convert_to_glb(part_bytes, "cap.prt", progress=callback)
      -> (glb_bytes, {"points": int, "triangles": int, "meshes": int,
         "skipped_hidden": int, "dropped_translucent": int, ...})
    """
    started = time.perf_counter()
    timings = {}
    with tempfile.TemporaryDirectory() as temp_dir:
        step_started = time.perf_counter()
        source = os.path.join(temp_dir, "part.prt")
        Path(source).write_bytes(data)
        for name, part_data in extra_parts or ():
            safe_name = os.path.basename(name)
            if safe_name and safe_name.lower() != "part.prt":
                Path(temp_dir, safe_name).write_bytes(part_data)
        source_name = Path(part_name or "part").name
        if Path(source_name).suffix.lower() != ".prt":
            source_name += ".prt"
        copy_referenced_parts(data, temp_dir, source_name)
        try:
            colour_index = part_colour_index(source)
        except Exception:
            colour_index = None
        if colour_index is not None:
            LOGGER.info(
                "conversion part=%s colour_index=%s(palette/None)",
                source_name, colour_index)
        timings["prepare_ms"] = round(
            (time.perf_counter() - step_started) * 1000, 1)
        LOGGER.info(
            "conversion part=%s step=prepare elapsed=%.3fs total=%.3fs",
            source_name, timings["prepare_ms"] / 1000,
            (time.perf_counter() - started))

        resolution = {"assemblies": []}
        step_started = time.perf_counter()
        workers = conversion_workers()
        LOGGER.info(
            "conversion part=%s parallel_workers=%d",
            source_name, workers)
        collection = convert_part_path(
            source, resolution=resolution, source_name=source_name,
            progress=progress, parallel_workers=workers)
        timings["decode_ms"] = round(
            (time.perf_counter() - step_started) * 1000, 1)
        LOGGER.info(
            "conversion part=%s step=decode elapsed=%.3fs total=%.3fs",
            source_name, timings["decode_ms"] / 1000,
            (time.perf_counter() - started))

    triangles = sum(len(mesh["t"]["tris"]) for mesh in collection)
    points = sum(len(mesh["t"]["pts"]) for mesh in collection)
    step_started = time.perf_counter()
    with tempfile.TemporaryDirectory() as temp_dir:
        stem = os.path.join(temp_dir, "part")
        prt_jt.write_gltf(collection, stem, glb=True, part_name=part_name)
        glb = Path(stem + ".glb").read_bytes()
    timings["serialize_ms"] = round(
        (time.perf_counter() - step_started) * 1000, 1)
    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    LOGGER.info(
        "conversion part=%s step=serialize elapsed=%.3fs total=%.3fs",
        part_name or "part", timings["serialize_ms"] / 1000,
        timings["total_ms"] / 1000)

    summaries = [{
        "part": item["part"],
        "reference_count": len(item["references"]),
        "resolved_count": len(item["resolved"]),
        "missing_count": len(item["missing"]),
        "nested_missing_count": len(item["nested_missing"]),
        "proxy_display": item["proxy_display"],
    } for item in resolution["assemblies"]]
    return glb, {
        "points": points,
        "triangles": triangles,
        "meshes": len(collection),
        "skipped_hidden": resolution.get("skipped_hidden", 0),
        "dropped_translucent": resolution.get("dropped_translucent", 0),
        "colour_index": colour_index,
        "assemblies": summaries,
        "timings_ms": timings,
    }


def convert_to_files(source: str | Path, output: str | Path):
    """Convert a local .prt path to GLB or glTF/bin files.

    Prints per-assembly resolution lines and colour notes against real
    part data when run directly.

    > example
    > convert_to_files("C:/parts/car.prt", "C:/out/car.glb")
      -> ([written paths], points, triangles, mesh count)
    """
    resolution = {"assemblies": []}
    workers = conversion_workers()
    LOGGER.info(
        "conversion part=%s parallel_workers=%d",
        Path(source).name, workers)
    collection = convert_part_path(
        source, resolution=resolution, parallel_workers=workers)
    if not collection:
        raise ValueError("no decodable mesh in this .prt file")
    try:
        colour_index = part_colour_index(str(source))
    except Exception:
        colour_index = None
    if colour_index is not None:
        print("part=%s colour_index=%d translucency=%s" % (
            Path(source).name, colour_index, body_translucency(str(source))))
    triangles = sum(len(mesh["t"]["tris"]) for mesh in collection)
    points = sum(len(mesh["t"]["pts"]) for mesh in collection)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    part_name = Path(source).stem
    if output.suffix.lower() == ".glb":
        with tempfile.TemporaryDirectory() as temp_dir:
            stem = os.path.join(temp_dir, "part")
            prt_jt.write_gltf(collection, stem, glb=True, part_name=part_name)
            output.write_bytes(Path(stem + ".glb").read_bytes())
        written = [output]
    else:
        stem = str(output.with_suffix(""))
        prt_jt.write_gltf(collection, stem, glb=False, part_name=part_name)
        written = [Path(stem + ".gltf"), Path(stem + ".bin")]
    for assembly in resolution["assemblies"]:
        print("assembly=%s proxy=%s resolved=%d/%d missing=%s" % (
            assembly["part"], assembly["proxy_display"],
            len(assembly["resolved"]), len(assembly["references"]),
            ",".join(assembly["missing"] + assembly["nested_missing"])))
        for note in assembly.get("pairing") or []:
            print("  note: %s" % note)
    return written, points, triangles, len(collection)


if __name__ == "__main__":
    import sys

    from .cli import main

    sys.exit(main(sys.argv))
