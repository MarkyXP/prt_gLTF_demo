"""Stored occurrence-label to child-filename matching.

Some NX occurrence labels omit the numeric suffix added to the saved child
filename. Matching is case-insensitive and tolerates only a trailing
numeric suffix, so ``bearing`` and ``bearing128.prt`` agree while two
genuinely different siblings stay distinct.
"""
from __future__ import annotations


def family_name_matches_child(family_key, stem):
    """Whether a stored occurrence name identifies one child prototype.

    > example
    >>> family_name_matches_child(
    ...     ("name", "LBS000317_CONCENTRIC_BEARING"),
    ...     "LBS000317_concentric_bearing128.prt")
    True
    >>> family_name_matches_child(("name", "part"), r"C:\\parts\\part12.prt")
    True
    >>> family_name_matches_child(("conn", b"abcd"), "bearing.prt")
    False
    """
    if not isinstance(family_key, tuple) or len(family_key) != 2:
        return False
    kind, label = family_key
    if kind != "name" or not isinstance(label, str):
        return False
    child = str(stem).replace("\\", "/").rsplit("/", 1)[-1]
    if child.casefold().endswith(".prt"):
        child = child[:-4]
    label = label.casefold()
    child = child.casefold()
    suffix = child[len(label):] if child.startswith(label) else None
    return suffix == "" or bool(suffix and suffix.isdigit())
