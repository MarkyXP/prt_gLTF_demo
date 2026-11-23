"""Assembly reference filtering, lookup, and tree generation.

Inputs:
  * UG_PART metadata bytes.
  * A project directory and optional configured search roots.
  * Uploaded or local .prt paths.

Outputs:
  * Exact referenced basenames.
  * Resolved child paths.
  * The JSON-compatible reference tree shown by the web UI.
  * Reference copies staged beside uploaded root parts.

Suppressed child components (see ``suppression.py``) are neither resolved
nor copied for uploads: they would not render, so loading them only costs
time.
"""
from __future__ import annotations

import os
from pathlib import Path

import extref_references
import parasolid
import prt_jt
from .config import BASE_DIR
from .reference_index import find_sub_assembly, reference_basename
from .suppression import (
    early_hidden_child_names,
    external_reference_names,
    is_suppressed_child,
    suppressed_child_names,
)

GENERIC_PART_REFERENCES = {
    "model_template_nx10.prt",
    "xxx_000x_100_model1.prt",
    "xxx_000x_100_a_model1.prt",
}


def exact_reference_summary(data: bytes | None) -> tuple[bool, list[str], list[str]]:
    """Exact EXTREF child names and slot-two directories, when valid.

    The first item is ``True`` when at least one EXTREFSTREAM parsed
    completely. Regex metadata scanning is only for parts without one.

    > example
    > exact_reference_summary(part_bytes)
      -> (True, ["wheel.prt", "cap.prt"], ["C:/assemblies"])
    """
    if not data:
        return False, [], []
    try:
        streams = prt_jt.open_prt_bytes(data)
    except Exception:
        return False, [], []
    if not extref_references.has_valid_stream(streams):
        return False, [], []
    return (True, extref_references.reference_names(streams),
            extref_references.search_roots(streams))


def _meaningful_names(names: list[str], own: str | None) -> list[str]:
    """Filter self references and NX boilerplate from a name list."""
    return [
        name for name in names
        if name.lower() != own and name.lower() not in GENERIC_PART_REFERENCES
    ]


def meaningful_part_references(data: bytes,
                               own_name: str | os.PathLike | None = None) -> list[str]:
    """Return real component references, excluding NX boilerplate and self.

    A valid EXTREFSTREAM is authoritative even when it names only the part
    itself: those files have no component children, so unrelated free-text
    ``.prt`` tokens in their history metadata must not be promoted to
    children. The framed-regex scan is only for parts without a valid exact
    table.

    > example
    > meaningful_part_references(part_bytes, "cap.prt")
      -> ["bracket.prt", "wheel.prt"], minus template references and self
    """
    own = Path(own_name).name.lower() if own_name else None
    valid, exact_names, _roots = exact_reference_summary(data)
    if valid:
        return _meaningful_names(exact_names, own)
    return _meaningful_names(parasolid.referenced_part_names(data), own)


def find_named_part(name: str, roots: list[str | os.PathLike],
                    parent_path: str | os.PathLike | None = None) -> Path | None:
    """Resolve one exact component filename, preferring its parent folder.

    > example
    > find_named_part("wheel.prt", ["C:/data"], parent_path="C:/a/car.prt")
      -> Path("C:/a/wheel.prt"), or None when unresolved
    """
    return find_sub_assembly(
        name,
        parent_assembly=parent_path,
        additional_roots=roots,
        project_dir=BASE_DIR,
    )


def reference_roots(data: bytes | None = None,
                    containing_dir: str | os.PathLike | None = None) -> list[str]:
    """Build metadata, configured, and project reference roots.

    > example
    > reference_roots(part_bytes, "C:/assemblies")
      -> ["C:/assemblies", ...configured, metadata-derived, project roots...]
    """
    roots = []
    if containing_dir:
        roots.append(str(containing_dir))
    configured = os.environ.get("PRT_REFERENCE_ROOTS", "")
    roots += [path for path in configured.split(os.pathsep) if path]
    roots += exact_reference_summary(data)[2]
    roots += parasolid.referenced_search_roots(data)
    roots.append(str(BASE_DIR))
    return list(dict.fromkeys(roots))


def reference_tree(name: str, data: bytes, roots: list[str],
                   seen: set[str] | None = None,
                   parent_path: str | os.PathLike | None = None) -> dict:
    """Build the recursive component tree consumed by the sidebar.

    > example
    > reference_tree("car.prt", part_bytes, ["C:/data"])
      -> {"name": ..., "found": bool, "children": [child nodes, ...]}
    """
    seen = set() if seen is None else seen
    key = name.lower()
    node = {"name": name, "found": False, "children": []}
    if key in seen:
        return node
    seen.add(key)
    external_names = external_reference_names(data)
    for child_name in meaningful_part_references(data, name):
        if reference_basename(child_name).casefold() in external_names:
            continue
        child = {"name": child_name, "found": False, "children": []}
        match = find_named_part(child_name, roots, parent_path=parent_path)
        if match:
            child["found"] = True
            try:
                child_data = Path(match).read_bytes()
                nested = reference_tree(
                    child_name, child_data, roots, seen, parent_path=match)
                child["children"] = nested["children"]
            except OSError:
                pass
        node["children"].append(child)
    return node


def copy_referenced_parts(data: bytes, target_dir: str | os.PathLike,
                          source_name: str,
                          source_dir: str | os.PathLike | None = None
                          ) -> list[str]:
    """Copy locally resolvable references beside an uploaded root file.

    Suppressed children are skipped before their file is read: the parent
    flags them invisible, so no conversion or reference copy happens for them.
    Every resolvable meaningful name is staged; a name that merely overlaps a
    legacy ``ExternalReferences`` scan is still a real component occurrence,
    and staging an extra file is harmless because resolution uses it only
    when the parent actually names it. ``source_dir`` keeps the CLI/render
    path resolving sibling children exactly as a direct conversion does.

    > example
    > copy_referenced_parts(part_bytes, "C:/staged", "car.prt")
      -> the locally resolved source paths copied into C:/staged
    """
    copied = []
    roots = reference_roots(data, source_dir)
    ref_names = meaningful_part_references(data, source_name)
    suppressed = (
        frozenset(suppressed_child_names(data))
        | early_hidden_child_names(data, ref_names)
    )
    for name in ref_names:
        if is_suppressed_child(name, suppressed):
            continue
        path = find_named_part(
            name, roots, parent_path=Path(target_dir) / "part.prt")
        if path is None:
            continue
        path = str(path)
        target = os.path.join(target_dir, os.path.basename(path))
        if os.path.abspath(path).lower() == os.path.abspath(target).lower():
            copied.append(path)
            continue
        if not os.path.exists(target):
            with open(path, "rb") as source, open(target, "wb") as target_file:
                target_file.write(source.read())
        copied.append(path)
    return copied
