"""Safe subset selection and overlap helpers for RMFastLoad membership.

The selector mirrors cadmpeg's complete-set relation but never drops a body
whose node-id set is empty (incomplete evidence), and drops nothing when no
complete body set is a subset of the membership table.

> example
>>> select_active_bodies({"a": {1, 2}, "b": {2, 3}}, [1, 2, 9])["dropped"]
frozenset({'b'})
"""
from __future__ import annotations


def select_active_bodies(body_node_ids, membership_ids):
    """Apply the complete-set subset relation to body node-id sets.

    ``body_node_ids`` maps a body key to its complete nonempty FACE/EDGE/
    VERTEX node-id set. Bodies whose set is empty (incomplete evidence) are
    never dropped. When no complete body is a subset of the membership set,
    nothing is dropped and ``decisive`` is false.

    > example
    >>> result = select_active_bodies({"a": {1, 2}, "b": {2, 3}}, [1, 2, 9])
    >>> sorted(result["selected"]), sorted(result["dropped"])
    (['a'], ['b'])
    >>> select_active_bodies({"a": {1}}, [])["decisive"]
    False
    """
    active = set(membership_ids)
    complete = {
        key: frozenset(values) for key, values in body_node_ids.items()
        if values
    }
    unresolved = frozenset(set(body_node_ids) - set(complete))
    selected = frozenset(
        key for key, values in complete.items() if values <= active)
    decisive = bool(selected)
    dropped = frozenset(complete) - selected if decisive else frozenset()
    retained = frozenset(body_node_ids) - dropped
    return {
        "decisive": decisive,
        "selected": selected,
        "unresolved": unresolved,
        "dropped": dropped,
        "retained": retained,
        "reason": (
            "complete subset relation" if decisive
            else "no complete body subset; retaining every body"),
    }


def overlap_summary(membership_ids, identifier_sets):
    """Count membership overlap against named identifier sets.

    > example
    >>> overlap_summary([1, 2, 3], {"jt": [3, 4]})
    {'jt': {'known': 2, 'overlap': 1}}
    """
    active = set(membership_ids)
    return {
        name: {"known": len(values), "overlap": len(active & set(values))}
        for name, values in identifier_sets.items()
    }
