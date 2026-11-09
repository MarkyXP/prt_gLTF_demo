"""Parent-side per-master name rows (scan helpers).

The parent's OM record area carries a per-master name table
(``39 04 <len> 3c 57 <STEM>`` + ``@$PART_NAME>``/``@DES>``), roughly
0x5600 bytes away from the placement frames.  The name rows are real
data (verified on the corpus), but the id-graph that links each row to
its placement family (the ``81/82 xx`` compact ids seen on both sides)
still needs its OM-record framing decoded before a family can be bound
by name.  ``parent_stem_labels``/``match_stem`` are kept as the tested
scan helpers for that work; no caller binds families from them yet.

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

NAME_ROW_SCAN = 512


def parent_stem_labels(data):
    """Decode the parent's per-master name rows: ``<STEM>@$PART_NAME>``.

    The component-names table in the parent's OM record area writes one row
    per referenced master: ``39 04 <len> 3c 57 <STEM>`` optionally followed
    by ``@$PART_NAME>``/``@DES>`` and a NUL.  Names are upper-cased there
    while file stems are lower-case, so matching is case-insensitive and
    ignores a trailing ``.prt``.  Rows are returned in payload order as
    ``(offset, stem)`` so family binding can use record adjacency.
    """
    import re
    rows = []
    for match in re.finditer(
            rb'\x39\x04.\x3c\x57[A-Z0-9_.\-]{2,120}?\x40'
            rb'(?:\$PART_NAME|DES)[>\x00]', data):
        text = match.group(0)
        tail = text.find(b'@')
        word = text[5:tail]
        stem = word.decode('ascii', 'replace').lower()
        if stem.endswith('.prt'):
            stem = stem[:-4]
        rows.append((match.start(), stem))
    return rows


def match_stem(candidates, stem):
    """Exact stemmed match, else unique prefix-family match; else ``None``.

    > example
    >>> match_stem(["21_2629_100"], "21_2629")
    '21_2629_100'
    >>> match_stem(["scr_m4x8", "scr_m4x12"], "scr_m4") is None
    True
    """
    same = [stem] if stem in candidates else []
    if len(same) == 1:
        return same[0]
    prefix = [s for s in candidates
              if stem and s.startswith(stem) or stem.startswith(s)]
    if len(set(prefix)) == 1:
        return prefix[0]
    return None
