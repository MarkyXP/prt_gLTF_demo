# -*- coding: utf-8 -*-
"""Child-reference selection over parsed ``EXTREFSTREAM`` payloads.

Inputs:
  * A stream mapping from an NX container (or any ``{name: bytes}`` mapping).

Outputs:
  * Unique exact ``.prt`` entries with their slot-two directory, reference
    code, and load-mode token.
  * Slot-two search roots.
  * A validity flag distinguishing a complete EXTREFSTREAM from a part
    that only has legacy regex-scannable text.

Payload framing is owned by :mod:`extref`; this module only selects entries
from streams that parsed completely.
"""
from __future__ import annotations

import sys

import extref

MAGIC = extref.MAGIC


def payloads(streams):
    """Yield payloads from a stream mapping whose bytes start with the magic.

    > example
    >>> list(payloads({"a": b"EXTREFSTREAM-x", "b": b"other"}))
    [b'EXTREFSTREAM-x']
    """
    for value in streams.values():
        data = getattr(value, "data", value)
        if isinstance(data, (bytes, bytearray)) and bytes(data[:12]) == MAGIC:
            yield bytes(data)


def reference_entries(streams):
    """Exact child entries from every valid trailing string table.

    Names keep table order; the first record whose slot zero resolves to a
    name supplies that entry's directory, code, and load mode. Entries are
    de-duplicated case-insensitively.

    > example
    >>> reference_entries({})
    []
    """
    ordered = {}
    for payload in payloads(streams):
        parsed = extref.parse_extref(payload)
        if parsed is None:
            continue
        for string in parsed["strings"]:
            name = string["text"]
            if not name.lower().endswith(".prt"):
                continue
            key = name.casefold()
            entry = ordered.setdefault(key, {
                "name": name,
                "directory": None,
                "reference_code": None,
                "load_mode": None,
            })
            for record in parsed["records"]:
                if record.get("kind") != "handle_set":
                    continue
                if (record.get("child_name") or "").casefold() != key:
                    continue
                entry["directory"] = record.get("directory")
                entry["reference_code"] = record.get("reference_code")
                entry["load_mode"] = record.get("load_mode")
                break
    return list(ordered.values())


def reference_names(streams):
    """Exact ``.prt`` names from every valid trailing string table.

    > example
    >>> reference_names({})
    []
    """
    return [entry["name"] for entry in reference_entries(streams)]


def search_roots(streams):
    """Slot-two directories from records with complete slot relations.

    > example
    >>> search_roots({})
    []
    """
    roots = []
    seen = set()
    for payload in payloads(streams):
        parsed = extref.parse_extref(payload)
        if parsed is None:
            continue
        for record in parsed["records"]:
            if record.get("kind") != "handle_set":
                continue
            directory = record.get("directory")
            if not directory:
                continue
            if not any(token in directory for token in ("\\", "/", ":")):
                continue
            key = directory.casefold()
            if key not in seen:
                seen.add(key)
                roots.append(directory)
    return roots


def has_valid_stream(streams):
    """Whether at least one payload parses as a complete EXTREFSTREAM.

    > example
    >>> has_valid_stream({})
    False
    """
    return any(extref.parse_extref(payload) is not None
               for payload in payloads(streams))


def main(argv=None):
    """CLI: print the parsed extent of each EXTREFSTREAM in a .prt file."""
    from prt_jt import open_prt

    argv = sys.argv[1:] if argv is None else argv
    for path in argv:
        streams = open_prt(path)
        print("== %s" % path)
        for name, stream in streams.items():
            parsed = extref.parse_extref(getattr(stream, "data", stream))
            if parsed is None:
                continue
            print("  %s table@%d strings=%d records=%d" % (
                name, parsed["string_table_offset"],
                len(parsed["strings"]), len(parsed["records"])))
            for entry in reference_entries({name: stream}):
                print("   child=%s dir=%s mode=%s" % (
                    entry["name"], entry["directory"], entry["load_mode"]))


if __name__ == "__main__":
    main()
