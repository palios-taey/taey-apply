#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile

import validate_contract


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from taey_apply.classification_contract import (  # noqa: E402
    CLAIM_SCHEMA,
    OPERATION,
)
from taey_apply.classification_preparer import (  # noqa: E402
    MANIFEST_OPERATION,
    MANIFEST_SCHEMA,
    POLICY_INPUT_SCHEMA,
    POLICY_SCHEMA,
    PRIORITY_BOARDS_SCHEMA,
    REFUSAL_SCHEMA,
)
from taey_apply.linkedin_classification import (  # noqa: E402
    _digestable_sqlite_value,
    row_sha256,
    stable_row_sha256,
)


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def digest(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def private_bytes(path: Path, raw_bytes: bytes) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(raw_bytes)
    path.chmod(0o400)
    return digest(raw_bytes)


def private_json(path: Path, value: object) -> str:
    return private_bytes(path, canonical(value))


def database_state(database: Path) -> tuple[str, tuple[int, int, int]]:
    raw_digest = digest(database.read_bytes())
    connection = sqlite3.connect(database)
    counts = tuple(
        int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in ("jobs", "applications", "apply_runs")
    )
    connection.close()
    return raw_digest, (counts[0], counts[1], counts[2])


def database_row(database: Path) -> tuple[list[str], tuple[object, ...]]:
    connection = sqlite3.connect(database)
    table_info = sorted(
        connection.execute("PRAGMA table_info(jobs)").fetchall(),
        key=lambda item: int(item[0]),
    )
    columns = [str(item[1]) for item in table_info]
    projection = ",".join('"' + column.replace('"', '""') + '"' for column in columns)
    rows = connection.execute(f"SELECT {projection} FROM jobs").fetchall()
    connection.close()
    if len(rows) != 1:
        raise RuntimeError("generated row identity differs")
    return columns, tuple(rows[0])


def environment() -> dict[str, str]:
    value = dict(os.environ)
    value["PYTHONPATH"] = str(SRC)
    return value


def command(
    private_root: Path,
    database: Path,
    manifest_path: Path,
    manifest_sha256: str,
) -> list[str]:
    return [
        sys.executable,
        "-P",
        "-m",
        "taey_apply.classification_prepare_cli",
        "--private-root",
        str(private_root),
        "--database",
        str(database),
        "--manifest-file",
        str(manifest_path),
        "--expected-manifest-sha256",
        manifest_sha256,
    ]


def setup_case(
    base: Path,
    name: str,
    classifier_verdict: str,
    *,
    track_invocation: bool = False,
) -> tuple[Path, Path, Path, str, dict[str, object]]:
    private_root = base / name / "private"
    database_root = base / name / "database"
    private_root.mkdir(mode=0o700, parents=True)
    database_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    database_root.chmod(0o700)
    database = database_root / "jobs.db"
    validate_contract.create_database(database)
    transaction_sha256, _card_digest = validate_contract.build_private_inputs(
        private_root
    )
    intake_result = validate_contract.invoke(
        private_root,
        database,
        transaction_sha256,
        "intake.json",
        f"{name}-intake",
    )
    receipt_path = private_root / "receipts" / "seat" / "intake.json"
    if digest(receipt_path.read_bytes()) != intake_result["receipt_sha256"]:
        raise RuntimeError("generated intake receipt differs")
    (private_root / "classification-attempts").mkdir(mode=0o700)
    identity_root = private_root / "classification-preparation" / name
    identity_root.mkdir(mode=0o700, parents=True)
    identity_root.parent.chmod(0o700)
    identity_root.chmod(0o700)
    policy = {"schema": POLICY_SCHEMA, "filter_rev": 12}
    priority_boards = [
        ["greenhouse", "example", "Example"],
        ["ashby", "another", "Another"],
    ]
    priority_artifact = {
        "schema": PRIORITY_BOARDS_SCHEMA,
        "priority_boards": priority_boards,
    }
    invocation_marker = identity_root / "classifier-invoked"
    invocation_line = (
        "    from pathlib import Path\n"
        f"    Path({str(invocation_marker)!r}).write_bytes(b'1')\n"
        if track_invocation
        else ""
    )
    classifier_source = (
        "FILTER_REV = 12\n"
        "_invocations = 0\n"
        "def classify(job):\n"
        "    global _invocations\n"
        "    _invocations += 1\n"
        "    if _invocations != 1:\n"
        "        raise RuntimeError('classifier invoked more than once')\n"
        + invocation_line
        + f"    return ({classifier_verdict!r}, 'private reason', 'private detail')\n"
    ).encode("utf-8")
    policy_path = identity_root / "policy.json"
    priority_path = identity_root / "priority-boards.json"
    classifier_path = identity_root / "classifier.py"
    policy_sha256 = private_json(policy_path, policy)
    priority_artifact_sha256 = private_json(priority_path, priority_artifact)
    classifier_sha256 = private_bytes(classifier_path, classifier_source)
    claim_parent = private_root / "classification" / name
    refusal_parent = private_root / "classification-preparation-refusals" / name
    claim_parent.mkdir(mode=0o700, parents=True)
    refusal_parent.mkdir(mode=0o700, parents=True)
    claim_parent.parent.chmod(0o700)
    refusal_parent.parent.chmod(0o700)
    claim_parent.chmod(0o700)
    refusal_parent.chmod(0o700)
    claim_ref = f"classification/{name}/claim.json"
    refusal_ref = f"classification-preparation-refusals/{name}/refusal.json"
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "operation": MANIFEST_OPERATION,
        "intake_transaction_ref": "transactions/seat/intake.json",
        "intake_transaction_sha256": transaction_sha256,
        "intake_receipt_ref": "receipts/seat/intake.json",
        "intake_receipt_sha256": digest(receipt_path.read_bytes()),
        "policy_artifact_ref": policy_path.relative_to(private_root).as_posix(),
        "policy_artifact_sha256": policy_sha256,
        "classifier_ref": classifier_path.relative_to(private_root).as_posix(),
        "classifier_sha256": classifier_sha256,
        "priority_boards_artifact_ref": priority_path.relative_to(
            private_root
        ).as_posix(),
        "priority_boards_artifact_sha256": priority_artifact_sha256,
        "claim_ref": claim_ref,
        "refusal_ref": refusal_ref,
    }
    manifest_path = identity_root / "manifest.json"
    manifest_sha256 = private_json(manifest_path, manifest)
    expected = {
        "classifier_sha256": classifier_sha256,
        "policy_artifact_sha256": policy_sha256,
        "priority_boards_artifact_sha256": priority_artifact_sha256,
        "priority_boards_sha256": digest(canonical(priority_boards)),
        "intake_transaction_sha256": transaction_sha256,
        "intake_receipt_sha256": digest(receipt_path.read_bytes()),
        "claim_path": private_root / claim_ref,
        "refusal_path": private_root / refusal_ref,
        "filter_rev": 12,
        "invocation_marker": invocation_marker,
    }
    return private_root, database, manifest_path, manifest_sha256, expected


def run_success_case(
    base: Path, name: str, classifier_verdict: str
) -> dict[str, object]:
    private_root, database, manifest_path, manifest_sha256, expected = setup_case(
        base, name, classifier_verdict
    )
    before = database_state(database)
    completed = subprocess.run(
        command(private_root, database, manifest_path, manifest_sha256),
        check=True,
        capture_output=True,
        text=True,
        env=environment(),
    )
    if completed.stderr:
        raise RuntimeError("classification preparer wrote unexpected stderr")
    result = json.loads(completed.stdout)
    claim_path = expected["claim_path"]
    refusal_path = expected["refusal_path"]
    if not isinstance(claim_path, Path) or not isinstance(refusal_path, Path):
        raise RuntimeError("generated identity path type differs")
    claim_bytes = claim_path.read_bytes()
    claim = json.loads(claim_bytes.decode("utf-8"))
    columns, row = database_row(database)
    job = dict(zip(columns, row, strict=True))
    priority_boards_sha256 = str(expected["priority_boards_sha256"])
    policy_input_sha256 = digest(
        canonical(
            {
                "schema": POLICY_INPUT_SCHEMA,
                "classifier_sha256": expected["classifier_sha256"],
                "filter_rev": expected["filter_rev"],
                "job": {
                    key: _digestable_sqlite_value(value)
                    for key, value in job.items()
                },
                "priority_boards_sha256": priority_boards_sha256,
            }
        )
    )
    expected_digests = {
        "manifest_sha256": manifest_sha256,
        "claim_sha256": digest(claim_bytes),
        "intake_transaction_sha256": expected["intake_transaction_sha256"],
        "intake_receipt_sha256": expected["intake_receipt_sha256"],
        "policy_artifact_sha256": expected["policy_artifact_sha256"],
        "classifier_sha256": expected["classifier_sha256"],
        "priority_boards_artifact_sha256": expected[
            "priority_boards_artifact_sha256"
        ],
        "priority_boards_sha256": priority_boards_sha256,
        "policy_input_sha256": policy_input_sha256,
        "prewrite_row_sha256": row_sha256(columns, row),
        "stable_row_sha256": stable_row_sha256(columns, row),
    }
    if set(result["digests"]) != set(expected_digests) | {
        "database_snapshot_sha256"
    }:
        raise RuntimeError("preparer digest-only projection differs")
    for key, value in expected_digests.items():
        if result["digests"].get(key) != value:
            raise RuntimeError(f"preparer digest differs: {key}")
    public_projection = completed.stdout
    forbidden = {
        classifier_verdict,
        "private reason",
        "private detail",
        "https://www.linkedin.com/jobs/view/1234567890/",
        str(claim_path),
        str(database),
    }
    claim_stat = os.lstat(claim_path)
    attempt_path = private_root / "classification-attempts" / f"{digest(claim_bytes)}.json"
    if (
        result["state"] != "claim_prepared"
        or result["ok"] is not True
        or set(claim) != {
            "schema",
            "operation",
            "intake_transaction_ref",
            "intake_transaction_sha256",
            "intake_receipt_ref",
            "intake_receipt_sha256",
            "prewrite_row_sha256",
            "stable_row_sha256",
            "policy_input_sha256",
            "classifier_sha256",
            "verdict",
        }
        or claim["schema"] != CLAIM_SCHEMA
        or claim["operation"] != OPERATION
        or claim["policy_input_sha256"] != policy_input_sha256
        or canonical(claim) != claim_bytes
        or not stat.S_ISREG(claim_stat.st_mode)
        or stat.S_IMODE(claim_stat.st_mode) != 0o400
        or claim_stat.st_uid != os.geteuid()
        or refusal_path.exists()
        or attempt_path.exists()
        or database_state(database) != before
        or any(value in public_projection for value in forbidden)
    ):
        raise RuntimeError("classification claim preparation postcondition differs")
    replay = subprocess.run(
        command(private_root, database, manifest_path, manifest_sha256),
        capture_output=True,
        text=True,
        env=environment(),
    )
    if (
        replay.returncode != 2
        or replay.stdout
        or replay.stderr
        != "ClassificationPreparationError[IDENTITY_SPENT]: preparation stopped\n"
        or database_state(database) != before
        or refusal_path.exists()
    ):
        raise RuntimeError("successful preparation identity was replayable")
    return {
        "terminal_verdict": classifier_verdict,
        "claim_canonical_0400": True,
        "database_read_only": True,
        "existing_attempt_marker_absent": True,
        "replay_refused": True,
        "stdout_digest_only": True,
    }


def run_refusal_case(base: Path) -> dict[str, object]:
    private_root, database, manifest_path, manifest_sha256, expected = setup_case(
        base, "refusal", "MAYBE"
    )
    before = database_state(database)
    completed = subprocess.run(
        command(private_root, database, manifest_path, manifest_sha256),
        capture_output=True,
        text=True,
        env=environment(),
    )
    refusal_path = expected["refusal_path"]
    claim_path = expected["claim_path"]
    if not isinstance(refusal_path, Path) or not isinstance(claim_path, Path):
        raise RuntimeError("generated identity path type differs")
    refusal_bytes = refusal_path.read_bytes()
    refusal = json.loads(refusal_bytes.decode("utf-8"))
    refusal_stat = os.lstat(refusal_path)
    if (
        completed.returncode != 2
        or completed.stdout
        or completed.stderr
        != "ClassificationPreparationError[PRIVATE_CLASSIFIER_INVALID]: preparation stopped\n"
        or claim_path.exists()
        or refusal["schema"] != REFUSAL_SCHEMA
        or refusal["state"] != "preparation_refused"
        or refusal["failure_code"] != "PRIVATE_CLASSIFIER_INVALID"
        or refusal["manifest_sha256"] != manifest_sha256
        or canonical(refusal) != refusal_bytes
        or not stat.S_ISREG(refusal_stat.st_mode)
        or stat.S_IMODE(refusal_stat.st_mode) != 0o400
        or refusal_stat.st_uid != os.geteuid()
        or database_state(database) != before
    ):
        raise RuntimeError("terminal preparation refusal differs")
    replay = subprocess.run(
        command(private_root, database, manifest_path, manifest_sha256),
        capture_output=True,
        text=True,
        env=environment(),
    )
    if (
        replay.returncode != 2
        or replay.stdout
        or replay.stderr
        != "ClassificationPreparationError[IDENTITY_SPENT]: preparation stopped\n"
        or database_state(database) != before
    ):
        raise RuntimeError("refused preparation identity was replayable")
    return {
        "claim_absent": True,
        "database_read_only": True,
        "refusal_canonical_0400": True,
        "replay_refused": True,
    }


def run_spent_identity_case(base: Path, existing_outcome: str) -> dict[str, object]:
    private_root, database, manifest_path, manifest_sha256, expected = setup_case(
        base,
        f"spent-{existing_outcome}",
        "PASS",
        track_invocation=True,
    )
    claim_path = expected["claim_path"]
    refusal_path = expected["refusal_path"]
    invocation_marker = expected["invocation_marker"]
    if (
        not isinstance(claim_path, Path)
        or not isinstance(refusal_path, Path)
        or not isinstance(invocation_marker, Path)
    ):
        raise RuntimeError("generated spent identity path type differs")
    if existing_outcome == "claim":
        existing_path = claim_path
        counterpart = refusal_path
        existing_value = {
            "schema": CLAIM_SCHEMA,
            "operation": OPERATION,
            "intake_transaction_ref": "transactions/seat/intake.json",
            "intake_transaction_sha256": "1" * 64,
            "intake_receipt_ref": "receipts/seat/intake.json",
            "intake_receipt_sha256": "2" * 64,
            "prewrite_row_sha256": "3" * 64,
            "stable_row_sha256": "4" * 64,
            "policy_input_sha256": "5" * 64,
            "classifier_sha256": "6" * 64,
            "verdict": "PASS",
        }
    elif existing_outcome == "refusal":
        existing_path = refusal_path
        counterpart = claim_path
        existing_value = {
            "schema": REFUSAL_SCHEMA,
            "operation": MANIFEST_OPERATION,
            "ok": False,
            "state": "preparation_refused",
            "failure_code": "PREPARATION_REFUSED",
            "manifest_sha256": manifest_sha256,
            "claim_identity_sha256": "7" * 64,
        }
    else:
        raise RuntimeError("unknown spent identity fixture")
    private_json(existing_path, existing_value)
    existing_bytes = existing_path.read_bytes()
    before = database_state(database)
    completed = subprocess.run(
        command(private_root, database, manifest_path, manifest_sha256),
        capture_output=True,
        text=True,
        env=environment(),
    )
    if (
        completed.returncode != 2
        or completed.stdout
        or completed.stderr
        != "ClassificationPreparationError[IDENTITY_SPENT]: preparation stopped\n"
        or existing_path.read_bytes() != existing_bytes
        or counterpart.exists()
        or invocation_marker.exists()
        or database_state(database) != before
    ):
        raise RuntimeError(
            f"existing {existing_outcome} did not stop before classifier invocation"
        )
    return {
        "existing_outcome": existing_outcome,
        "outcome_bytes_unchanged": True,
        "classifier_invocations": 0,
        "database_read_only": True,
        "stdout_empty": True,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(
        prefix="taey-apply-classification-preparer-"
    ) as temporary:
        base = Path(temporary)
        success = [
            run_success_case(base, "success-pass", "PASS"),
            run_success_case(base, "success-killed", "KILLED"),
        ]
        refusal = run_refusal_case(base)
        spent_identities = [
            run_spent_identity_case(base, "claim"),
            run_spent_identity_case(base, "refusal"),
        ]
        print(
            json.dumps(
                {
                    "schema": "taey_apply_classification_preparer_gate_v1",
                    "success": success,
                    "refusal": refusal,
                    "spent_identities": spent_identities,
                    "verdict": "PASS",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
