"""Generate SHA-256-bound NX feature-to-JT identity manifests.

Run ``python -m converter.feature_jt_identity <part.prt> --feature-id
EXTRUDE(1)`` on a workstation with NXOpen. The tool toggles each selected
feature in isolated NX copies, maps the changed suppression-state record,
facets the active feature body, and requires a complete unique triangle match
to one JT piece. It never edits the source part.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from converter.feature_identity import (
    _state_payload_records,
    feature_identity_path,
    feature_identity_registry_path,
    match_feature_bodies_to_jt,
)


JOURNAL = Path(__file__).with_name("nx_feature_jt_identity_journal.py")
DEFAULT_NX = Path(
    r"C:\Program Files\Siemens\NX2412\NXBIN\run_journal.exe")


def _run_variant(nx_exe, source, feature_id, desired, workspace, label):
    variant_dir = workspace / label
    variant_dir.mkdir(parents=True)
    report_path = variant_dir / "report.json"
    command = [
        str(nx_exe), str(JOURNAL), "-args", str(source), feature_id,
        str(desired).lower(), str(variant_dir), str(report_path),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            "NX journal failed for %s (%s): %s" %
            (feature_id, label, error)) from error
    if result.returncode != 0 or not report_path.is_file():
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(
            "NX journal did not produce %s state for %s: %s" %
            (label, feature_id, details[-4000:]))
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise RuntimeError(
            "Invalid NX report for %s (%s)" % (feature_id, label)) from error
    if (report.get("journalId") != feature_id
            or report.get("suppressed") is not desired):
        raise RuntimeError(
            "NX report did not confirm requested feature state: %s"
            % feature_id)
    return report


def _feature_state_index(source_records, active_records, suppressed_records,
                         source_suppressed, feature_id):
    if not (len(source_records) == len(active_records)
            == len(suppressed_records)):
        raise ValueError(
            "EXTRUDE state-record layout changed while toggling %s"
            % feature_id)
    expected_source = (
        "suppressed" if source_suppressed else "unsuppressed")
    candidates = [
        index for index, (source, active, suppressed) in enumerate(zip(
            source_records, active_records, suppressed_records))
        if source.suppression == expected_source
        and active.suppression == "unsuppressed"
        and suppressed.suppression == "suppressed"
    ]
    if len(candidates) != 1:
        raise ValueError(
            "Suppression record for %s matched %d records; expected one"
            % (feature_id, len(candidates)))
    return candidates[0]


def _build_feature_entry(source, feature_id, nx_exe, workspace):
    source_records = _state_payload_records(source)
    active = _run_variant(
        nx_exe, source, feature_id, False, workspace, "unsuppressed")
    suppressed = _run_variant(
        nx_exe, source, feature_id, True, workspace, "suppressed")
    if active.get("sourceSuppressed") != suppressed.get("sourceSuppressed"):
        raise ValueError("NXOpen source-state reports disagree")
    source_suppressed = active["sourceSuppressed"]
    if (active.get("name") != suppressed.get("name")
            or active.get("bodyCount", 0) <= 0
            or suppressed.get("bodyCount") != 0):
        raise ValueError(
            "NXOpen did not produce one consistent active feature body")

    active_records = _state_payload_records(active["variantPath"])
    suppressed_records = _state_payload_records(suppressed["variantPath"])
    index = _feature_state_index(
        source_records, active_records, suppressed_records,
        source_suppressed, feature_id)
    source_record = source_records[index]
    pieces = match_feature_bodies_to_jt(source, active)
    return {
        "journalId": feature_id,
        "name": active.get("name") or "",
        "sourceSuppressed": source_suppressed,
        "stateRecordIndex": index,
        "sourceStateCode": source_record.state_code,
        "pieces": pieces,
    }


def build_feature_identity_manifest(part_path, feature_ids, nx_exe=DEFAULT_NX):
    """Create a manifest only after state and exact triangle joins succeed."""
    source = Path(part_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not Path(nx_exe).is_file():
        raise FileNotFoundError("NX run_journal.exe not found: %s" % nx_exe)
    if not JOURNAL.is_file():
        raise FileNotFoundError("NX identity journal not found: %s" % JOURNAL)
    if not feature_ids or len(set(feature_ids)) != len(feature_ids):
        raise ValueError("Provide unique NX feature JournalIdentifiers")

    with tempfile.TemporaryDirectory(
            prefix="nx_feature_jt_identity_") as temporary:
        workspace = Path(temporary)
        features = [
            _build_feature_entry(
                source, feature_id, Path(nx_exe),
                workspace / ("feature-%d" % index))
            for index, feature_id in enumerate(feature_ids)
        ]

    manifest = {
        "version": 1,
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "features": features,
    }
    from converter.feature_identity import _valid_manifest
    if not _valid_manifest(manifest):
        raise ValueError(
            "Generated feature identities are duplicate or malformed")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("part", type=Path)
    parser.add_argument(
        "--feature-id", action="append", required=True,
        help="NX feature JournalIdentifier; may be repeated")
    parser.add_argument("--nx-exe", type=Path, default=DEFAULT_NX)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    output = args.output or feature_identity_path(args.part)
    try:
        manifest = build_feature_identity_manifest(
            args.part, args.feature_id, args.nx_exe)
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(output.name + ".tmp")
            temporary.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            temporary.replace(output)
        except PermissionError:
            if args.output is not None:
                raise
            output = feature_identity_registry_path(
                manifest["sourceSha256"])
            output.parent.mkdir(parents=True, exist_ok=True)
            temporary = output.with_name(output.name + ".tmp")
            temporary.write_text(
                json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            temporary.replace(output)
            print(
                "Part folder is read-only; using content-addressed manifest "
                "%s" % output, file=sys.stderr)
    except (OSError, ValueError, RuntimeError) as error:
        print("feature identity failed: %s" % error, file=sys.stderr)
        return 2
    print("Wrote %s" % output)
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
