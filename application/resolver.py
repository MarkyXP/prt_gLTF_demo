"""Recursive part conversion and assembly proxy policy.

Inputs:
  * A root .prt path.
  * Optional resolution state and logical source filename.

Outputs:
  * A normalized mesh collection with assembly instances and proxy roles.
  * Resolution reports for web and CLI diagnostics.
"""
from __future__ import annotations

from pathlib import Path

import parasolid
import sub_assembly_resolver

from .geometry import (
    is_proxy_collection,
    mark_proxy,
    mesh_collection,
)
from .placement import placement_bundle, placement_families
from .references import find_named_part, meaningful_part_references, reference_roots


def convert_part_path(path: str | Path, seen: set[str] | None = None,
                      resolution: dict | None = None,
                      source_name: str | None = None) -> list[dict]:
    """Prefer Parasolid solids, then resolve references and embedded JT meshes."""
    path = Path(path).resolve()
    resolution = {"assemblies": []} if resolution is None else resolution
    seen = set() if seen is None else seen
    stash = resolution.setdefault("stash", {})
    key = str(path).lower()
    if key in stash:
        return stash[key]
    if key in seen:
        return []
    seen.add(key)

    data = path.read_bytes()
    logical_name = source_name or path.name
    roots = reference_roots(data, path.parent)
    ref_names = meaningful_part_references(data, logical_name)
    primary = None
    parasolid_collection = []
    try:
        parasolid_collection = parasolid.convert(str(path))
        if (parasolid_collection
                and not is_proxy_collection(parasolid_collection)
                and not ref_names):
            stash[key] = parasolid_collection
            return parasolid_collection
    except RuntimeError as error:
        primary = error

    jt_collection = mesh_collection(data)
    if (jt_collection and not is_proxy_collection(jt_collection)
            and not ref_names and not parasolid_collection):
        stash[key] = jt_collection
        return jt_collection

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
            return collection
        if primary is not None:
            raise primary
        return []

    referenced = []
    resolved_names = []
    missing_names = []
    nested_missing = []
    families = placement_families(data)
    resolved_meshes = {}
    resolved_paths = {}
    for ref_name in ref_names:
        reference = find_named_part(ref_name, roots, parent_path=path)
        if reference is None:
            missing_names.append(ref_name)
            continue
        report_start = len(resolution["assemblies"])
        try:
            child = convert_part_path(reference, seen, resolution)
        except RuntimeError:
            child = []
        child_reports = resolution["assemblies"][report_start:]
        if not child:
            missing_names.append(ref_name)
            continue

        stem = sub_assembly_resolver._reference_basename(ref_name)
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

    if families and resolved_meshes:
        bundle, pair_notes = placement_bundle(
            list(resolved_meshes), resolved_meshes, resolved_paths, families)
    else:
        bundle, pair_notes = None, []
    if bundle:
        resolution["assemblies"].append({
            "part": logical_name,
            "references": ref_names,
            "resolved": resolved_names,
            "missing": missing_names,
            "nested_missing": nested_missing,
            "proxy_display": "placed-instances",
            "pairing": pair_notes,
        })
        return bundle

    incomplete = bool(missing_names or nested_missing)
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
                "proxy_display": proxy_display,
            })
            return referenced
        mark_proxy(collection, "placeholder")
        resolution["assemblies"].append({
            "part": logical_name,
            "references": ref_names,
            "resolved": [],
            "missing": missing_names or ref_names,
            "nested_missing": nested_missing,
            "proxy_display": "placeholder",
        })
        return collection

    resolution["assemblies"].append({
        "part": logical_name,
        "references": ref_names,
        "resolved": resolved_names,
        "missing": missing_names,
        "nested_missing": nested_missing,
        "proxy_display": "no-proxy",
        "pairing": pair_notes,
    })
    if referenced:
        return referenced
    if primary is not None:
        raise RuntimeError("%s; referenced local parts had no usable "
                           "geometry: %s"
                           % (primary, ", ".join(missing_names or ref_names)))
    return collection
