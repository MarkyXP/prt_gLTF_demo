"""Per-component suppress-state reader for NX .prt assemblies (Phase 2).

The suppression record lives in the /root/FastLoad/Structure stream of
a component-assembly `.prt` as ONE list row with the format

    [NN 11 NN 01 11]  list1(17 tokens)  [02 e0 <4B hash>]  [01 11]  list2(16 tokens)

list2 encodes the per-component `OccPartData::PackSuppressBits` value as
a 1-byte atom per slot:

    0x0a = component suppressed (user-suppress, reason 1)
    0x01 = component registered and NOT suppressed
    0x00 = empty slot (no state)

Byte-decode evidence: private reverse-engineering find notes.

**Crucial caveat, proven byte-level: the state row is a SAVE-TIME record
only.**  NX 2412 does NOT read it on reload for the loaded-children fast
path; the reload decision is served by the CONFG-layer per-occurrence
reader (``loader_rows`` below, whose per-occurrence record has not been
byte-pinned yet).  In production this state-row reader is usable as an
assembly-level *discovery* aid alongside ``row_state.partition_hidden``
(which IS loader-driven for the blank flag), not as a byte-authoritative
skip list.

The slot ordering is a derived candidate solver slot(k) = (7k + 9) mod 16
that fits every observed fixture (k=1 -> slot 0; ladder and mixed
fixtures -> slots 3, 7).  Treat the formula as candidate and fail open
when the slot-to-name mapping cannot be confidently matched.

Use:
    streams = prt_jt.open_prt(path)
    state = suppress_state.state_row(
        suppress_state.structure_payload(streams))
    # -> {'off', 'nn', 'ref', 'list2', 'suppressed_slots'} or None
    sup, unsup, notes = suppress_state.partition_by_name(
        state, ref_names)

Loader-side layer (CONFG per-occurrence rows): one state block per
component occurrence inside /Root/UG_PART/UG_PART; the flag pair flips
with the reload suppression state on every controlled fixture, but
single-byte flips of it are NOT honored by the NX loader on reload (the
byte is written by the saver, not re-read as authoritative).  Keep it as
a diagnostic surface with the name join; do not gate skipping on it.

Pure stdlib.

CLI:
    python converter/suppress_state.py <part.prt> [<more.prt> ...]
    prints the suppress-state row (+ derived partition) per file.
"""
from __future__ import annotations

import os
import re
import sys


ROW_GRAMMAR = re.compile(rb"([\x00-\xff])\x11\1\x01\x11")
STATE_ROW_OPPLEN = 46
LIST2_ON = 30
LIST2_LEN = 16


def state_rows(payload: bytes):
    """Every state-row candidate: one per matched opener with the exact
    17-token list1 + c04504-ref + 01 11 + 16-token list2 shape.

    > example
    >>> payload = (b"\\x11\\x11\\x11\\x01\\x11" + b"\\x00" * 17
    ...            + b"\\x02\\xe0\\x44\\x55\\x66\\x77" + b"\\x01\\x11"
    ...            + b"\\x01" * 15 + b"\\x0a")
    >>> rows = state_rows(payload)
    >>> (rows[0]["nn"], rows[0]["ref"].hex(" "), rows[0]["suppressed_slots"])
    (17, '02 e0 44 55 66 77', [15])
    """
    out = []
    for m in ROW_GRAMMAR.finditer(payload):
        s = m.start()
        nn = m.group(1)[0]
        body = payload[s + 5:s + 5 + STATE_ROW_OPPLEN - 5]
        i = body.find(b"\x02\xe0")
        if i != 17:              # list1 must be exactly 17 tokens
            continue
        j = body.find(b"\x01\x11", i + 6)
        if j != 23:              # 02 e0 + 4B hash then 01 11
            continue
        row = payload[s:s + STATE_ROW_OPPLEN]
        ref = row[22:28]
        l2 = row[LIST2_ON:LIST2_ON + LIST2_LEN]
        out.append({"off": s, "nn": nn, "ref": ref, "list2": l2,
                    "suppressed_slots": [i for i, v in enumerate(l2)
                                         if v == 0x0a]})
    return out


def state_row(payload: bytes):
    """The ONE row carrying state (containing 0x0a tokens), or ``None``.

    Takes the payload bytes directly (no ``open_prt`` detour).

    > example
    >>> payload = (b"\\x11\\x11\\x11\\x01\\x11" + b"\\x00" * 17
    ...            + b"\\x02\\xe0\\x44\\x55\\x66\\x77" + b"\\x01\\x11"
    ...            + b"\\x01" * 15 + b"\\x0a")
    >>> state_row(payload)["off"]
    0
    """
    for row in state_rows(payload):
        if row["suppressed_slots"]:
            return row
    return None


def structure_payload(streams):
    """The /root/FastLoad/Structure payload bytes from a stream dict sim.

    > example
    >>> from types import SimpleNamespace
    >>> payload = structure_payload({"/root/FastLoad/Structure": SimpleNamespace(data=b"x")})
    >>> payload
    b'x'
    >>> structure_payload({"/root/FastLoad/Other": SimpleNamespace(data=b"x")}) is None
    True
    """
    for name, stream in streams.items():
        if name.endswith("/FastLoad/Structure"):
            return stream.data
    return None


def suppressed_slots(streams):
    """Flattened list2 slot indexes of every 0x0a (suppressed) token across
    the row(s) carrying state; normally exactly one row per file.

    > example
    >>> from types import SimpleNamespace
    >>> payload = (b"\\x11\\x11\\x11\\x01\\x11" + b"\\x00" * 17
    ...            + b"\\x02\\xe0\\x44\\x55\\x66\\x77" + b"\\x01\\x11"
    ...            + b"\\x01" * 15 + b"\\x0a")
    >>> suppressed_slots({"/root/FastLoad/Structure": SimpleNamespace(data=payload)})
    [15]
    """
    for name, stream in streams.items():
        if name.endswith("/FastLoad/Structure"):
            row = state_row(stream.data)
            if row is not None:
                return list(row["suppressed_slots"])
    return []


def slot_solver(k: int) -> int:
    """Derived candidate slot for the k-th component (0-based, creation order).

    Fits every observed fixture; treat as candidate, not proven.

    > example
    >>> slot_solver(0)
    9
    >>> slot_solver(1)
    0
    >>> slot_solver(16)
    9
    """
    return (7 * k + 9) % 16


def partition_by_name(state_row: dict | None,
                      ref_names: list[str]):
    """Return ``(sup, unsup, notes)``.

    ``sup``    names in ref_names that map (via the candidate slot solver)
               to a 0x0a slot.  Only available when slot count is within
               range and the solver-referenced slot is marked suppressed
               in the record.
    ``unsup``  remaining names in ref_names.
    ``notes``  diagnostic strings describing what the driver found.

    Fails open: names whose candidate slot does NOT read suppressed are
    kept in ``unsup``, so callers never over-drop.

    > example
    >>> payload = (b"\\x11\\x11\\x11\\x01\\x11" + b"\\x00" * 17
    ...            + b"\\x02\\xe0\\x44\\x55\\x66\\x77" + b"\\x01\\x11"
    ...            + b"\\x01" * 9 + b"\\x0a" + b"\\x01" * 6)
    >>> sup, unsup, notes = partition_by_name(state_row(payload), ["child1"])
    >>> sup, unsup
    (['child1'], [])
    """
    notes: list[str] = []
    if not state_row:
        notes.append("no suppress state row in payload: nothing suppressed")
        return [], list(ref_names), notes
    l2 = state_row["list2"]
    notes.append("suppress row found: nn=%d ref=%s supslots=%s list2=%s"
                 % (state_row["nn"], state_row["ref"].hex(" "),
                    state_row["suppressed_slots"], l2.hex(" ")))
    sup: list[str] = []
    unsup: list[str] = []
    seen: set[str] = set()
    for k, name in enumerate(ref_names):
        slot = slot_solver(k)
        if slot >= len(l2):
            unsup.append(name)
            notes.append("slot_solver(%d)=%d out of range for %s"
                         % (k, slot, name))
            continue
        if l2[slot] == 0x0a and name not in seen:
            sup.append(name)
            seen.add(name)
        else:
            unsup.append(name)
    return sup, unsup, notes


def main(argv=None):
    """CLI: print the suppress-state row and a synthetic-name partition."""
    if argv is None:
        argv = sys.argv[1:]
    from prt_jt import open_prt
    for path in argv:
        streams = open_prt(path)
        row = None
        for name, stream in streams.items():
            if name.endswith("/FastLoad/Structure"):
                row = state_row(stream.data)
                break
        print("== %s" % os.path.basename(path))
        if row is not None:
            print("  sup slots: %s" % row["suppressed_slots"])
            sup, unsup, notes = partition_by_name(
                row, list(["child%d" % (k + 1)
                           for k in range(len(row["list2"]))]))
            for note in notes:
                print("   note: %s" % note)
        else:
            print("  no state row")


CONFG_BLOCK = re.compile(
    rb"\x03\x06[\x20-\x7e]{1,24}\x00\x03\x26[0-9a-f\-]{36}\x00\x05\x01\xff"
    rb"\x00\xff\xff([^\xff])\xff([^\xff])([^\xff])")


def loader_rows(payload: bytes):
    """CONFG-layer rows: one per component occurrence (diagnostic).

    Locator (NX 2412 writer family)::

        03 06 <child name> 00 03 26 <36-char guid> 00 05 01 ff
        00 ff ff <X> ff <S> <S> ...

    ``S`` = per-child suppression flag pair (00 not suppressed / 01
    suppressed, duplicated). ``X`` is the per-child state-history counter
    (1 = no state events, +1 per suppress/unsuppress transition).
    """
    rows = []
    for m in CONFG_BLOCK.finditer(payload):
        nstart = m.start() + 2
        nend = payload.find(b"\x00", nstart)
        name = payload[nstart:nend].decode("latin1", "replace")
        s = (m.group(2)[0], m.group(3)[0])
        rows.append({
            "child": name,
            "suppressed": (s[0] == 1 and s[1] == 1),
            "s": s,
            "x": m.group(1)[0],
            "off": m.start(),
        })
    return rows


def loader_suppress_flags(data: bytes):
    """Suppression flags per component occurrence, keyed by child name.

    Returns ``[(child_name, suppressed_bool), ...]`` in stream order.

    > example
    >>> loader_suppress_flags(b"no confg blocks here")
    []
    """
    return [(row["child"], row["suppressed"]) for row in loader_rows(data)]


def loader_suppressed_names(data: bytes):
    """Child names whose loader flag pair reads suppressed."""
    return [child for child, want in loader_suppress_flags(data) if want]


if __name__ == "__main__":
    main()
