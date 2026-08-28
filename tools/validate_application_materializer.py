#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Any, Callable


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from taey_apply.application_materializer import (  # noqa: E402
    ApplicationMaterializationError,
    materialize_application_context,
)
from taey_apply.application_preparer import prepare_application  # noqa: E402
from taey_apply.contract import canonical_json_bytes  # noqa: E402


PRIVATE_SENTINEL = "synthetic-private-materialization-value"


def digest(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def write_frozen_bytes(path: Path, raw: bytes) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(raw)
    path.chmod(0o400)
    return digest(raw)


def write_frozen_json(path: Path, value: dict[str, Any]) -> str:
    return write_frozen_bytes(path, canonical_json_bytes(value))


def fixture(
    root: Path,
    seat: str,
    mutate: Callable[[dict[str, Any], dict[str, dict[str, Any]]], None] | None = None,
) -> tuple[Path, str, str]:
    identity = digest(f"application:{seat}")
    sources = root / "sources" / seat
    source_documents: dict[str, dict[str, Any]] = {
        "facts": {
            "full_name": {
                "value": PRIVATE_SENTINEL,
                "evidence_sha256": digest(f"{seat}:full-name"),
            },
            "email": {
                "value": f"{seat}@example.invalid",
                "evidence_sha256": digest(f"{seat}:email"),
            },
            "work_authorization": {
                "value": True,
                "evidence_sha256": digest(f"{seat}:authorization"),
            },
        },
        "work": {
            "automation": {
                "statement": PRIVATE_SENTINEL,
                "evidence_sha256": digest(f"{seat}:work"),
            }
        },
        "policy": {
            "submission_authorized": True,
            "directives": {
                "unknown_answer": {
                    "value": "halt_without_guessing",
                    "evidence_sha256": digest(f"{seat}:policy"),
                }
            },
        },
    }
    stage_specs = {
        "discovery": ("discovered", "job_record", "application/json"),
        "qualification": ("qualified", "qualification_receipt", "application/json"),
        "deep_research": ("deep_research_complete", "research_report", "text/markdown"),
        "materials": ("materials_ready", "resume", "application/pdf"),
    }
    stages: dict[str, dict[str, Any]] = {}
    for stage, (state, kind, media_type) in stage_specs.items():
        suffix = "pdf" if kind == "resume" else "txt"
        reference = f"sources/{seat}/{stage}.{suffix}"
        raw = f"{seat}:{stage}:synthetic private artifact".encode("utf-8")
        stages[stage] = {
            "state": state,
            "artifacts": [
                {
                    "kind": kind,
                    "ref": reference,
                    "sha256": write_frozen_bytes(root / reference, raw),
                    "media_type": media_type,
                }
            ],
        }

    manifest: dict[str, Any] = {
        "schema": "taey_apply_application_materialization_manifest_v1",
        "operation": "materialize_autonomous_application",
        "provider": "greenhouse",
        "application_identity_sha256": identity,
        "maximum_one_action_calls": 64,
        "required_fact_keys": ["full_name", "email", "work_authorization"],
        "stages": stages,
        "applicant_facts": source_documents["facts"],
        "work_evidence": source_documents["work"],
        "submission_policy": source_documents["policy"],
    }
    if mutate is not None:
        mutate(manifest, source_documents)
    manifest_path = root / "manifests" / f"{seat}.json"
    manifest_sha256 = write_frozen_json(manifest_path, manifest)
    return manifest_path, manifest_sha256, identity


def success_case(root: Path) -> None:
    seat = "success"
    manifest_path, manifest_sha256, identity = fixture(root, seat)
    result = materialize_application_context(
        private_root_value=root,
        manifest_path_value=manifest_path,
        expected_manifest_sha256=manifest_sha256,
        seat_id_value=seat,
        correlation_id_value=seat,
    )
    parent = root / "application-materializations" / seat
    lifecycle_path = parent / f"{seat}.lifecycle.json"
    context_path = parent / f"{seat}.context.json"
    context_raw = context_path.read_bytes()
    context = json.loads(context_raw)
    gates = sorted(parent.glob(f"{seat}.*.gate.json"))
    public_result = canonical_json_bytes(result)
    if (
        result["state"] != "materialized"
        or result["application_identity_sha256"] != identity
        or result["evidence_gate_count"] != 6
        or result["fact_count"] != 3
        or result["work_evidence_count"] != 1
        or len(gates) != 6
        or context["schema"] != "taey_apply_application_context_v1"
        or context["truth_attestation_sha256"] != result["truth_attestation_sha256"]
        or PRIVATE_SENTINEL.encode() not in context_raw
        or PRIVATE_SENTINEL.encode() in public_result
        or any(stat.S_IMODE(path.stat().st_mode) != 0o400 for path in [*gates, lifecycle_path, context_path])
        or stat.S_IMODE(parent.stat().st_mode) != 0o700
    ):
        raise RuntimeError("successful materialization did not prove its exact boundary")
    prepared = prepare_application(
        private_root_value=root,
        lifecycle_path_value=lifecycle_path,
        expected_lifecycle_sha256=str(result["lifecycle_sha256"]),
        seat_id_value=seat,
        correlation_id_value=seat,
    )
    if prepared["state"] != "prepared_unclaimed" or prepared["evidence_gate_count"] != 6:
        raise RuntimeError("materialized lifecycle was not ready for application_prepare_cli")
    try:
        materialize_application_context(
            private_root_value=root,
            manifest_path_value=manifest_path,
            expected_manifest_sha256=manifest_sha256,
            seat_id_value=seat,
            correlation_id_value=seat,
        )
    except ApplicationMaterializationError as exc:
        if exc.failure_code != "identity_spent":
            raise RuntimeError("materialization replay changed failure code") from exc
    else:
        raise RuntimeError("materialization identity replayed")


def refusal_case(
    root: Path,
    seat: str,
    expected_code: str,
    mutate: Callable[[dict[str, Any], dict[str, dict[str, Any]]], None],
    after_fixture: Callable[[Path, dict[str, Any]], None] | None = None,
) -> None:
    manifest_path, manifest_sha256, _identity = fixture(root, seat, mutate)
    manifest = json.loads(manifest_path.read_bytes())
    if after_fixture is not None:
        after_fixture(root, manifest)
    try:
        materialize_application_context(
            private_root_value=root,
            manifest_path_value=manifest_path,
            expected_manifest_sha256=manifest_sha256,
            seat_id_value=seat,
            correlation_id_value=seat,
        )
    except ApplicationMaterializationError as exc:
        if exc.failure_code != expected_code:
            raise RuntimeError(
                f"{seat} returned {exc.failure_code}, expected {expected_code}"
            ) from exc
    else:
        raise RuntimeError(f"{seat} materialization did not refuse")
    refusal_path = (
        root
        / "application-materializations"
        / seat
        / f"{seat}.refusal.json"
    )
    refusal_raw = refusal_path.read_bytes()
    refusal = json.loads(refusal_raw)
    if (
        refusal["failure_code"] != expected_code
        or refusal["state"] != "materialization_refused"
        or stat.S_IMODE(refusal_path.stat().st_mode) != 0o400
        or PRIVATE_SENTINEL.encode() in refusal_raw
    ):
        raise RuntimeError(f"{seat} refusal leaked or changed state")


def cli_case(root: Path) -> None:
    seat = "cli"
    manifest_path, manifest_sha256, _identity = fixture(root, seat)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "taey_apply.application_materialize_cli",
            "--private-root",
            str(root),
            "--manifest-file",
            str(manifest_path),
            "--manifest-sha256",
            manifest_sha256,
            "--seat-id",
            seat,
            "--correlation-id",
            seat,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    result = json.loads(completed.stdout)
    if (
        completed.stderr
        or result["state"] != "materialized"
        or PRIVATE_SENTINEL in completed.stdout
    ):
        raise RuntimeError("materializer CLI leaked private data or changed state")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="taey-apply-materializer-") as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        success_case(root)
        refusal_case(
            root,
            "missing-fact",
            "missing_truthful_applicant_data",
            lambda manifest, _docs: manifest["required_fact_keys"].append("phone"),
        )
        refusal_case(
            root,
            "empty-fact",
            "missing_truthful_applicant_data",
            lambda _manifest, docs: docs["facts"]["full_name"].update({"value": ""}),
        )
        refusal_case(
            root,
            "no-work-evidence",
            "missing_truthful_applicant_data",
            lambda _manifest, docs: docs["work"].clear(),
        )
        refusal_case(
            root,
            "authority-absent",
            "policy_or_authority_boundary",
            lambda _manifest, docs: docs["policy"].update(
                {"submission_authorized": False}
            ),
        )
        refusal_case(
            root,
            "review-field",
            "application_materialization_invalid",
            lambda manifest, _docs: manifest.update({"human_review_required": True}),
        )
        refusal_case(
            root,
            "missing-resume",
            "missing_truthful_applicant_data",
            lambda manifest, _docs: manifest["stages"]["materials"]["artifacts"][0].update(
                {"kind": "cover"}
            ),
        )
        refusal_case(
            root,
            "source-digest",
            "application_materialization_invalid",
            lambda manifest, _docs: manifest["stages"]["discovery"]["artifacts"][0].update(
                {"sha256": digest("wrong-source")}
            ),
        )
        refusal_case(
            root,
            "mutable-source",
            "application_materialization_invalid",
            lambda _manifest, _docs: None,
            lambda fixture_root, manifest: (
                fixture_root
                / manifest["stages"]["qualification"]["artifacts"][0]["ref"]
            ).chmod(0o600),
        )
        cli_case(root)
    result = {
        "schema": "taey_apply_application_materializer_validation_v1",
        "verdict": "PASS",
        "production_mutations": 0,
        "fixture_cases": 10,
        "validated": [
            "six immutable prerequisite receipts",
            "opaque canonical application context",
            "application_prepare_cli ready lifecycle",
            "missing truthful applicant data halt",
            "submission authority halt",
            "no human review field",
            "exact source digest and mode",
            "single-use materialization identity",
            "private value omission from stdout and refusals",
            "no UI database or submission access",
        ],
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
