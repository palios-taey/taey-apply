from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Sequence

from . import __version__
from .classification_contract import (
    OPERATION,
    RECEIPT_SCHEMA,
    RESULT_SCHEMA,
    ClassificationClaim,
    ClassificationContractError,
)
from .contract import (
    IntakeContractError,
    canonical_json_bytes,
    read_private_input,
    read_private_json,
    resolve_private_reference,
    sha256_hex,
    validate_digest,
    validate_git_commit,
    write_new_private_json,
)
from .linkedin_intake import (
    LinkedInCapture,
    _validate_jobs_table,
    load_linkedin_capture,
)


_INTAKE_RECEIPT_KEYS = {
    "schema",
    "operation",
    "connector_version",
    "requester",
    "turn_lineage_sha256",
    "correlation_id_sha256",
    "transaction_sha256",
    "state",
    "ok",
    "failure_code",
    "sources",
    "pairing",
    "action",
    "postcondition",
}
_SOURCE_KEYS = {
    "search_artifact_sha256",
    "search_receipt_sha256",
    "selected_artifact_sha256",
    "selected_receipt_sha256",
    "search_hands_commit",
    "selected_hands_commit",
    "card_digest",
}
_PAIRING_KEYS = {
    "card_match_count",
    "selection_identity_match_count",
    "search_reference_match_count",
    "current_job_id_match_count",
    "job_identity_sha256",
    "capture_digest",
}
_ACTION_KEYS = {
    "kind",
    "verdict",
    "records_observed",
    "records_written",
    "row_digest",
}
_POSTCONDITION_KEYS = {
    "kind",
    "verdict",
    "jobs_match_count",
    "verdict_is_null",
    "score_is_null",
    "applied_at_is_null",
    "applications_before",
    "applications_after",
    "apply_runs_before",
    "apply_runs_after",
}


@dataclass(frozen=True, slots=True)
class QualifiedIntake:
    capture: LinkedInCapture
    intake_transaction_sha256: str
    intake_receipt_sha256: str
    intake_row_digest: str


@dataclass(frozen=True, slots=True)
class ClassificationWrite:
    jobs_before: int
    jobs_after: int
    applications_before: int
    applications_after: int
    apply_runs_before: int
    apply_runs_after: int
    stable_row_sha256: str
    postwrite_row_sha256: str


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", f"{context} must be an object"
        )
    return value


def _expect_keys(value: Mapping[str, Any], keys: set[str], context: str) -> None:
    if set(value) != keys:
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", f"{context} keys differ"
        )


def _exact_count(value: object, expected: int, context: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", f"{context} differs"
        )


def _validate_upstream_receipt(
    receipt: Mapping[str, Any], capture: LinkedInCapture, claim: ClassificationClaim
) -> str:
    _expect_keys(receipt, _INTAKE_RECEIPT_KEYS, "intake receipt")
    if (
        receipt["schema"] != "taey_apply_linkedin_intake_receipt_v1"
        or receipt["operation"] != "ingest_linkedin_captured_job"
        or receipt["connector_version"] != "0.1.1"
        or receipt["state"] not in {"captured_unclassified", "already_present"}
        or receipt["ok"] is not True
        or receipt["failure_code"] is not None
        or receipt["transaction_sha256"] != claim.intake_transaction_sha256
    ):
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "intake receipt is not qualified"
        )
    try:
        validate_digest(receipt["turn_lineage_sha256"], "intake lineage")
        validate_digest(receipt["correlation_id_sha256"], "intake correlation")
    except IntakeContractError as exc:
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "intake receipt lineage is invalid"
        ) from exc
    sources = _mapping(receipt["sources"], "intake sources")
    _expect_keys(sources, _SOURCE_KEYS, "intake sources")
    expected_sources = {
        "search_artifact_sha256": capture.search_artifact_sha256,
        "search_receipt_sha256": capture.search_receipt_sha256,
        "selected_artifact_sha256": capture.selected_artifact_sha256,
        "selected_receipt_sha256": capture.selected_receipt_sha256,
        "search_hands_commit": capture.search_hands_commit,
        "selected_hands_commit": capture.selected_hands_commit,
        "card_digest": capture.card_digest,
    }
    if dict(sources) != expected_sources:
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "intake source binding differs"
        )
    try:
        validate_git_commit(sources["search_hands_commit"], "search Hands commit")
        validate_git_commit(sources["selected_hands_commit"], "selected Hands commit")
    except IntakeContractError as exc:
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "intake source commit is invalid"
        ) from exc
    pairing = _mapping(receipt["pairing"], "intake pairing")
    _expect_keys(pairing, _PAIRING_KEYS, "intake pairing")
    for key, expected in (
        ("card_match_count", 1),
        ("selection_identity_match_count", 3),
        ("search_reference_match_count", 4),
        ("current_job_id_match_count", 1),
    ):
        _exact_count(pairing[key], expected, f"intake {key}")
    if (
        pairing["job_identity_sha256"] != capture.job_identity_sha256
        or pairing["capture_digest"] != capture.capture_digest
    ):
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "intake pairing digest differs"
        )
    action = _mapping(receipt["action"], "intake action")
    _expect_keys(action, _ACTION_KEYS, "intake action")
    expected_written = 1 if receipt["state"] == "captured_unclassified" else 0
    expected_verdict = "written" if expected_written else "already_present"
    if (
        action["kind"] != "sqlite_insert_once"
        or action["verdict"] != expected_verdict
        or action["records_observed"] != 1
        or action["records_written"] != expected_written
        or action["row_digest"] != capture.capture_digest
    ):
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "intake action differs"
        )
    postcondition = _mapping(receipt["postcondition"], "intake postcondition")
    _expect_keys(postcondition, _POSTCONDITION_KEYS, "intake postcondition")
    if (
        postcondition["kind"] != "exact_capture_row_present"
        or postcondition["verdict"] != "satisfied"
        or postcondition["jobs_match_count"] != 1
        or postcondition["verdict_is_null"] is not True
        or postcondition["score_is_null"] is not True
        or postcondition["applied_at_is_null"] is not True
        or postcondition["applications_before"]
        != postcondition["applications_after"]
        or postcondition["apply_runs_before"] != postcondition["apply_runs_after"]
    ):
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "intake postcondition is not qualified"
        )
    return str(action["row_digest"])


def load_qualified_intake(
    private_root: Path, claim: ClassificationClaim
) -> QualifiedIntake:
    try:
        transaction_path = resolve_private_reference(
            private_root,
            claim.intake_transaction_ref,
            "intake transaction",
        )
        transaction, transaction_sha256 = read_private_input(
            transaction_path,
            private_root,
            claim.intake_transaction_sha256,
        )
        capture = load_linkedin_capture(private_root, transaction)
        receipt_path = resolve_private_reference(
            private_root, claim.intake_receipt_ref, "intake receipt"
        )
        receipt, receipt_bytes = read_private_json(receipt_path, "intake receipt")
    except IntakeContractError as exc:
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "qualified intake could not be loaded"
        ) from exc
    receipt_sha256 = sha256_hex(receipt_bytes)
    if receipt_sha256 != claim.intake_receipt_sha256:
        raise ClassificationContractError(
            "UPSTREAM_INTAKE_INVALID", "intake receipt digest differs"
        )
    intake_row_digest = _validate_upstream_receipt(receipt, capture, claim)
    return QualifiedIntake(
        capture=capture,
        intake_transaction_sha256=transaction_sha256,
        intake_receipt_sha256=receipt_sha256,
        intake_row_digest=intake_row_digest,
    )


def _digestable_sqlite_value(value: object) -> object:
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ClassificationContractError(
                "DATABASE_CONTRACT_INVALID", "job row contains a non-finite value"
            )
        return value
    if isinstance(value, bytes):
        return {"sqlite_blob_sha256": sha256_hex(value)}
    raise ClassificationContractError(
        "DATABASE_CONTRACT_INVALID", "job row contains an unsupported value"
    )


def row_sha256(columns: Sequence[str], row: Sequence[object]) -> str:
    if len(columns) != len(row):
        raise ClassificationContractError(
            "DATABASE_CONTRACT_INVALID", "job row shape differs"
        )
    return sha256_hex(
        canonical_json_bytes(
            {
                column: _digestable_sqlite_value(value)
                for column, value in zip(columns, row, strict=True)
            }
        )
    )


def stable_row_sha256(columns: Sequence[str], row: Sequence[object]) -> str:
    stable_pairs = [
        (column, value)
        for column, value in zip(columns, row, strict=True)
        if column != "verdict"
    ]
    return row_sha256(
        [column for column, _value in stable_pairs],
        [value for _column, value in stable_pairs],
    )


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _jobs_columns(connection: sqlite3.Connection) -> list[str]:
    return [
        str(row[1])
        for row in sorted(
            connection.execute("PRAGMA table_info(jobs)").fetchall(),
            key=lambda item: int(item[0]),
        )
    ]


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    columns = connection.execute(f"PRAGMA table_info({_quoted(table)})").fetchall()
    if not columns:
        raise ClassificationContractError(
            "DATABASE_CONTRACT_INVALID", "application-state table is absent"
        )
    return int(connection.execute(f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0])


def _target_rows(
    connection: sqlite3.Connection,
    columns: Sequence[str],
    canonical_url: str,
) -> list[tuple[object, ...]]:
    projection = ",".join(_quoted(column) for column in columns)
    return connection.execute(
        f"SELECT {projection} FROM jobs WHERE url=?", (canonical_url,)
    ).fetchall()


def _rollback(connection: sqlite3.Connection, mutation_observed: bool) -> None:
    if not connection.in_transaction:
        return
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error as exc:
        code = "SIDE_EFFECT_UNCERTAIN" if mutation_observed else "DATABASE_OPERATION_FAILED"
        raise ClassificationContractError(code, "rollback was not proven") from exc


def _verify_committed_row(
    database: Path,
    canonical_url: str,
    columns: Sequence[str],
    verdict: str,
    stable_sha256: str,
) -> str:
    connection = sqlite3.connect(str(database), timeout=30, isolation_level=None)
    try:
        rows = _target_rows(connection, columns, canonical_url)
        if len(rows) != 1:
            raise ClassificationContractError(
                "SIDE_EFFECT_UNCERTAIN", "committed row identity is not exact"
            )
        row = rows[0]
        values = dict(zip(columns, row, strict=True))
        if (
            values["verdict"] != verdict
            or values["score"] is not None
            or values["applied_at"] is not None
            or stable_row_sha256(columns, row) != stable_sha256
        ):
            raise ClassificationContractError(
                "SIDE_EFFECT_UNCERTAIN", "committed row postcondition differs"
            )
        return row_sha256(columns, row)
    except sqlite3.Error as exc:
        raise ClassificationContractError(
            "SIDE_EFFECT_UNCERTAIN", "committed row could not be verified"
        ) from exc
    finally:
        connection.close()


def persist_classification(
    database: Path,
    qualified: QualifiedIntake,
    claim: ClassificationClaim,
) -> ClassificationWrite:
    connection = sqlite3.connect(str(database), timeout=30, isolation_level=None)
    mutation_observed = False
    committed = False
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            _validate_jobs_table(connection)
        except IntakeContractError as exc:
            raise ClassificationContractError(
                "DATABASE_CONTRACT_INVALID", "jobs table contract differs"
            ) from exc
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='jobs'"
        ).fetchall()
        if triggers:
            raise ClassificationContractError(
                "DATABASE_CONTRACT_INVALID", "jobs table has write triggers"
            )
        columns = _jobs_columns(connection)
        for required in ("url", "verdict", "score", "applied_at"):
            if required not in columns:
                raise ClassificationContractError(
                    "DATABASE_CONTRACT_INVALID", "jobs table contract differs"
                )
        jobs_before = _table_count(connection, "jobs")
        applications_before = _table_count(connection, "applications")
        apply_runs_before = _table_count(connection, "apply_runs")
        rows = _target_rows(
            connection, columns, qualified.capture.canonical_url
        )
        if len(rows) != 1:
            raise ClassificationContractError(
                "ROW_IDENTITY_MISMATCH", "job identity is not exact"
            )
        before_row = rows[0]
        before_values = dict(zip(columns, before_row, strict=True))
        prewrite_sha256 = row_sha256(columns, before_row)
        stable_sha256 = stable_row_sha256(columns, before_row)
        if (
            qualified.capture.job_identity_sha256
            != sha256_hex(str(before_values["url"]).encode("utf-8"))
            or prewrite_sha256 != claim.prewrite_row_sha256
            or stable_sha256 != claim.stable_row_sha256
        ):
            raise ClassificationContractError(
                "ROW_DIGEST_MISMATCH", "frozen row digest differs"
            )
        if any(before_values[key] is not None for key in ("verdict", "score", "applied_at")):
            raise ClassificationContractError(
                "PRECONDITION_MISMATCH", "job row is not unclassified"
            )
        connection.execute(
            "UPDATE jobs SET verdict=? "
            "WHERE url=? AND verdict IS NULL AND score IS NULL AND applied_at IS NULL",
            (claim.verdict, qualified.capture.canonical_url),
        )
        records_written = int(connection.execute("SELECT changes()").fetchone()[0])
        mutation_observed = records_written != 0
        if records_written != 1 or connection.total_changes != 1:
            raise ClassificationContractError(
                "WRITE_POSTCONDITION_FAILED", "classification effect is not exact"
            )
        after_rows = _target_rows(
            connection, columns, qualified.capture.canonical_url
        )
        if len(after_rows) != 1:
            raise ClassificationContractError(
                "WRITE_POSTCONDITION_FAILED", "classified row identity is not exact"
            )
        after_row = after_rows[0]
        after_values = dict(zip(columns, after_row, strict=True))
        if (
            after_values["verdict"] != claim.verdict
            or after_values["score"] is not None
            or after_values["applied_at"] is not None
            or stable_row_sha256(columns, after_row) != stable_sha256
        ):
            raise ClassificationContractError(
                "WRITE_POSTCONDITION_FAILED", "classified row postcondition differs"
            )
        jobs_after = _table_count(connection, "jobs")
        applications_after = _table_count(connection, "applications")
        apply_runs_after = _table_count(connection, "apply_runs")
        if (
            jobs_after != jobs_before
            or applications_after != applications_before
            or apply_runs_after != apply_runs_before
        ):
            raise ClassificationContractError(
                "WRITE_POSTCONDITION_FAILED", "non-classification state changed"
            )
        try:
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            raise ClassificationContractError(
                "WRITE_INDETERMINATE", "database commit was not proven"
            ) from exc
        committed = True
        postwrite_sha256 = _verify_committed_row(
            database,
            qualified.capture.canonical_url,
            columns,
            claim.verdict,
            stable_sha256,
        )
        return ClassificationWrite(
            jobs_before=jobs_before,
            jobs_after=jobs_after,
            applications_before=applications_before,
            applications_after=applications_after,
            apply_runs_before=apply_runs_before,
            apply_runs_after=apply_runs_after,
            stable_row_sha256=stable_sha256,
            postwrite_row_sha256=postwrite_sha256,
        )
    except ClassificationContractError:
        if not committed:
            _rollback(connection, mutation_observed)
        raise
    except sqlite3.Error as exc:
        if committed:
            raise ClassificationContractError(
                "SIDE_EFFECT_UNCERTAIN", "database state could not be proven"
            ) from exc
        _rollback(connection, mutation_observed)
        raise ClassificationContractError(
            "DATABASE_OPERATION_FAILED", "database operation failed"
        ) from exc
    finally:
        connection.close()


def finalize_classification(
    *,
    receipt_path: Path,
    requester: str,
    transaction_sha256: str,
    attempt_sha256: str,
    turn_lineage_sha256: str,
    correlation_id_sha256: str,
    qualified: QualifiedIntake,
    claim: ClassificationClaim,
    write: ClassificationWrite,
) -> dict[str, Any]:
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "operation": OPERATION,
        "connector_version": __version__,
        "requester": requester,
        "turn_lineage_sha256": turn_lineage_sha256,
        "correlation_id_sha256": correlation_id_sha256,
        "transaction_sha256": transaction_sha256,
        "state": "classified",
        "ok": True,
        "failure_code": None,
        "upstream": {
            "intake_transaction_sha256": qualified.intake_transaction_sha256,
            "intake_receipt_sha256": qualified.intake_receipt_sha256,
            "job_identity_sha256": qualified.capture.job_identity_sha256,
            "intake_row_sha256": qualified.intake_row_digest,
        },
        "decision_binding": {
            "attempt_sha256": attempt_sha256,
            "policy_input_sha256": claim.policy_input_sha256,
            "classifier_sha256": claim.classifier_sha256,
            "prewrite_row_sha256": claim.prewrite_row_sha256,
            "stable_row_sha256": claim.stable_row_sha256,
        },
        "action": {
            "kind": "sqlite_update_once",
            "records_observed": 1,
            "records_written": 1,
            "changed_columns": ["verdict"],
        },
        "postcondition": {
            "kind": "exact_terminal_classification_present",
            "verdict": "satisfied",
            "jobs_before": write.jobs_before,
            "jobs_after": write.jobs_after,
            "applications_before": write.applications_before,
            "applications_after": write.applications_after,
            "apply_runs_before": write.apply_runs_before,
            "apply_runs_after": write.apply_runs_after,
            "stable_row_sha256": write.stable_row_sha256,
            "postwrite_row_sha256": write.postwrite_row_sha256,
            "score_is_null": True,
            "applied_at_is_null": True,
            "terminal": True,
        },
    }
    try:
        receipt_bytes = write_new_private_json(receipt_path, receipt)
    except (IntakeContractError, OSError) as exc:
        raise ClassificationContractError(
            "RECEIPT_INDETERMINATE", "receipt publication was not proven"
        ) from exc
    return {
        "schema": RESULT_SCHEMA,
        "operation": OPERATION,
        "ok": True,
        "state": "classified",
        "failure_code": None,
        "records_observed": 1,
        "records_written": 1,
        "transaction_sha256": transaction_sha256,
        "receipt_sha256": sha256_hex(receipt_bytes),
        "turn_lineage_sha256": turn_lineage_sha256,
        "terminal": True,
    }
