"""NX journal: regenerate face-coloured JT geometry on a temporary copy.

Run with:
  run_journal.exe nx_face_colour_sidecar.py -args <part.prt> [output.json]

NXOpen supplies the active RGB values for the palette indices used by the
part. The source part is never modified, and the generated sidecar is written
only after an exact, complete triangle join with the source JT geometry.
"""

import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

import NXOpen
import NXOpen.UF

_REPO = str(Path(__file__).resolve().parent)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from converter.face_colour_sidecar import (  # noqa: E402
    build_colour_mesh_sidecar,
    sidecar_path,
)


def _arguments():
    paths = [argument for argument in sys.argv[1:]
             if argument and not argument.startswith("-")]
    source = next((
        argument for argument in reversed(paths)
        if argument.lower().endswith(".prt") and os.path.isfile(argument)
    ), None)
    if source is None:
        raise RuntimeError("Pass the source .prt after -args")
    output = next((
        argument for argument in reversed(paths)
        if argument.lower().endswith(".json")
    ), None)
    return source, output or str(sidecar_path(source))


def _palette_rgb(part):
    """Read the NX-open palette RGB for every palette used by its faces."""
    display = NXOpen.UF.UFSession.GetUFSession().Disp
    colours = {}
    for body in part.Bodies:
        for face in body.GetFaces():
            index = face.Color
            if index in colours:
                continue
            _name, rgb = display.AskColor(index, 0)
            values = tuple(float(value) for value in rgb)
            if len(values) != 3 or any(
                    not math.isfinite(value) or value < 0 or value > 1
                    for value in values):
                raise RuntimeError(
                    "NX returned an invalid RGB value for palette %d"
                    % index)
            colours[index] = values
    return colours


def main():
    source, output = _arguments()
    folder = tempfile.mkdtemp(prefix="nx_face_colour_sidecar_")
    temporary_part = os.path.join(folder, os.path.basename(source))
    shutil.copy2(source, temporary_part)
    session = NXOpen.Session.GetSession()
    part = None
    try:
        part, _status = session.Parts.OpenBaseDisplay(temporary_part)
        bodies = list(part.Bodies)
        if not bodies or not bodies[0].GetFaces():
            raise RuntimeError("The part has no solid face to regenerate")
        palette_rgb = _palette_rgb(part)
        face = bodies[0].GetFaces()[0]
        original_colour = face.Color
        alternate_colour = 1 if original_colour != 1 else 2
        face.Color = alternate_colour
        face.Color = original_colour
        mark = session.SetUndoMark(
            NXOpen.Session.MarkVisibility.Invisible,
            "Regenerate face-colour sidecar",
        )
        session.UpdateManager.DoUpdate(mark)
        part.RegenerateDisplayFacets(True)
        part.Save(
            NXOpen.BasePart.SaveComponents.FalseValue,
            NXOpen.BasePart.CloseAfterSave.FalseValue,
        )
        part.Close(
            NXOpen.BasePartCloseWholeTree.FalseValue,
            NXOpen.BasePartCloseModified.CloseModified,
            None,
        )
        part = None
        report = build_colour_mesh_sidecar(
            source, temporary_part, output, palette_rgb=palette_rgb)
        print("wrote %s (%d triangles, %d colours)" % (
            report["path"], report["triangles"], report["colours"]))
    finally:
        if part is not None:
            part.Close(
                NXOpen.BasePartCloseWholeTree.FalseValue,
                NXOpen.BasePartCloseModified.CloseModified,
                None,
            )
        shutil.rmtree(folder)


if __name__ == "__main__":
    main()
