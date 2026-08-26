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


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def text_digest(value: str) -> str:
    return digest(value.encode("utf-8"))


def private_json(path: Path, value: object) -> str:
    raw = canonical(value)
    path.write_bytes(raw)
    path.chmod(0o400)
    return digest(raw)


def lock(lineage: str, correlation: str) -> dict[str, object]:
    return {
        "policy": "careers",
        "request_id": "a" * 64,
        "acquired": True,
        "released": True,
        "owner_token_sha256": "b" * 64,
        "wait_ms": 0,
        "turn_lineage_sha256": lineage,
        "correlation_id_sha256": correlation,
        "deadline_seconds": 120,
    }


def create_database(path: Path, *, unique_url: bool = True) -> None:
    connection = sqlite3.connect(path)
    url_definition = "url TEXT PRIMARY KEY" if unique_url else "url TEXT"
    connection.execute(
        f"CREATE TABLE jobs({url_definition},source TEXT,company TEXT,title TEXT,location TEXT,"
        "workplace TEXT,description TEXT,posted TEXT,posted_raw TEXT,posted_source TEXT,first_seen TEXT,"
        "verdict TEXT,kill_reason TEXT,detail TEXT,applied_at TEXT,score INTEGER)"
    )
    connection.execute("CREATE TABLE applications(url TEXT PRIMARY KEY)")
    connection.execute(
        "CREATE TABLE apply_runs(target_url TEXT,phase_key TEXT,PRIMARY KEY(target_url,phase_key))"
    )
    connection.commit()
    connection.close()
    path.chmod(0o600)


def build_private_inputs(
    private_root: Path, *, mutation: str | None = None
) -> tuple[str, str]:
    source_dir = private_root / "sources"
    transaction_dir = private_root / "transactions" / "seat"
    receipt_dir = private_root / "receipts" / "seat"
    for directory in (
        source_dir,
        private_root / "transactions",
        transaction_dir,
        private_root / "receipts",
        receipt_dir,
    ):
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
    search_ref = "opaque-search-reference"
    job_id = {
        "leading_zero_job_id": "01234567890",
        "non_ascii_job_id": "１２３４５６７８９０",
        "encoded_job_id_alias": "%311234567890",
    }.get(mutation, "1234567890")
    source_url = (
        "https://www.linkedin.com/jobs/search-results/"
        f"?currentJobId={job_id}&keywords=example"
    )
    card_body = {
        "ordinal": 0,
        "target_card_name": "Example mounted card",
        "detail_title_name": "Example role",
        "detail_company_name": "Example company",
        "location_text": "Remote",
        "showing": True,
    }
    card = {**card_body, "card_digest": digest(canonical(card_body))}
    if mutation == "card_digest_mismatch":
        card["card_digest"] = "0" * 64
    search_artifact = {
        "schema": "linkedin_mounted_job_search_v1",
        "search_ref": search_ref,
        "source_url": source_url,
        "cards": [card],
    }
    search_artifact_sha = private_json(
        source_dir / "search-artifact.json", search_artifact
    )
    search_lineage = "c" * 64
    search_correlation = "d" * 64
    search_receipt = {
        "schema": "linkedin_job_search_receipt_v1",
        "platform": "linkedin",
        "operation": "capture_mounted_job_search",
        "display": ":18",
        "requester": "example-seat",
        "turn_lineage_sha256": search_lineage,
        "correlation_id_sha256": search_correlation,
        "deadline_seconds": 120,
        "hands_commit": "1" * 40,
        "state": "captured",
        "ok": True,
        "failure_code": None,
        "transaction_sha256": "e" * 64,
        "expected_transaction_sha256": "e" * 64,
        "search_ref_sha256": text_digest(search_ref),
        "sink_ref_sha256": "f" * 64,
        "pre_observation_sha256": search_artifact_sha,
        "pre_match_counts": {
            "structural_candidates": 1,
            "valid_cards": 1,
            "duplicate_cards": 0,
        },
        "stable_cycles_observed": 2,
        "lock": lock(search_lineage, search_correlation),
        "action": {
            "kind": "private_sink_write_once",
            "verdict": "written",
            "batches_observed": 1,
            "batches_written": 1,
            "cards_observed": 1,
            "content_digest": search_artifact_sha,
        },
        "postcondition": {
            "kind": "mounted_job_card_set_digest_unchanged",
            "verdict": "satisfied",
            "post_observation_sha256": search_artifact_sha,
            "post_match_counts": {
                "structural_candidates": 1,
                "valid_cards": 1,
                "duplicate_cards": 0,
            },
        },
    }
    if mutation == "search_stable_cycle_one":
        search_receipt["stable_cycles_observed"] = 1
    elif mutation == "receipt_lineage_mismatch":
        search_receipt["lock"]["turn_lineage_sha256"] = "9" * 64
    elif mutation == "receipt_digest_mismatch":
        search_receipt["pre_observation_sha256"] = "8" * 64
    private_json(source_dir / "search-receipt.json", search_receipt)
    selected_search_ref = (
        "different-search-reference"
        if mutation == "search_ref_mismatch"
        else search_ref
    )
    selected_artifact = {
        "schema": "linkedin_selected_job_v1",
        "search_ref": selected_search_ref,
        "source_url": source_url,
        "detail_heading": "About the job",
        "detail_text": "This generated description exercises the complete deterministic intake boundary.",
    }
    selected_artifact_sha = private_json(
        source_dir / "selected-artifact.json", selected_artifact
    )
    selected_lineage = "3" * 64
    selected_correlation = "4" * 64
    selected_counts = {"about_job_heading": 1, "selected_job_description_path": 1}
    selected_receipt = {
        "schema": "linkedin_jobs_receipt_v1",
        "platform": "linkedin",
        "operation": "select_and_capture_job",
        "display": ":18",
        "requester": "example-seat",
        "turn_lineage_sha256": selected_lineage,
        "correlation_id_sha256": selected_correlation,
        "deadline_seconds": 120,
        "hands_commit": "2" * 40,
        "terminal_state": "captured",
        "ok": True,
        "failure_code": None,
        "transaction_sha256": "5" * 64,
        "expected_transaction_sha256": "5" * 64,
        "search_ref_sha256": text_digest(selected_search_ref),
        "sink_ref_sha256": "6" * 64,
        "pre_observation_sha256": selected_artifact_sha,
        "pre_match_counts": selected_counts,
        "selection": {
            "kind": "private_exact_job_card_atspi_activate",
            "verdict": "satisfied",
            "target_card_name_sha256": text_digest(card_body["target_card_name"]),
            "detail_title_name_sha256": text_digest(card_body["detail_title_name"]),
            "detail_company_name_sha256": text_digest(card_body["detail_company_name"]),
            "target_match_count": 1,
            "detail_title_match_count": 1,
            "detail_company_match_count": 1,
            "stable_cycles_observed": 2,
            "action_name": "click",
            "action_index": 0,
            "action_match_count": 1,
        },
        "lock": lock(selected_lineage, selected_correlation),
        "action": {
            "kind": "private_sink_write_once",
            "verdict": "written",
            "records_observed": 1,
            "records_written": 1,
            "content_digest": selected_artifact_sha,
        },
        "postcondition": {
            "kind": "selected_job_content_digest_unchanged",
            "verdict": "satisfied",
            "post_observation_sha256": selected_artifact_sha,
            "post_match_counts": selected_counts,
        },
    }
    if mutation == "selection_title_hash_mismatch":
        selected_receipt["selection"]["detail_title_name_sha256"] = "7" * 64
    elif mutation == "selection_company_hash_mismatch":
        selected_receipt["selection"]["detail_company_name_sha256"] = "7" * 64
    elif mutation == "selected_stable_cycle_one":
        selected_receipt["selection"]["stable_cycles_observed"] = 1
    elif mutation == "receipt_transaction_digest_mismatch":
        selected_receipt["expected_transaction_sha256"] = "8" * 64
    private_json(source_dir / "selected-receipt.json", selected_receipt)
    transaction = {
        "schema": "taey_apply_linkedin_intake_private_input_v1",
        "operation": "ingest_linkedin_captured_job",
        "search_receipt_ref": "sources/search-receipt.json",
        "search_artifact_ref": "sources/search-artifact.json",
        "selected_receipt_ref": "sources/selected-receipt.json",
        "selected_artifact_ref": "sources/selected-artifact.json",
        "card_digest": card["card_digest"],
    }
    transaction_sha = private_json(transaction_dir / "intake.json", transaction)
    return transaction_sha, card["card_digest"]


def connector_command(
    root: Path, database: Path, transaction_sha: str, receipt_name: str, turn: str
) -> tuple[list[str], Path]:
    receipt = root / "receipts" / "seat" / receipt_name
    return [
        sys.executable,
        "-m",
        "taey_apply.cli",
        "--private-root",
        str(root),
        "--database",
        str(database),
        "--transaction-file",
        str(root / "transactions" / "seat" / "intake.json"),
        "--expected-transaction-sha256",
        transaction_sha,
        "--receipt-file",
        str(receipt),
        "--requester",
        "example-seat",
        "--turn-id",
        turn,
        "--correlation-id",
        turn,
        "--process-generation",
        "7" * 32,
    ], receipt


def connector_environment() -> dict[str, str]:
    environment = dict(os.environ)
    source_root = Path(__file__).resolve().parents[1] / "src"
    environment["PYTHONPATH"] = str(source_root)
    return environment


def invoke(
    root: Path, database: Path, transaction_sha: str, receipt_name: str, turn: str
) -> dict[str, object]:
    command, receipt = connector_command(
        root, database, transaction_sha, receipt_name, turn
    )
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=connector_environment(),
    )
    if completed.stderr:
        raise RuntimeError("connector wrote unexpected stderr")
    result = json.loads(completed.stdout)
    if receipt.stat().st_mode & 0o777 != 0o400:
        raise RuntimeError("receipt mode differs from contract")
    if digest(receipt.read_bytes()) != result["receipt_sha256"]:
        raise RuntimeError("receipt digest differs from result")
    return result


def invoke_failure(
    root: Path,
    database: Path,
    transaction_sha: str,
    receipt_name: str,
    turn: str,
    expected_failure_code: str,
) -> None:
    command, receipt = connector_command(
        root, database, transaction_sha, receipt_name, turn
    )
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=connector_environment(),
    )
    expected_stderr = (
        f"IntakeContractError[{expected_failure_code}]: transaction stopped\n"
    )
    if (
        completed.returncode != 2
        or completed.stdout
        or completed.stderr != expected_stderr
        or receipt.exists()
    ):
        raise RuntimeError(f"adversarial case {turn} did not stop exactly")


def database_counts(database: Path) -> tuple[int, int, int]:
    connection = sqlite3.connect(database)
    counts = tuple(
        int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in ("jobs", "applications", "apply_runs")
    )
    connection.close()
    return counts


def run_failure_case(
    base: Path,
    *,
    name: str,
    expected_failure_code: str,
    mutation: str | None = None,
    database_shape: str = "canonical",
) -> None:
    case_root = base / "adversarial" / name
    private_root = case_root / "private"
    database_root = case_root / "database"
    private_root.mkdir(mode=0o700, parents=True)
    database_root.mkdir(mode=0o700)
    private_root.chmod(0o700)
    database_root.chmod(0o700)
    database = database_root / "jobs.db"
    create_database(
        database,
        unique_url=database_shape not in {"missing_unique", "duplicate_urls"},
    )
    transaction_sha, _card_digest = build_private_inputs(
        private_root, mutation=mutation
    )
    if database_shape == "duplicate_urls":
        connection = sqlite3.connect(database)
        canonical_url = "https://www.linkedin.com/jobs/view/1234567890/"
        connection.execute("INSERT INTO jobs(url) VALUES(?)", (canonical_url,))
        connection.execute("INSERT INTO jobs(url) VALUES(?)", (canonical_url,))
        connection.commit()
        connection.close()
    elif database_shape == "existing_conflict":
        connection = sqlite3.connect(database)
        connection.execute(
            "INSERT INTO jobs(url,source,company,title,location,description,first_seen) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                "https://www.linkedin.com/jobs/view/1234567890/",
                "linkedin:ui",
                "Different company",
                "Example role",
                "Remote",
                "This generated description exercises the complete deterministic intake boundary.",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        connection.commit()
        connection.close()
    before = database_counts(database)
    invoke_failure(
        private_root,
        database,
        transaction_sha,
        f"{name}.json",
        name,
        expected_failure_code,
    )
    if database_counts(database) != before:
        raise RuntimeError(f"adversarial case {name} changed database state")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="taey-apply-contract-") as temporary:
        base = Path(temporary)
        private_root = base / "private"
        database_root = base / "database"
        private_root.mkdir(mode=0o700)
        database_root.mkdir(mode=0o700)
        private_root.chmod(0o700)
        database_root.chmod(0o700)
        database = database_root / "jobs.db"
        create_database(database)
        transaction_sha, _card_digest = build_private_inputs(private_root)
        first = invoke(
            private_root, database, transaction_sha, "first.json", "intake-first"
        )
        second = invoke(
            private_root, database, transaction_sha, "second.json", "intake-second"
        )
        connection = sqlite3.connect(database)
        row = connection.execute(
            "SELECT COUNT(*),verdict,score,applied_at FROM jobs"
        ).fetchone()
        applications = connection.execute(
            "SELECT COUNT(*) FROM applications"
        ).fetchone()[0]
        apply_runs = connection.execute("SELECT COUNT(*) FROM apply_runs").fetchone()[0]
        connection.close()
        if (
            first["state"] != "captured_unclassified"
            or first["records_written"] != 1
            or second["state"] != "already_present"
            or second["records_written"] != 0
            or row != (1, None, None, None)
            or applications != 0
            or apply_runs != 0
        ):
            raise RuntimeError("deterministic intake postcondition failed")
        adversarial_cases = [
            {
                "name": "database_missing_url_uniqueness",
                "expected_failure_code": "database_contract_invalid",
                "database_shape": "missing_unique",
            },
            {
                "name": "database_duplicate_url_rows",
                "expected_failure_code": "database_contract_invalid",
                "database_shape": "duplicate_urls",
            },
            {
                "name": "leading_zero_job_id",
                "expected_failure_code": "pair_mismatch",
                "mutation": "leading_zero_job_id",
            },
            {
                "name": "non_ascii_job_id",
                "expected_failure_code": "pair_mismatch",
                "mutation": "non_ascii_job_id",
            },
            {
                "name": "encoded_job_id_alias",
                "expected_failure_code": "pair_mismatch",
                "mutation": "encoded_job_id_alias",
            },
            {
                "name": "card_digest_mismatch",
                "expected_failure_code": "source_artifact_invalid",
                "mutation": "card_digest_mismatch",
            },
            {
                "name": "search_ref_mismatch",
                "expected_failure_code": "pair_mismatch",
                "mutation": "search_ref_mismatch",
            },
            {
                "name": "selection_title_hash_mismatch",
                "expected_failure_code": "pair_mismatch",
                "mutation": "selection_title_hash_mismatch",
            },
            {
                "name": "selection_company_hash_mismatch",
                "expected_failure_code": "pair_mismatch",
                "mutation": "selection_company_hash_mismatch",
            },
            {
                "name": "receipt_lineage_mismatch",
                "expected_failure_code": "source_receipt_invalid",
                "mutation": "receipt_lineage_mismatch",
            },
            {
                "name": "receipt_digest_mismatch",
                "expected_failure_code": "source_receipt_invalid",
                "mutation": "receipt_digest_mismatch",
            },
            {
                "name": "receipt_transaction_digest_mismatch",
                "expected_failure_code": "source_receipt_invalid",
                "mutation": "receipt_transaction_digest_mismatch",
            },
            {
                "name": "search_stable_cycle_one",
                "expected_failure_code": "source_receipt_invalid",
                "mutation": "search_stable_cycle_one",
            },
            {
                "name": "selected_stable_cycle_one",
                "expected_failure_code": "source_receipt_invalid",
                "mutation": "selected_stable_cycle_one",
            },
            {
                "name": "existing_row_conflict",
                "expected_failure_code": "existing_row_conflict",
                "database_shape": "existing_conflict",
            },
        ]
        for case in adversarial_cases:
            run_failure_case(base, **case)
        print(
            json.dumps(
                {
                    "schema": "taey_apply_contract_gate_v1",
                    "first_records_written": first["records_written"],
                    "second_records_written": second["records_written"],
                    "jobs_rows": row[0],
                    "applications_rows": applications,
                    "apply_runs_rows": apply_runs,
                    "null_boundary": row[1:] == (None, None, None),
                    "adversarial_cases": len(adversarial_cases),
                    "verdict": "PASS",
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
