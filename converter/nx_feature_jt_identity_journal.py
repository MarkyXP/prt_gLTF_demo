"""NXOpen journal: save one feature state and export its exact facets.

Inputs:
  * Source .prt, feature JournalIdentifier, desired suppression state,
    variant directory, and report path from ``run_journal.exe -args``.

Outputs:
  * An isolated saved .prt copy and JSON report; the source part is read-only.
  * Per-face facet triangles when the requested feature is unsuppressed.
"""

import json
import os
import shutil
import sys

import NXOpen
import NXOpen.UF


def _arguments():
    try:
        args = sys.argv[sys.argv.index("-args") + 1:]
    except ValueError:
        args = sys.argv[1:]
    if len(args) != 5:
        raise RuntimeError(
            "Expected source.prt, feature JournalIdentifier, desired state, "
            "variant directory, and report path")
    source, journal_id, desired, variant_dir, report_path = args
    if desired.lower() not in ("true", "false"):
        raise RuntimeError("Desired suppression state must be true or false")
    return source, journal_id, desired.lower() == "true", variant_dir, report_path


source, journal_id, desired_suppressed, variant_dir, report_path = _arguments()
os.makedirs(variant_dir, exist_ok=True)
copy_path = os.path.join(variant_dir, os.path.basename(source))
shutil.copy2(source, copy_path)

session = NXOpen.Session.GetSession()
part, _status = session.Parts.OpenBaseDisplay(copy_path)
matches = [
    feature for feature in part.Features
    if feature.JournalIdentifier == journal_id
]
if len(matches) != 1:
    raise RuntimeError(
        "Expected exactly one feature %s, found %d"
        % (journal_id, len(matches)))
feature = matches[0]
source_suppressed = feature.Suppressed
if source_suppressed != desired_suppressed:
    mark = session.SetUndoMark(
        NXOpen.Session.MarkVisibility.Invisible,
        "Feature state identity probe",
    )
    (feature.Suppress if desired_suppressed else feature.Unsuppress)()
    session.UpdateManager.DoUpdate(mark)
if feature.Suppressed != desired_suppressed:
    raise RuntimeError(
        "NX did not apply requested suppression state for %s" % journal_id)

part.Save(
    NXOpen.BasePart.SaveComponents.FalseValue,
    NXOpen.BasePart.CloseAfterSave.FalseValue,
)

bodies = []
if not desired_suppressed:
    uf_facet = NXOpen.UF.UFSession.GetUFSession().Facet
    for body_index, body in enumerate(feature.GetBodies()):
        parameters = uf_facet.AskDefaultParameters()
        parameters.MaxFacetEdges = 3
        parameters.StoreFaceTags = True
        model = uf_facet.FacetSolid(body.Tag, parameters)
        expected_facets = uf_facet.AskNFacetsInModel(model)
        facets = []
        facet_id = uf_facet.CycleFacets(model, -1)
        while facet_id != -1:
            vertex_count, vertices = uf_facet.AskVerticesOfFacet(
                model, facet_id)
            if vertex_count != 3 or len(vertices) != vertex_count:
                raise RuntimeError(
                    "UF_FACET did not produce triangles for %s" % journal_id)
            face_id = uf_facet.AskFaceIdOfFacet(model, facet_id)
            if face_id < 0:
                raise RuntimeError(
                    "UF_FACET omitted face identity for %s" % journal_id)
            facets.append({
                "faceId": face_id,
                "vertices": [
                    [float(value) for value in point]
                    for point in vertices
                ],
            })
            facet_id = uf_facet.CycleFacets(model, facet_id)
        if len(facets) != expected_facets or not facets:
            raise RuntimeError(
                "UF_FACET returned incomplete geometry for %s" % journal_id)
        bodies.append({
            "bodyIndex": body_index,
            "facets": facets,
        })

with open(report_path, "w", encoding="utf-8") as report:
    json.dump({
        "journalId": feature.JournalIdentifier,
        "name": feature.Name,
        "sourceSuppressed": source_suppressed,
        "suppressed": feature.Suppressed,
        "bodyCount": len(feature.GetBodies()),
        "partUnits": str(part.PartUnits),
        "variantPath": copy_path,
        "bodies": bodies,
    }, report, separators=(",", ":"))
