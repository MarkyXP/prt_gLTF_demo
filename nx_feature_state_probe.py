"""Save controlled feature-suppression variants as temporary NX copies.

Run with ``run_journal.exe nx_feature_state_probe.py -args <source.prt>
EXTRUDE(1)=false``. The source remains untouched; the modified copy and an
NXOpen state report are written to a new ``%TEMP%`` subdirectory.
"""

import os
import re
import shutil
import sys
import tempfile

import NXOpen
import NXOpen.UF


def _arguments():
    source = next(
        (value for value in reversed(sys.argv)
         if value.lower().endswith(".prt") and os.path.isfile(value)),
        None,
    )
    targets = [
        match.groups()
        for value in sys.argv
        if (match := re.fullmatch(
           r"(EXTRUDE\(\d+(?::\d+[A-Z]?)?\))=(true|false)",
           value,
           re.IGNORECASE,
        ))
    ]
    if source is None or not targets:
        raise RuntimeError(
            "Pass a source .prt and EXTRUDE(identifier)=true|false target(s)")
    return source, targets


source, targets = _arguments()
folder = tempfile.mkdtemp(prefix="nx_feature_state_")
copy_path = os.path.join(folder, os.path.basename(source))
shutil.copy2(source, copy_path)

session = NXOpen.Session.GetSession()
part, _status = session.Parts.OpenBaseDisplay(copy_path)
features = {item.JournalIdentifier: item for item in part.Features}
states = {}
target_features = []
for identifier, desired in targets:
    feature = features.get(identifier)
    if feature is None:
        raise RuntimeError("Feature not found: %s" % identifier)
    suppressed = desired.lower() == "true"
    if feature.Suppressed != suppressed:
        mark = session.SetUndoMark(
            NXOpen.Session.MarkVisibility.Invisible,
            "Feature suppression diagnostic",
        )
        (feature.Suppress if suppressed else feature.Unsuppress)()
        session.UpdateManager.DoUpdate(mark)
    states[identifier] = {
        "suppressed": feature.Suppressed,
        "body_count": len(feature.GetBodies()),
    }
    target_features.append((identifier, feature))

part.Save(
    NXOpen.BasePart.SaveComponents.FalseValue,
    NXOpen.BasePart.CloseAfterSave.FalseValue,
)
with open(os.path.join(folder, "state.txt"), "w", encoding="utf-8") as report:
    report.write(repr(states))
    report.write("\n")
    report.write(repr([
        {
            "journal_id": item.JournalIdentifier,
            "name": item.Name,
            "tag": item.Tag,
            "suppressed": item.Suppressed,
            "body_count": len(item.GetBodies()),
        }
        for item in part.Features
    ]))
    report.write("\n")
    bodies = list(part.Bodies)
    uf_session = NXOpen.UF.UFSession.GetUFSession()
    facet_api = getattr(uf_session, "Facet", None)
    facet_namespace = getattr(NXOpen.UF, "Facet", None)
    facet_parameters_type = getattr(facet_namespace, "Parameters", None)
    part_units_type = getattr(NXOpen.Part, "Units", None)
    target_facets = []
    for identifier, feature in target_features:
        bodies_for_feature = list(feature.GetBodies())
        if not bodies_for_feature or facet_api is None:
            target_facets.append({
                "feature": identifier,
                "status": "no_feature_body_or_uf_facet_api",
            })
            continue
        try:
            parameters = facet_api.AskDefaultParameters()
            parameters.MaxFacetEdges = 3
            parameters.StoreFaceTags = True
            model = facet_api.FacetSolid(
                bodies_for_feature[0].Tag, parameters)
            facet_count = facet_api.AskNFacetsInModel(model)
            facets = []
            facet_id = facet_api.CycleFacets(model, -1)
            while facet_id != -1:
                vertex_count, flat_vertices = facet_api.AskVerticesOfFacet(
                    model, facet_id)
                if len(flat_vertices) != vertex_count:
                    raise RuntimeError("UF_FACET returned an incomplete facet")
                face_id = facet_api.AskFaceIdOfFacet(model, facet_id)
                solid_face_tag = facet_api.AskSolidFaceOfFacet(
                    model, facet_id)
                facets.append({
                    "facet_id": facet_id,
                    "face_id": face_id,
                    "solid_face_tag": solid_face_tag,
                    "vertices": [list(point) for point in flat_vertices],
                })
                facet_id = facet_api.CycleFacets(model, facet_id)
            target_facets.append({
                "feature": identifier,
                "model_tag": model,
                "facet_count": facet_count,
                "facets": facets,
            })
        except Exception as error:
            target_facets.append({
                "feature": identifier,
                "status": "facet_export_error",
                "error": repr(error),
            })
    body_facet_info = {}
    if bodies:
        body = bodies[0]
        try:
            body_facet_info["body_facet_count"] = body.GetNumberOfFacets()
            facet = body.GetFirstFacetOnBody()
            body_facet_info["first_facet_type"] = (
                str(type(facet)) if facet is not None else None)
            body_facet_info["first_facet_methods"] = (
                [
                    name for name in dir(facet)
                    if any(word in name.lower()
                           for word in ("facet", "vertex", "point"))
                ] if facet is not None else [])
        except Exception as error:
            body_facet_info["body_facet_error"] = repr(error)
    report.write(repr({
        "target_facets": target_facets,
        "uf_types": [
            name for name in dir(NXOpen.UF)
            if any(word in name.lower()
                   for word in ("facet", "parameter"))
        ],
        "uf_facet_constants": {
            "namespace": [
                name for name in dir(facet_namespace)
                if any(word in name.lower() for word in ("null", "none"))
            ] if facet_namespace is not None else None,
            "api": [
                name for name in dir(facet_api)
                if any(word in name.lower() for word in ("null", "none"))
            ] if facet_api is not None else None,
            "module": [
                name for name in dir(NXOpen.UF)
                if "facet" in name.lower()
                and any(word in name.lower() for word in ("null", "none"))
            ],
        },
        "part_units": str(part.PartUnits),
        "part_units_values": {
            name: str(getattr(part_units_type, name))
            for name in dir(part_units_type)
            if not name.startswith("_")
        } if part_units_type is not None else None,
        "uf_facet_signatures": {
            name: getattr(facet_api, name).__doc__
            for name in ("FacetSolid", "AskDefaultParameters",
                         "AskModelsOfSolid", "AskVerticesOfFacet",
                         "AskNFacetsInModel", "CycleFacets",
                         "AskNumVertsInFacet", "AskSolidFaceOfFacet")
            if facet_api is not None and hasattr(facet_api, name)
        },
        "facet_parameter_fields": [
            name for name in dir(facet_parameters_type)
            if not name.startswith("_")
        ] if facet_parameters_type is not None else None,
        "uf_facet_methods": [
            name for name in dir(facet_api)
            if any(word in name.lower()
                   for word in ("facet", "model", "face", "vertex"))
        ] if facet_api is not None else None,
        "part_facet_methods": [
            name for name in dir(part)
            if any(word in name.lower()
                   for word in ("facet", "tessell", "mesh"))
        ],
        "body_type": str(type(bodies[0])) if bodies else None,
        "body_methods": [
            name for name in dir(bodies[0])
            if any(word in name.lower()
                   for word in ("facet", "face", "vertex", "mesh"))
        ] if bodies else [],
        **body_facet_info,
        "face_type": str(type(bodies[0].GetFaces()[0]))
        if bodies and bodies[0].GetFaces() else None,
        "face_methods": [
            name for name in dir(bodies[0].GetFaces()[0])
            if any(word in name.lower()
                   for word in ("facet", "face", "vertex", "mesh"))
        ] if bodies and bodies[0].GetFaces() else [],
    }))
    report.write("\n")
    report.write(copy_path)
print(folder)
