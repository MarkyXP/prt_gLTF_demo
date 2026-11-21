import os
import shutil
import sys
import tempfile

import NXOpen

source = next(
    (argument for argument in reversed(sys.argv)
     if argument.lower().endswith(".prt") and os.path.isfile(argument)),
    None,
)
if source is None:
    raise RuntimeError("Pass source .prt and feature journal id after -args")

journal_id = next(
    (argument for argument in reversed(sys.argv)
     if argument.upper().startswith("EXTRUDE(")),
    None,
)
if journal_id is None:
    raise RuntimeError("Pass the feature journal id after -args")

folder = tempfile.mkdtemp(prefix="nx_state_probe_")
copy_path = os.path.join(folder, os.path.basename(source))
shutil.copy2(source, copy_path)
session = NXOpen.Session.GetSession()
part, _status = session.Parts.OpenBaseDisplay(copy_path)
feature = next(item for item in part.Features
               if item.JournalIdentifier == journal_id)

with open(os.path.join(folder, "state.txt"), "w", encoding="utf-8") as report:
    report.write("before=%s suppressed=%s\n" %
                 (feature.JournalIdentifier, feature.Suppressed))
    mark = session.SetUndoMark(
        NXOpen.Session.MarkVisibility.Invisible,
        "Suppress feature diagnostic",
    )
    feature.Suppress()
    session.UpdateManager.DoUpdate(mark)
    report.write("after suppressed=%s bodies=%s\n" % (
        feature.Suppressed,
        [body.JournalIdentifier for body in feature.GetBodies()],
    ))
    status = part.Save(
        NXOpen.BasePart.SaveComponents.FalseValue,
        NXOpen.BasePart.CloseAfterSave.FalseValue,
    )
    report.write("saved=%s path=%s\n" % (status, copy_path))
print(folder)
