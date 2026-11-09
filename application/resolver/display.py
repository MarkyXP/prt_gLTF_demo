"""Body colour painting and fully-transparent body filtering.

Inputs:
  * A converted .prt path (display records are read from its bytes).
  * The embedded-JT mesh collection decoded from the same bytes.

Outputs:
  * The collection with the part's body colour and translucency alpha
    painted onto every mesh entry.
  * The number of mesh entries dropped as unrenderable.

Body colour and translucency come from the display records
(converter/color_index.py + converter/palette.py). NX translucency is
opposed to glTF alpha: 0 = opaque, 100 = fully transparent. A small alpha
floor keeps mostly-transparent bodies visible in three.js (alpha 0 renders
nothing and drop shadows disappear); fully transparent bodies are dropped
entirely, because they would never show in the render.
"""
from __future__ import annotations


def paint_body_display(path: str, collection: list[dict]) -> tuple[list[dict], int]:
    """Paint, then drop mesh entries that could never render visibly.

    Fully transparent entries (translucency 100) are removed entirely and
    counted; partially transparent entries keep the alpha floor painting.

    > example
    > paint_body_display("part.prt", collection)
      -> (painted_collection, dropped_count); counts 0 entries when no
      display records decode
    """
    try:
        from color_index import body_palette_index_rgb, body_translucency
        percent = body_translucency(str(path)) or 0
        colour = body_palette_index_rgb(str(path))[1]
        if percent is None:
            percent = 0
        if colour and isinstance(colour, tuple) and len(colour) >= 3:
            rgb = list(colour[:3])
            for entry in collection:
                if entry.get("color") in (None, [0.8, 0.8, 0.8]):
                    entry["color"] = rgb
        if percent >= 100:
            return [], len(collection)
        if percent:
            alpha = max(0.01, 1.0 - percent / 100.0)
            for entry in collection:
                if isinstance(entry.get("color"), list) and len(entry["color"]) == 3:
                    entry["color"] = entry["color"] + [round(alpha, 6)]
    except Exception:
        return collection, 0
    return collection, 0
