"""Convert one .prt part tree into a normalized mesh collection.

Inputs:
  * A root .prt path (assembly or single part).
  * Optional resolution state, logical source filename, progress callback,
    and worker configuration.

Outputs:
  * A normalized mesh collection with assembly instances and proxy roles.
  * Resolution reports for web and CLI diagnostics.

Assembly files with direct embedded-JT geometry skip the Parasolid probe;
proxy-only assemblies defer it until after child resolution so a complete
assembly can avoid Parasolid entirely.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import parasolid

from .accounting import (
    ArtifactCallback,
    ConversionContext,
    ProgressCallback,
    count_skip,
    publish_skip_counts,
)
from .display import paint_body_display
from ..geometry import is_proxy_collection, mark_proxy, mesh_collection
from ..placement import placement_bundle, placement_families, placement_payload
from ..reference_index import reference_basename
from ..references import find_named_part, meaningful_part_references, reference_roots
from ..suppression import is_suppressed_child, suppressed_child_names


LOGGER = logging.getLogger(__name__)


def _report(progress: ProgressCallback | None, event: str, part: str):
    if progress is not None:
        progress(event, part)


def convert_part_path(path: str | Path, seen: set[str] | None = None,
                      resolution: dict | None = None,
                      source_name: str | None = None,
                      progress: ProgressCallback | None = None,
                      artifact: ArtifactCallback | None = None,
                      parallel_workers: int = 2,
                      parallel_depth: int = 0,
                      _context: ConversionContext | None = None) -> list[dict]:
    """Convert one part, sharing completed work across parallel branches.

    > example
    > convert_part_path("C:/assemblies/car.prt")
      -> a mesh dictionary collection for the whole part tree
    """
    owns_context = _context is None
    context = _context or ConversionContext()
    resolved_path = Path(path).resolve()
    key = str(resolved_path).lower()
    if key in context.cache:
        return context.cache[key]

    seen = set() if seen is None else seen
    if key in seen:
        return []

    with context.lock:
        if key in context.cache:
            return context.cache[key]
        future = context.inflight.get(key)
        owner = future is None
        if owner:
            future = Future()
            context.inflight[key] = future

    if not owner:
        return future.result()

    try:
        result = _convert_part_path(
            resolved_path, seen=seen, resolution=resolution,
            source_name=source_name, progress=progress,
            artifact=artifact,
            parallel_workers=parallel_workers,
            parallel_depth=parallel_depth, context=context)
    except BaseException as error:
        with context.lock:
            context.inflight.pop(key, None)
            future.set_exception(error)
        raise

    with context.lock:
        context.cache[key] = result
        context.reports[key] = list(
            (resolution or {}).get("assemblies", []))
        context.inflight.pop(key, None)
        future.set_result(result)
    if owns_context:
        publish_skip_counts(context, resolution)
    return result


def _convert_part_path(path: str | Path, seen: set[str],
                       resolution: dict | None,
                       source_name: str | None,
                       progress: ProgressCallback | None,
                       artifact: ArtifactCallback | None,
                       parallel_workers: int,
                       parallel_depth: int,
                       context: ConversionContext) -> list[dict]:
    """Resolve embedded JT geometry first, deferring Parasolid when possible.

    > example
    > _convert_part_path(Path("C:/parts/cap.prt"), set(), None, None, None, 2, 0, ConversionContext())
      -> the mesh collection for that single part file
    """
    path = Path(path)
    resolution = {"assemblies": []} if resolution is None else resolution
    stash = resolution.setdefault("stash", {})
    key = str(path).lower()
    if key in stash:
        return stash[key]
    if key in seen:
        return []
    seen.add(key)

    started = time.perf_counter()
    data = path.read_bytes()
    logical_name = source_name or path.name
    step_started = time.perf_counter()
    roots = reference_roots(data, path.parent)
    ref_names = meaningful_part_references(data, logical_name)
    _report(progress, "started", logical_name)
    suppressed = suppressed_child_names(data)

    def finish(result: list[dict], includes_children: bool = False) -> list[dict]:
        """Complete one part, streaming artifacts for its own geometry.

        Combined results that bundle already-streamed children skip the
        artifact so each part's geometry is uploaded to the viewer exactly
        once, as soon as it is ready.

        > example
        > finish(collection) -> collection; emits an artifact because the
        > result holds this part's own geometry, not resolved children
        """
        if artifact is not None and result and not includes_children:
            artifact(str(path), logical_name, result)
        _report(progress, "completed", logical_name)
        return result

    primary = None
    parasolid_collection = []

    def run_parasolid():
        nonlocal primary
        step_started = time.perf_counter()
        try:
            collection = parasolid.convert(str(path))
        except RuntimeError as error:
            primary = error
            collection = []
        LOGGER.info(
            "part=%s step=parasolid elapsed=%.3fs meshes=%d total=%.3fs",
            logical_name, time.perf_counter() - step_started,
            len(collection), time.perf_counter() - started)
        return collection

    step_started = time.perf_counter()
    jt_collection = mesh_collection(data)
    jt_ms = (time.perf_counter() - step_started) * 1000
    jt_collection, dropped_transparent = paint_body_display(
        str(path), jt_collection)
    if dropped_transparent:
        count_skip(context, "dropped_translucent", dropped_transparent)
        LOGGER.info(
            "part=%s step=jt result=dropped-translucent meshes=%d",
            logical_name, dropped_transparent)
    LOGGER.info(
        "part=%s step=jt elapsed=%.3fs meshes=%d total=%.3fs",
        logical_name, jt_ms / 1000, len(jt_collection),
        time.perf_counter() - started)

    direct_jt = bool(
        jt_collection and not is_proxy_collection(jt_collection))
    defer_parasolid = bool(ref_names and not direct_jt)
    if ref_names and direct_jt:
        LOGGER.info(
            "part=%s step=parasolid skipped reason=direct-jt",
            logical_name)
    elif not ref_names:
        parasolid_collection = run_parasolid()
        if (parasolid_collection
                and not is_proxy_collection(parasolid_collection)):
            stash[key] = parasolid_collection
            return finish(parasolid_collection)

    if (not ref_names and direct_jt and not parasolid_collection):
        stash[key] = jt_collection
        return finish(jt_collection)

    if parasolid_collection and not is_proxy_collection(parasolid_collection):
        collection = parasolid_collection
    elif jt_collection and not is_proxy_collection(jt_collection):
        collection = jt_collection
    else:
        collection = parasolid_collection or jt_collection
    if collection:
        stash[key] = collection
    is_proxy = is_proxy_collection(collection)
    if not ref_names:
        if collection:
            return finish(collection)
        if primary is not None:
            raise primary
        return finish([])

    referenced = []
    resolved_names = []
    missing_names = []
    nested_missing = []
    skipped_hidden = []
    families = placement_families(data)
    om_payload = placement_payload(data)
    resolved_meshes = {}
    resolved_paths = {}

    def resolve_child(ref_name: str, reference: Path):
        child_resolution = {"assemblies": []}
        try:
            child = convert_part_path(
                reference, seen=set(seen), resolution=child_resolution,
                progress=progress, artifact=artifact,
                parallel_workers=parallel_workers,
                parallel_depth=parallel_depth + 1, _context=context)
        except RuntimeError:
            child = []
        child_reports = context.reports.get(str(reference.resolve()).lower(), [])
        return child, child_resolution, child_reports

    candidates = []
    for ref_name in ref_names:
        child_started = time.perf_counter()
        if is_suppressed_child(ref_name, suppressed):
            skipped_hidden.append(ref_name)
            count_skip(context, "skipped_hidden")
            LOGGER.info(
                "part=%s step=child name=%s result=suppressed elapsed=%.3fs "
                "total=%.3fs",
                logical_name, ref_name,
                time.perf_counter() - child_started,
                time.perf_counter() - started)
            continue
        reference = find_named_part(ref_name, roots, parent_path=path)
        if reference is None:
            missing_names.append(ref_name)
            _report(progress, "missing", ref_name)
            LOGGER.info(
                "part=%s step=child name=%s result=missing elapsed=%.3fs "
                "total=%.3fs",
                logical_name, ref_name,
                time.perf_counter() - child_started,
                time.perf_counter() - started)
            continue
        candidates.append((ref_name, reference, child_started))

    # Only parallelize the top-level sibling set. Nested pools would
    # oversubscribe the machine, while the shared context prevents duplicate
    # expensive conversions when branches meet at the same part.
    if parallel_workers > 1 and parallel_depth == 0 and len(candidates) > 1:
        with ThreadPoolExecutor(max_workers=parallel_workers) as executor:
            results = list(executor.map(
                lambda candidate: resolve_child(candidate[0], candidate[1]),
                candidates))
    else:
        results = [
            resolve_child(ref_name, reference)
            for ref_name, reference, _child_started in candidates
        ]

    for (ref_name, reference, child_started), (
            child, child_resolution, child_reports) in zip(
            candidates, results):
        resolution["assemblies"].extend(
            child_resolution.get("assemblies", []))
        resolution["stash"].update(child_resolution.get("stash", {}))
        if not child:
            missing_names.append(ref_name)
            _report(progress, "missing", ref_name)
            LOGGER.info(
                "part=%s step=child name=%s result=empty elapsed=%.3fs "
                "total=%.3fs",
                logical_name, ref_name,
                time.perf_counter() - child_started,
                time.perf_counter() - started)
            continue

        stem = reference_basename(ref_name)
        resolved_meshes[stem] = child
        resolved_paths[stem] = reference
        resolved_names.append(ref_name)
        referenced.extend(child)
        for report in child_reports:
            unresolved = report.get("missing", []) + report.get("nested_missing", [])
            if unresolved or report.get("proxy_display") in {
                "placeholder", "translucent-bounds",
            }:
                nested_missing.append("%s -> %s" % (
                    ref_name, ", ".join(unresolved or [report["part"]])))
        LOGGER.info(
            "part=%s step=child name=%s result=decoded meshes=%d elapsed=%.3fs "
            "total=%.3fs",
            logical_name, ref_name, len(child),
            time.perf_counter() - child_started,
            time.perf_counter() - started)

    if families and resolved_meshes:
        bundle, pair_notes = placement_bundle(
            list(resolved_meshes), resolved_meshes, resolved_paths, families,
            payload=om_payload)
    else:
        bundle, pair_notes = None, []
    if bundle:
        resolution["assemblies"].append({
            "part": logical_name,
            "references": ref_names,
            "resolved": resolved_names,
            "missing": missing_names,
            "nested_missing": nested_missing,
            "skipped_hidden": skipped_hidden,
            "proxy_display": "placed-instances",
            "pairing": pair_notes,
        })
        return finish(bundle, includes_children=True)

    incomplete = bool(missing_names or nested_missing)
    if defer_parasolid and (incomplete or not referenced):
        parasolid_collection = run_parasolid()
        if parasolid_collection:
            collection = parasolid_collection
            is_proxy = is_proxy_collection(collection)

    if is_proxy:
        if referenced:
            proxy_display = "translucent-bounds" if incomplete else "suppressed"
            if incomplete:
                mark_proxy(collection, proxy_display)
                referenced.extend(collection)
            resolution["assemblies"].append({
                "part": logical_name,
                "references": ref_names,
                "resolved": resolved_names,
                "missing": missing_names,
                "nested_missing": nested_missing,
                "skipped_hidden": skipped_hidden,
                "proxy_display": proxy_display,
            })
            return finish(referenced, includes_children=True)
        mark_proxy(collection, "placeholder")
        resolution["assemblies"].append({
            "part": logical_name,
            "references": ref_names,
            "resolved": [],
            "missing": missing_names or ref_names,
            "nested_missing": nested_missing,
            "skipped_hidden": skipped_hidden,
            "proxy_display": "placeholder",
        })
        return finish(collection)

    resolution["assemblies"].append({
        "part": logical_name,
        "references": ref_names,
        "resolved": resolved_names,
        "missing": missing_names,
        "nested_missing": nested_missing,
        "skipped_hidden": skipped_hidden,
        "proxy_display": "no-proxy",
        "pairing": pair_notes,
    })
    if referenced:
        return finish(referenced, includes_children=True)
    if primary is not None:
        raise RuntimeError("%s; referenced local parts had no usable "
                           "geometry: %s"
                           % (primary, ", ".join(missing_names or ref_names)))
    return finish(collection)
