"""Inspect NXOpen linked-body feature and body state without editing a part.

Inputs:
  * A source .prt path passed to ``run_journal.exe -args``.

Outputs:
  * A JSON report in a new temporary directory containing feature/body state,
    layers, blanking, linked-body relationships, and assembly components.
  * The journal opens only a temporary copy and does not save it.
"""

import json
import os
import shutil
import sys
import tempfile

import NXOpen
import NXOpen.UF


def _arguments():
    try:
        args = sys.argv[sys.argv.index("-args") + 1:]
    except ValueError:
        args = sys.argv[1:]
    if len(args) != 1 or not os.path.isfile(args[0]):
        raise RuntimeError("Pass exactly one existing source .prt path")
    return args[0]


def _value(obj, name):
    try:
        value = getattr(obj, name)
        if callable(value):
            return "<callable>"
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        return str(value)
    except Exception as error:
        return "<%s: %s>" % (type(error).__name__, error)


def _body_info(body):
    names = (
        "Name", "JournalIdentifier", "Tag", "Layer", "IsBlanked",
        "IsOccurrence", "IsSolidBody", "IsSheetBody", "OwningComponent",
        "IsVisible",
    )
    info = {name: _value(body, name) for name in names}
    for method_name in ("GetFeatures",):
        try:
            values = getattr(body, method_name)()
            info[method_name] = [
                {
                    "type": type(value).__name__,
                    "name": _value(value, "Name"),
                    "journalIdentifier": _value(value, "JournalIdentifier"),
                    "tag": _value(value, "Tag"),
                }
                for value in values
            ]
        except Exception as error:
            info[method_name] = "<%s: %s>" % (
                type(error).__name__, error)
    try:
        faces = body.GetFaces()
        info["faceCount"] = len(faces)
        info["firstFaces"] = [
            {
                "journalIdentifier": _value(face, "JournalIdentifier"),
                "tag": _value(face, "Tag"),
                "layer": _value(face, "Layer"),
                "isBlanked": _value(face, "IsBlanked"),
            }
            for face in faces[:3]
        ]
    except Exception as error:
        info["faces"] = "<%s: %s>" % (type(error).__name__, error)
    return info


def _feature_info(feature, index):
    names = (
        "Name", "JournalIdentifier", "Tag", "Suppressed", "Layer",
        "IsBlanked", "IsVisible", "FeatureType",
    )
    info = {
        "index": index,
        "type": type(feature).__name__,
        "values": {name: _value(feature, name) for name in names},
    }
    try:
        bodies = feature.GetBodies()
        info["bodies"] = [_body_info(body) for body in bodies]
        info["bodyCount"] = len(bodies)
    except Exception as error:
        info["bodies"] = "<%s: %s>" % (type(error).__name__, error)
    return info


source = _arguments()
folder = tempfile.mkdtemp(prefix="nx_linked_body_visibility_")
copy_path = os.path.join(folder, os.path.basename(source))
shutil.copy2(source, copy_path)

session = NXOpen.Session.GetSession()
part, _status = session.Parts.OpenBaseDisplay(copy_path)
features = list(part.Features)
bodies = list(part.Bodies)

components = []
try:
    root = part.ComponentAssembly.RootComponent
    pending = [root] if root is not None else []
    seen = set()
    while pending:
        component = pending.pop()
        tag = str(component.Tag)
        if tag in seen:
            continue
        seen.add(tag)
        info = {
            "name": _value(component, "Name"),
            "journalIdentifier": _value(component, "JournalIdentifier"),
            "tag": tag,
            "suppressed": _value(component, "Suppressed"),
            "isSuppressed": _value(component, "IsSuppressed"),
            "prototype": _value(component, "Prototype"),
        }
        components.append(info)
        try:
            pending.extend(component.GetChildren())
        except Exception as error:
            info["childrenError"] = repr(error)
except Exception as error:
    components = ["<%s: %s>" % (type(error).__name__, error)]

uf_objects = {}
try:
    obj_api = NXOpen.UF.UFSession.GetUFSession().Obj
    uf_objects = {
        name: getattr(obj_api, name).__doc__
        for name in dir(obj_api)
        if any(word in name.lower() for word in ("blank", "status", "layer"))
    }
except Exception as error:
    uf_objects = {"error": repr(error)}

report = {
    "source": source,
    "copyPath": copy_path,
    "partName": _value(part, "Name"),
    "partUnits": str(part.PartUnits),
    "featureCount": len(features),
    "features": [_feature_info(feature, index)
                 for index, feature in enumerate(features)],
    "partBodyCount": len(bodies),
    "partBodies": [_body_info(body) for body in bodies],
    "components": components,
    "ufObjectStateMethods": uf_objects,
}
report_path = os.path.join(folder, "report.json")
with open(report_path, "w", encoding="utf-8") as output:
    json.dump(report, output, indent=2)
print(report_path)
