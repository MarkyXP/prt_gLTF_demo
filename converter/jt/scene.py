"""Select embedded JT shape segments using the version 9 scene graph.

Inputs are an inflated LSG segment and the segment IDs in the JT piece.
The result is the set of selected shape segment IDs, or None when this
scene graph does not contain a supported range-LOD hierarchy.
"""
import logging
import struct

from .attributes import G_TID_LATE_LOADED, split_lsg_elements

LOGGER = logging.getLogger(__name__)
_GROUP = "1b10dd10c82ad1119b6b0080c7bb5997"
_PARTITION = "3e10dd10c82ad1119b6b0080c7bb5997"
_RANGE_LOD = "4c10dd10c82ad1119b6b0080c7bb5997"
_SHAPE = "7710dd10c82ad1119b6b0080c7bb5997"
_SHAPE_KEY = "JT_LLPROP_SHAPEIMPL".encode("utf-16le")


def _group_children(data):
    """Read the flags and child IDs of a version 9 group node.

    > example
    > _group_children(group_node_bytes) -> (flags, [child_id, ...])
    """
    if len(data) < 16 or struct.unpack_from("<h", data)[0] != 1:
        raise ValueError("unsupported base node")
    flags, count = struct.unpack_from("<Ii", data, 2)
    if count < 0 or count > (len(data) - 16) // 4:
        raise ValueError("invalid attribute count")
    offset = 10 + count * 4
    if struct.unpack_from("<h", data, offset)[0] != 1:
        raise ValueError("unsupported group node")
    child_count = struct.unpack_from("<i", data, offset + 2)[0]
    offset += 6
    if child_count < 0 or child_count > (len(data) - offset) // 4:
        raise ValueError("invalid child count")
    return flags, list(struct.unpack_from("<%di" % child_count, data, offset))


def _property_table(raw, elements):
    """Read property atom bindings after the second LSG end marker.

    > example
    > _property_table(lsg_bytes, elements) -> {node_id: [value_id, ...]}
    """
    markers = [element["off"] for element in elements if element.get("kind") == "marker"]
    if len(markers) != 2:
        raise ValueError("missing LSG markers")
    offset = markers[-1] + 20
    version, count = struct.unpack_from("<hi", raw, offset)
    offset += 6
    if version != 1 or count < 0 or count > len(raw) // 8:
        raise ValueError("invalid property table")
    result = {}
    for _ in range(count):
        node = struct.unpack_from("<i", raw, offset)[0]
        offset += 4
        values = []
        while True:
            key = struct.unpack_from("<i", raw, offset)[0]
            offset += 4
            if key == 0:
                break
            value = struct.unpack_from("<i", raw, offset)[0]
            offset += 4
            values.append((key, value))
        result[node] = values
    return result


def selected_shape_segments(raw, segment_guids):
    """Select all shapes in the nearest range-LOD branch, preserving groups.

    > example
    > selected_shape_segments(lsg_bytes, segment_guids)
    > -> {"segment-guid"} or None when no supported hierarchy is present
    """
    try:
        elements = split_lsg_elements(raw)
        nodes = {element["oid"]: element for element in elements if "oid" in element}
        if not any(node["class"] == _RANGE_LOD for node in nodes.values()):
            return None
        properties = _property_table(raw, elements)
        selected = set()
        visited = set()

        def walk(node_id):
            if node_id in visited:
                raise ValueError("repeated or cyclic node")
            visited.add(node_id)
            node = nodes[node_id]
            kind = node["class"]
            if kind in (_GROUP, _PARTITION, _RANGE_LOD):
                flags, children = _group_children(node["data"])
                if flags & 1:
                    return
                if kind == _RANGE_LOD:
                    children = children[:1]
                for child in children:
                    walk(child)
            elif kind == _SHAPE:
                bindings = []
                for key, value in properties.get(node_id, []):
                    if key in nodes and _SHAPE_KEY in nodes[key]["data"]:
                        bindings.append(nodes[value])
                if len(bindings) != 1 or bindings[0]["class"] != G_TID_LATE_LOADED:
                    raise ValueError("missing shape binding")
                matches = [guid for guid in segment_guids
                           if bytes.fromhex(guid) in bindings[0]["data"]]
                if len(matches) != 1:
                    raise ValueError("unresolved shape segment")
                selected.add(matches[0])
            else:
                raise ValueError("unsupported scene node " + kind)

        walk(0)
        if not selected:
            raise ValueError("no selected shapes")
        return selected
    except (ValueError, KeyError, struct.error) as error:
        LOGGER.warning("Cannot select JT range-LOD shapes: %s", error)
        return None
