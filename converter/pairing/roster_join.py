"""Structural family binding from the FastLoad roster and EXTREF names.

The documented unique join is used only when every step is unambiguous:

1. group placement frames by their canonical UUID;
2. require exactly one roster UUID entry with that text;
3. collect the roster uses of that UUID;
4. require exactly one prototype index and a use count equal to the
   placement count;
5. resolve the prototype name to exactly one exact EXTREF ``.prt`` name;
6. require that child in the resolved child roster.

Any ambiguity leaves the family for the existing name/geometry pairing.
A unique roster prototype name can also complete a stored family label,
using the existing numeric-suffix tolerance.

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

from .names import family_name_matches_child


def _stem_key(value):
    """Case-insensitive part stem with any ``.prt`` suffix removed."""
    text = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    if text.casefold().endswith(".prt"):
        text = text[:-4]
    return text.casefold()


def _resolved_stem(name, stems):
    """Resolved stem matching ``name`` by case-insensitive part stem."""
    key = _stem_key(name)
    for stem in stems:
        if _stem_key(stem) == key:
            return stem
    return None


def _unique_extref_stem(prototype_name, extref_names, stems):
    """The single resolved child named exactly like one prototype."""
    matches = {
        _stem_key(name) for name in extref_names
        if _stem_key(name) == _stem_key(prototype_name)
    }
    if len(matches) != 1:
        return None
    return _resolved_stem(matches.pop(), stems)


def _placement_groups(families):
    """Map each canonical placement UUID to its families/frames."""
    groups = {}
    for key, frames in families:
        for frame in frames:
            uuid = frame.get("uuid")
            if uuid:
                groups.setdefault(uuid, []).append((key, frame))
    return groups


def uuid_join(families, roster, extref_names, stems):
    """Unique placement-UUID -> prototype -> EXTREF child assignments.

    Returns ``({family_key: stem}, notes)``. The assignment is withheld
    whenever a UUID, prototype, use count, filename, or family is ambiguous.
    """
    if not roster or not roster.get("prototypes") or not roster.get("uuids"):
        return {}, []
    groups = _placement_groups(families)
    if not groups:
        return {}, ["roster present; no canonical placement UUIDs"]

    uuids = roster["uuids"]
    uses = {}
    for occurrence in roster.get("occurrences", []):
        uses.setdefault(occurrence["uuid_index"], []).append(occurrence)
    prototypes = roster["prototypes"]
    family_frames = dict(families)

    candidates = {}
    candidate_notes = {}
    conflicts = set()
    notes = []
    for uuid, items in groups.items():
        entries = [entry for entry in uuids if entry["uuid"] == uuid]
        if len(entries) != 1:
            notes.append("uuid %s: %d roster UUID entries (ambiguous)"
                         % (uuid, len(entries)))
            continue
        entry = entries[0]
        group_uses = uses.get(entry["ordinal"] + 1, [])
        indices = {use["prototype_index"] for use in group_uses}
        if len(indices) != 1 or len(group_uses) != len(items):
            notes.append("uuid %s: uses/prototype ambiguous (uses=%d, "
                         "placements=%d, prototypes=%d)"
                         % (uuid, len(group_uses), len(items), len(indices)))
            continue
        prototype_index = indices.pop()
        if not 1 <= prototype_index <= len(prototypes):
            notes.append("uuid %s: prototype index out of range" % uuid)
            continue
        prototype = prototypes[prototype_index - 1]
        stem = _unique_extref_stem(
            prototype["name"], extref_names, stems)
        if stem is None:
            notes.append("uuid %s: prototype %s has no unique resolved "
                         "EXTREF child" % (uuid, prototype["name"]))
            continue
        family_keys = {key for key, _frame in items}
        if len(family_keys) != 1:
            notes.append("uuid %s: shared by %d families (ambiguous)"
                         % (uuid, len(family_keys)))
            continue
        key = family_keys.pop()
        known_uuids = {frame.get("uuid")
                       for frame in family_frames.get(key, [])
                       if frame.get("uuid")}
        if known_uuids != {uuid}:
            notes.append("uuid %s: family %s mixes placement UUIDs"
                         % (uuid, key))
            continue
        if key in candidates and candidates[key] != stem:
            conflicts.add(key)
            continue
        candidates[key] = stem
        candidate_notes[key] = (
            "uuid join %s: prototype %s -> %s (%d instance(s))"
            % (uuid, prototype["name"], stem, len(items)))

    assignments = {}
    used = {}
    for key, stem in candidates.items():
        if key in conflicts:
            notes.append("uuid join family %s: conflicting prototypes "
                         "(fallback)" % (key,))
            continue
        if stem in used and used[stem] != key:
            notes.append("uuid join: %s claimed by two families "
                         "(fallback)" % stem)
            conflicts.add(used[stem])
            conflicts.add(key)
            continue
        used[stem] = key
        assignments[key] = stem
    for key in conflicts:
        assignments.pop(key, None)
    for key in assignments:
        notes.append(candidate_notes[key])
    return assignments, notes


def prototype_name_join(families, roster, extref_names, stems,
                        assigned=None):
    """Bind a stored family label through a unique prototype name.

    Only families whose own label does not already identify one resolved
    child are considered, so existing name matching keeps precedence.
    """
    assigned = {} if assigned is None else assigned
    if not roster or not roster.get("prototypes"):
        return {}, []
    notes = []
    bindings = {}
    for key, _frames in families:
        if key in assigned:
            continue
        if not (isinstance(key, tuple) and len(key) == 2
                and key[0] == "name" and isinstance(key[1], str)):
            continue
        direct = [stem for stem in stems
                  if family_name_matches_child(key, stem)]
        if len(direct) == 1:
            continue
        matches = {}
        for prototype in roster["prototypes"]:
            name = prototype.get("name") or ""
            if not family_name_matches_child(
                    ("name", key[1]), name + ".prt"):
                continue
            stem = _unique_extref_stem(name, extref_names, stems)
            if stem is not None:
                matches[stem] = prototype
        if len(matches) != 1:
            continue
        bindings[key] = next(iter(matches.items()))
    applied = {}
    used = set(assigned.values())
    for key, (stem, prototype) in bindings.items():
        if stem in used:
            continue
        used.add(stem)
        applied[key] = stem
        notes.append("prototype name %s -> %s" % (prototype["name"], stem))
    return applied, notes
