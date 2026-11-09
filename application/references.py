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

import parasolid

from .config import BASE_DIR
from .reference_index import find_sub_assembly
from .suppression import is_suppressed_child, suppressed_child_names


GENERIC_PART_REFERENCES = {
    "model_template_nx10.prt",
    "xxx_000x_100_model1.prt",
    "xxx_000x_100_a_model1.prt",
}


def meaningful_part_references(data: bytes,
                               own_name: str | os.PathLike | None = None) -> list[str]:
    """Return real component references, excluding NX boilerplate and self.

    > example
    > meaningful_part_references(part_bytes, "cap.prt")
      -> ["bracket.prt", "wheel.prt"], minus template references and self
    """
    own = Path(own_name).name.lower() if own_name else None
    return [
        name for name in parasolid.referenced_part_names(data)
        if name.lower() != own and name.lower() not in GENERIC_PART_REFERENCES
    ]


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
    for child_name in meaningful_part_references(data, name):
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
                          source_name: str) -> list[str]:
    """Copy locally resolvable references beside an uploaded root file.

    Suppressed children are skipped before their file is read: the parent
    flags them invisible, so no conversion or reference copy happens for them.

    > example
    > copy_referenced_parts(part_bytes, "C:/staged", "car.prt")
      -> the locally resolved source paths copied into C:/staged
    """
    copied = []
    roots = reference_roots(data)
    suppressed = suppressed_child_names(data)
    for name in meaningful_part_references(data, source_name):
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
