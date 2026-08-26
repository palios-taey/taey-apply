#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
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
from taey_apply.linkedin_classification import (  # noqa: E402
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


def private_json(path: Path, value: object) -> str:
    raw_bytes = canonical(value)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    path.write_bytes(raw_bytes)
    path.chmod(0o400)
    return digest(raw_bytes)


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
        raise RuntimeError("generated database row identity is not exact")
    return columns, rows[0]


def create_claim(
    private_root: Path,
    database: Path,
    intake_transaction_sha256: str,
    intake_receipt_path: Path,
    verdict: str,
) -> tuple[Path, str]:
    columns, row = database_row(database)
    claim = {
        "schema": CLAIM_SCHEMA,
        "operation": OPERATION,
        "intake_transaction_ref": "transactions/seat/intake.json",
        "intake_transaction_sha256": intake_transaction_sha256,
        "intake_receipt_ref": intake_receipt_path.relative_to(private_root).as_posix(),
        "intake_receipt_sha256": digest(intake_receipt_path.read_bytes()),
        "prewrite_row_sha256": row_sha256(columns, row),
        "stable_row_sha256": stable_row_sha256(columns, row),
        "policy_input_sha256": "8" * 64,
        "classifier_sha256": "9" * 64,
        "verdict": verdict,
    }
    claim_path = private_root / "classification" / "seat" / "claim.json"
    return claim_path, private_json(claim_path, claim)


def command(
    private_root: Path,
    database: Path,
    claim_path: Path,
    claim_sha256: str,
    receipt_name: str,
    turn_id: str,
) -> tuple[list[str], Path]:
    receipt_path = private_root / "classification-receipts" / receipt_name
    receipt_path.parent.mkdir(mode=0o700, exist_ok=True)
    receipt_path.parent.chmod(0o700)
    return [
        sys.executable,
        "-P",
        "-m",
        "taey_apply.classification_cli",
        "--private-root",
        str(private_root),
        "--database",
        str(database),
        "--claim-file",
        str(claim_path),
        "--expected-claim-sha256",
        claim_sha256,
        "--receipt-file",
        str(receipt_path),
        "--requester",
        "example-seat",
        "--turn-id",
        turn_id,
        "--correlation-id",
        turn_id,
        "--process-generation",
        "7" * 32,
    ], receipt_path


def environment() -> dict[str, str]:
    value = dict(os.environ)
    value["PYTHONPATH"] = str(SRC)
    return value


def counts(database: Path) -> tuple[int, int, int]:
    connection = sqlite3.connect(database)
    value = tuple(
        int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("jobs", "applications", "apply_runs")
    )
    connection.close()
    return value


def setup_case(base: Path, name: str, verdict: str) -> tuple[Path, Path, Path, str]:
    private_root = base / name / "private"
    database_root = base / name / "database"
    private_root.mkdir(mode=0o700, parents=True)
    database_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    database_root.chmod(0o700)
    database = database_root / "jobs.db"
    validate_contract.create_database(database)
    intake_sha256, _card_digest = validate_contract.build_private_inputs(private_root)
    intake_result = validate_contract.invoke(
        private_root,
        database,
        intake_sha256,
        "intake.json",
        f"{name}-intake",
    )
    intake_receipt_path = private_root / "receipts" / "seat" / "intake.json"
    if digest(intake_receipt_path.read_bytes()) != intake_result["receipt_sha256"]:
        raise RuntimeError("generated intake receipt differs")
    attempts = private_root / "classification-attempts"
    attempts.mkdir(mode=0o700)
    attempts.chmod(0o700)
    claim_path, claim_sha256 = create_claim(
        private_root,
        database,
        intake_sha256,
        intake_receipt_path,
        verdict,
    )
    return private_root, database, claim_path, claim_sha256


def run_success_case(base: Path, verdict: str) -> dict[str, object]:
    private_root, database, claim_path, claim_sha256 = setup_case(
        base, verdict.lower(), verdict
    )
    before = counts(database)
    invocation, receipt_path = command(
        private_root,
        database,
        claim_path,
        claim_sha256,
        "classification.json",
        f"{verdict.lower()}-classification",
    )
    completed = subprocess.run(
        invocation,
        check=True,
        capture_output=True,
        text=True,
        env=environment(),
    )
    if completed.stderr:
        raise RuntimeError("classification connector wrote unexpected stderr")
    result = json.loads(completed.stdout)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    after = counts(database)
    connection = sqlite3.connect(database)
    row = connection.execute(
        "SELECT verdict,score,applied_at FROM jobs"
    ).fetchone()
    connection.close()
    forbidden = {
        verdict,
        "https://www.linkedin.com/jobs/view/1234567890/",
        "Example role",
        "Example company",
    }
    public_projection = completed.stdout + receipt_path.read_text(encoding="utf-8")
    if any(value in public_projection for value in forbidden):
        raise RuntimeError("classification projection contains a private value")
    if (
        result["state"] != "classified"
        or result["records_written"] != 1
        or result["terminal"] is not True
        or row != (verdict, None, None)
        or before != after
        or receipt["action"]["changed_columns"] != ["verdict"]
        or receipt["postcondition"]["applications_before"]
        != receipt["postcondition"]["applications_after"]
        or receipt["postcondition"]["apply_runs_before"]
        != receipt["postcondition"]["apply_runs_after"]
        or digest(receipt_path.read_bytes()) != result["receipt_sha256"]
    ):
        raise RuntimeError("classification postcondition differs")
    replay_command, replay_receipt = command(
        private_root,
        database,
        claim_path,
        claim_sha256,
        "replay.json",
        f"{verdict.lower()}-replay",
    )
    replay = subprocess.run(
        replay_command,
        capture_output=True,
        text=True,
        env=environment(),
    )
    if (
        replay.returncode != 2
        or replay.stdout
        or replay.stderr
        != "ClassificationContractError[REPLAY_REJECTED]: transaction stopped\n"
        or replay_receipt.exists()
        or counts(database) != after
    ):
        raise RuntimeError("classification replay was not refused exactly")
    return {
        "verdict": verdict,
        "rows_written": result["records_written"],
        "replay": "REPLAY_REJECTED",
    }


def run_spent_precondition_case(base: Path) -> None:
    private_root, database, claim_path, claim_sha256 = setup_case(
        base, "spent-precondition", "PASS"
    )
    connection = sqlite3.connect(database)
    connection.execute("UPDATE jobs SET verdict='KILLED'")
    connection.commit()
    connection.close()
    before = counts(database)
    invocation, receipt_path = command(
        private_root,
        database,
        claim_path,
        claim_sha256,
        "precondition.json",
        "precondition",
    )
    completed = subprocess.run(
        invocation,
        capture_output=True,
        text=True,
        env=environment(),
    )
    if (
        completed.returncode != 2
        or completed.stdout
        or completed.stderr
        != "ClassificationContractError[ROW_DIGEST_MISMATCH]: transaction stopped\n"
        or receipt_path.exists()
        or counts(database) != before
    ):
        raise RuntimeError("changed precondition did not stop exactly")
    second_command, _second_receipt = command(
        private_root,
        database,
        claim_path,
        claim_sha256,
        "precondition-replay.json",
        "precondition-replay",
    )
    second = subprocess.run(
        second_command,
        capture_output=True,
        text=True,
        env=environment(),
    )
    if second.stderr != "ClassificationContractError[REPLAY_REJECTED]: transaction stopped\n":
        raise RuntimeError("failed classification identity was replayable")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="taey-apply-classification-") as temporary:
        base = Path(temporary)
        pass_case = run_success_case(base, "PASS")
        killed_case = run_success_case(base, "KILLED")
        run_spent_precondition_case(base)
        print(
            json.dumps(
                {
                    "schema": "taey_apply_classification_gate_v1",
                    "terminal_verdict_cases": [pass_case, killed_case],
                    "spent_failure_nonreplayable": True,
                    "raw_values_excluded": True,
                    "applications_unchanged": True,
                    "apply_runs_unchanged": True,
                    "verdict": "PASS",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
