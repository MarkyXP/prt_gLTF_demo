# -*- coding: utf-8 -*-
"""parasolid.py - parasolid-only .prt tier: extract transmit bodies, convert.

Two external dependencies, referenced through environment variables:
  PRT_PK_EXE      parasolid-kit executable (x_b -> STEP, schema aware)
  PRT_SCHEMA_DIR  directory carrying sch_*.sch_txt schema files
  PRT_OCP_PYTHON  a Python interpreter with the OCP (OCCT) bindings installed

If any of these is missing the parasolid fallback is unavailable and the
conversion surfaces a descriptive error instead of geometry.
"""
import json
import os
import struct
import subprocess
import sys
import tempfile
import zlib
import shutil

import ole
import prt_jt
import s19_route_a

OM_TYPES = {1, 3, 4, 6, 15}
PS_TYPES = {10, 13, 14, 16, 17, 18}
TJ_SELF = os.path.dirname(os.path.abspath(__file__))


def ug_part_stream(path):
    """-> the UG_PART/UG_PART stream bytes of a .prt, or None."""
    data = open(path, "rb").read()
    ug = None
    try:
        ug = read_stream_dict(data).get("UG_PART/UG_PART")
    except Exception:
        ug = None
    if ug is None:
        try:
            streams = prt_jt.open_prt(path)
            st = streams.get("UG_PART/UG_PART") or streams.get(
                "/Root/UG_PART/UG_PART")
            ug = st.data if st is not None else None
        except Exception:
            ug = None
    return ug


def build_xbs(path):
    """-> list of x_b transmit blobs (text header + PS arena) for a .prt."""
    ug = ug_part_stream(path)
    if ug is None:
        return []
    out = []
    for rec in s19_route_a.records(ug):
        if rec["type"] not in PS_TYPES:
            continue
        desc = rec["data"]
        if len(desc) < 0x30:
            continue
        base, mlen, flag, nested = s19_route_a.walk_desc(desc)
        if base <= 0 or mlen <= 0 or base + mlen > len(desc):
            continue
        d0 = struct.unpack_from("<I", desc, 0)[0]
        tstart = max(0, base - (d0 & 0x3FFFFFFF))
        text = desc[tstart:base]
        if b"**PARASOLID" not in text:
            text = b""            # binary-only transmit: no text header
        else:
            k = text.rfind(b"\n")
            if k >= 0:
                text = text[:k + 1]
        try:
            arena = zlib.decompress(desc[base:base + mlen]) if flag == 1 \
                else desc[base:base + mlen]
        except Exception:
            continue
        if arena[:2] != b"PS":
            continue
        out.append(text + arena)
    return out


def _env(name, default=None):
    return os.environ.get(name, default)


def _runtime():
    """-> (kit_exe, schema_dir, ocp_python) or a descriptive error string."""
    here = TJ_SELF
    project_root = os.path.dirname(here)
    project_python = os.path.join(project_root, ".venv", "Scripts",
                                   "python.exe")
    candidates = [
        _env("PRT_PK_EXE"),
        os.path.join(project_root, ".venv", "Scripts",
                     "parasolid-kit.exe"),
        shutil.which("parasolid-kit.exe"),
        r"C:\code\nx_reverse_engineering\temp\jt\.para\.venv\Scripts"
        r"\parasolid-kit.exe",
    ]
    kit = next((p for p in candidates if p and os.path.isfile(p)), None)
    schema = _env("PRT_SCHEMA_DIR", os.path.join(here, "schema"))
    ocp = _env("PRT_OCP_PYTHON")
    if not kit or not os.path.isfile(kit):
        return None, None, None, "PRT_PK_EXE not set/found (parasolid-kit executable)"
    if not os.path.isdir(schema):
        return None, None, None, "PRT_SCHEMA_DIR not set/found (%s)" % schema
    if ocp and not os.path.isfile(ocp):
        ocp = None
    if not ocp:
        candidates = [project_python,
                      r"C:\code\nx_reverse_engineering\temp\jt\.para\.venv"
                      r"\Scripts\python.exe"]
        ocp = next((p for p in candidates if os.path.isfile(p)), None)
        if ocp:
            return kit, schema, ocp, None
        if not ocp:
            return None, None, None, "PRT_OCP_PYTHON not set and no OCP python found"
    return kit, schema, ocp, None


def step_meshes(steps, ocp_python):
    """Run the OCP worker over finished STEP files -> mesh-collection list."""
    tess = os.path.join(TJ_SELF, "occ_tess.py")
    out = []
    for stp in steps:
        p = subprocess.run([ocp_python, tess, stp], capture_output=True,
                           text=True, timeout=1800)
        line = p.stdout.strip().splitlines()
        line = line[-1] if line else ""
        try:
            d = json.loads(line)
        except Exception:
            d = {"error": (p.stderr or p.stdout or "")[-200:]}
        if "error" in d and out:
            continue
        if "error" in d and not out:
            raise RuntimeError("OCCT tessellation failed: %s" % d["error"])
        if "error" in d:
            continue
        out.append({"stream": "step:" + os.path.basename(stp),
                    "t": {"pts": d["pts"], "tris": d["tris"]}})
    return out


def xbs_to_steps(xbs, tmpdir, kit, schema):
    """Export every x_b to a STEP file inside tmpdir -> list of paths."""
    steps = []
    errors = []
    for j, xb in enumerate(xbs):
        src = os.path.join(tmpdir, "b%d.x_b" % j)
        stp = os.path.join(tmpdir, "b%d.step" % j)
        with open(src, "wb") as h:
            h.write(xb)
        r = subprocess.run([kit, "export-step", src, stp,
                            "--schema-dir", schema,
                            "--source-unit", "mm", "--overwrite"],
                           capture_output=True, text=True, timeout=1800)
        if os.path.exists(stp) and os.path.getsize(stp) >= 200:
            steps.append(stp)
        else:
            detail = (r.stderr or r.stdout or "").strip()
            if detail:
                try:
                    report = json.loads(detail)
                    diagnostics = report.get("report", {}).get("diagnostics", [])
                    messages = [d.get("message") for d in diagnostics
                                if d.get("message")]
                    if messages:
                        detail = "; ".join(dict.fromkeys(messages))
                except (TypeError, ValueError):
                    pass
                errors.append("body %d: %s" % (j, detail[-2000:]))
    if not steps and errors:
        raise RuntimeError("parasolid-kit STEP export failed:\n%s"
                           % "\n".join(errors))
    return steps


def convert(path, tmpdir=None):
    """Parasolid fallback: .prt -> mesh-collection list.

    Raises RuntimeError with a descriptive message when no geometry or the
    external toolchain is unavailable.
    """
    xbs = build_xbs(path)
    if not xbs:
        raise RuntimeError("no parasolid transmits in this .prt file")
    kit, schema, ocp, err = _runtime()
    if err:
        raise RuntimeError("parasolid tier unavailable: %s" % err)
    td = tmpdir or tempfile.mkdtemp(prefix="prt_parasolid_")
    steps = xbs_to_steps(xbs, td, kit, schema)
    if not steps:
        raise RuntimeError(
            "parasolid-kit produced a usable STEP for 0 of %d bodies "
            "(known OCCT interop gap)" % len(xbs))
    meshes = step_meshes(steps, ocp)
    if not meshes:
        raise RuntimeError("OCCT tessellation produced no triangles")
    return meshes
