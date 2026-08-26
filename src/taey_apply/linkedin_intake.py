from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping
from urllib.parse import urlsplit

from . import __version__
from .contract import (
    OPERATION,
    RECEIPT_SCHEMA,
    RESULT_SCHEMA,
    IntakeContractError,
    canonical_json_bytes,
    read_private_json,
    resolve_private_reference,
    sha256_hex,
    validate_digest,
    validate_git_commit,
    write_new_private_json,
)


SEARCH_ARTIFACT_SCHEMA = "linkedin_mounted_job_search_v1"
SEARCH_RECEIPT_SCHEMA = "linkedin_job_search_receipt_v1"
SELECTED_ARTIFACT_SCHEMA = "linkedin_selected_job_v1"
SELECTED_RECEIPT_SCHEMA = "linkedin_jobs_receipt_v1"

_SEARCH_RECEIPT_KEYS = {
    "schema",
    "platform",
    "operation",
    "display",
    "requester",
    "turn_lineage_sha256",
    "correlation_id_sha256",
    "deadline_seconds",
    "hands_commit",
    "state",
    "ok",
    "failure_code",
    "transaction_sha256",
    "expected_transaction_sha256",
    "search_ref_sha256",
    "sink_ref_sha256",
    "pre_observation_sha256",
    "pre_match_counts",
    "stable_cycles_observed",
    "lock",
    "action",
    "postcondition",
}
_SELECTED_RECEIPT_KEYS = {
    "schema",
    "platform",
    "operation",
    "display",
    "requester",
    "turn_lineage_sha256",
    "correlation_id_sha256",
    "deadline_seconds",
    "hands_commit",
    "terminal_state",
    "ok",
    "failure_code",
    "transaction_sha256",
    "expected_transaction_sha256",
    "search_ref_sha256",
    "sink_ref_sha256",
    "pre_observation_sha256",
    "pre_match_counts",
    "selection",
    "lock",
    "action",
    "postcondition",
}
_LOCK_KEYS = {
    "policy",
    "request_id",
    "acquired",
    "released",
    "owner_token_sha256",
    "wait_ms",
    "turn_lineage_sha256",
    "correlation_id_sha256",
    "deadline_seconds",
}
_CARD_KEYS = {
    "ordinal",
    "target_card_name",
    "detail_title_name",
    "detail_company_name",
    "location_text",
    "showing",
    "card_digest",
}
_CAPTURE_COLUMNS = (
    "url",
    "source",
    "company",
    "title",
    "location",
    "workplace",
    "description",
    "posted",
    "posted_raw",
    "posted_source",
)
_REQUIRED_JOB_COLUMN_TYPES = {
    **{column: "TEXT" for column in _CAPTURE_COLUMNS},
    "first_seen": "TEXT",
    "verdict": "TEXT",
    "kill_reason": "TEXT",
    "detail": "TEXT",
    "applied_at": "TEXT",
    "score": "INTEGER",
}
_JOB_ID_RE = re.compile(r"[1-9][0-9]{0,19}")


@dataclass(frozen=True, slots=True)
class LinkedInCapture:
    canonical_url: str
    company: str
    title: str
    location: str
    description: str
    job_identity_sha256: str
    capture_digest: str
    card_digest: str
    search_artifact_sha256: str
    search_receipt_sha256: str
    selected_artifact_sha256: str
    selected_receipt_sha256: str
    search_hands_commit: str
    selected_hands_commit: str


@dataclass(frozen=True, slots=True)
class DatabaseWrite:
    verdict: str
    records_written: int
    jobs_match_count: int
    row_digest: str
    verdict_is_null: bool
    score_is_null: bool
    applied_at_is_null: bool
    applications_before: int
    applications_after: int
    apply_runs_before: int
    apply_runs_after: int


def _expect_keys(value: Mapping[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise IntakeContractError(
            "source_receipt_invalid", f"{context} keys differ from contract"
        )


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise IntakeContractError(
            "source_receipt_invalid", f"{context} must be an object"
        )
    return value


def _nonempty_string(value: object, context: str, *, limit: int = 16384) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise IntakeContractError("source_artifact_invalid", f"{context} is invalid")
    return value


def _exact_int(value: object, context: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise IntakeContractError("source_receipt_invalid", f"{context} is invalid")
    return value


def _validate_lock(
    value: object,
    context: str,
    *,
    turn_lineage_sha256: str,
    correlation_id_sha256: str,
    deadline_seconds: int,
) -> None:
    lock = _mapping(value, context)
    _expect_keys(lock, _LOCK_KEYS, context)
    if (
        lock["policy"] != "careers"
        or lock["acquired"] is not True
        or lock["released"] is not True
    ):
        raise IntakeContractError(
            "source_receipt_invalid", f"{context} is not positively released"
        )
    validate_digest(lock["request_id"], f"{context} request ID")
    lock_lineage = validate_digest(
        lock["turn_lineage_sha256"], f"{context} turn lineage"
    )
    lock_correlation = validate_digest(
        lock["correlation_id_sha256"], f"{context} correlation"
    )
    owner_digest = lock["owner_token_sha256"]
    if owner_digest is not None:
        validate_digest(owner_digest, f"{context} owner token")
    _exact_int(lock["wait_ms"], f"{context} wait")
    lock_deadline = _exact_int(
        lock["deadline_seconds"], f"{context} deadline", minimum=1
    )
    if (
        lock_lineage != turn_lineage_sha256
        or lock_correlation != correlation_id_sha256
        or lock_deadline != deadline_seconds
    ):
        raise IntakeContractError(
            "source_receipt_invalid", f"{context} lineage differs"
        )


def _match_counts(value: object, context: str) -> Mapping[str, Any]:
    counts = _mapping(value, context)
    expected = {"structural_candidates", "valid_cards", "duplicate_cards"}
    _expect_keys(counts, expected, context)
    for key in expected:
        _exact_int(counts[key], f"{context} {key}")
    return counts


def _validate_search_receipt(
    receipt: Mapping[str, Any],
    artifact_sha256: str,
    artifact_card_count: int,
) -> None:
    _expect_keys(receipt, _SEARCH_RECEIPT_KEYS, "search receipt")
    if (
        receipt["schema"] != SEARCH_RECEIPT_SCHEMA
        or receipt["platform"] != "linkedin"
        or receipt["operation"] != "capture_mounted_job_search"
        or receipt["state"] not in {"captured", "already_captured"}
        or receipt["ok"] is not True
        or receipt["failure_code"] is not None
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "search receipt is not a successful terminal"
        )
    validate_git_commit(receipt["hands_commit"], "search Hands commit")
    transaction = validate_digest(receipt["transaction_sha256"], "search transaction")
    if transaction != validate_digest(
        receipt["expected_transaction_sha256"], "expected search transaction"
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "search transaction claim differs"
        )
    validate_digest(receipt["search_ref_sha256"], "search reference")
    validate_digest(receipt["sink_ref_sha256"], "search sink")
    lineage = validate_digest(receipt["turn_lineage_sha256"], "search lineage")
    correlation = validate_digest(
        receipt["correlation_id_sha256"], "search correlation"
    )
    deadline = _exact_int(receipt["deadline_seconds"], "search deadline", minimum=1)
    if _exact_int(receipt["stable_cycles_observed"], "search stable cycles") != 2:
        raise IntakeContractError(
            "source_receipt_invalid", "search stabilization is not exact"
        )
    _validate_lock(
        receipt["lock"],
        "search lock",
        turn_lineage_sha256=lineage,
        correlation_id_sha256=correlation,
        deadline_seconds=deadline,
    )
    pre_counts = _match_counts(receipt["pre_match_counts"], "search pre counts")
    if (
        pre_counts["valid_cards"] != artifact_card_count
        or pre_counts["structural_candidates"] != artifact_card_count
        or pre_counts["duplicate_cards"] != 0
        or receipt["pre_observation_sha256"] != artifact_sha256
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "search precondition differs from artifact"
        )
    action = _mapping(receipt["action"], "search action")
    _expect_keys(
        action,
        {
            "kind",
            "verdict",
            "batches_observed",
            "batches_written",
            "cards_observed",
            "content_digest",
        },
        "search action",
    )
    expected_written = 1 if receipt["state"] == "captured" else 0
    expected_verdict = "written" if expected_written else "already_present"
    if (
        action["kind"] != "private_sink_write_once"
        or action["verdict"] != expected_verdict
        or action["batches_observed"] != 1
        or action["batches_written"] != expected_written
        or action["cards_observed"] != artifact_card_count
        or action["content_digest"] != artifact_sha256
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "search action differs from artifact"
        )
    post = _mapping(receipt["postcondition"], "search postcondition")
    _expect_keys(
        post,
        {"kind", "verdict", "post_observation_sha256", "post_match_counts"},
        "search postcondition",
    )
    post_counts = _match_counts(post["post_match_counts"], "search post counts")
    if (
        post["kind"] != "mounted_job_card_set_digest_unchanged"
        or post["verdict"] != "satisfied"
        or post["post_observation_sha256"] != artifact_sha256
        or dict(post_counts) != dict(pre_counts)
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "search postcondition is not exact"
        )


def _validate_card(value: object, expected_ordinal: int) -> Mapping[str, Any]:
    card = _mapping(value, "search card")
    if set(card) != _CARD_KEYS:
        raise IntakeContractError(
            "source_artifact_invalid", "search card keys differ from contract"
        )
    if card["ordinal"] != expected_ordinal or isinstance(card["ordinal"], bool):
        raise IntakeContractError(
            "source_artifact_invalid", "search card ordinal is not exact"
        )
    target = _nonempty_string(card["target_card_name"], "target card name")
    title = _nonempty_string(card["detail_title_name"], "job title")
    company = _nonempty_string(card["detail_company_name"], "job company")
    location = _nonempty_string(card["location_text"], "job location")
    if not isinstance(card["showing"], bool):
        raise IntakeContractError(
            "source_artifact_invalid", "search card showing state is invalid"
        )
    digest_input = {
        "ordinal": expected_ordinal,
        "target_card_name": target,
        "detail_title_name": title,
        "detail_company_name": company,
        "location_text": location,
        "showing": card["showing"],
    }
    expected_digest = sha256_hex(canonical_json_bytes(digest_input))
    if card["card_digest"] != expected_digest:
        raise IntakeContractError(
            "source_artifact_invalid", "search card digest differs"
        )
    return card


def _validate_search_artifact(artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if set(artifact) != {"schema", "search_ref", "source_url", "cards"}:
        raise IntakeContractError(
            "source_artifact_invalid", "search artifact keys differ from contract"
        )
    if artifact["schema"] != SEARCH_ARTIFACT_SCHEMA:
        raise IntakeContractError(
            "source_artifact_invalid", "search artifact schema is unsupported"
        )
    _nonempty_string(artifact["search_ref"], "search reference", limit=4096)
    _validate_search_results_url(artifact["source_url"], require_job_id=False)
    cards_value = artifact["cards"]
    if not isinstance(cards_value, list) or not cards_value:
        raise IntakeContractError(
            "source_artifact_invalid", "search artifact contains no cards"
        )
    cards = [_validate_card(card, ordinal) for ordinal, card in enumerate(cards_value)]
    digests = [card["card_digest"] for card in cards]
    if len(digests) != len(set(digests)):
        raise IntakeContractError(
            "source_artifact_invalid", "search artifact contains duplicate cards"
        )
    return cards


def _validate_selected_receipt(
    receipt: Mapping[str, Any], artifact_sha256: str
) -> None:
    _expect_keys(receipt, _SELECTED_RECEIPT_KEYS, "selected receipt")
    if (
        receipt["schema"] != SELECTED_RECEIPT_SCHEMA
        or receipt["platform"] != "linkedin"
        or receipt["operation"] != "select_and_capture_job"
        or receipt["terminal_state"] not in {"captured", "already_captured"}
        or receipt["ok"] is not True
        or receipt["failure_code"] is not None
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "selected receipt is not a successful terminal"
        )
    validate_git_commit(receipt["hands_commit"], "selected Hands commit")
    transaction = validate_digest(receipt["transaction_sha256"], "selected transaction")
    if transaction != validate_digest(
        receipt["expected_transaction_sha256"], "expected selected transaction"
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "selected transaction claim differs"
        )
    for key in ("search_ref_sha256", "sink_ref_sha256"):
        validate_digest(receipt[key], f"selected {key}")
    lineage = validate_digest(receipt["turn_lineage_sha256"], "selected lineage")
    correlation = validate_digest(
        receipt["correlation_id_sha256"], "selected correlation"
    )
    deadline = _exact_int(receipt["deadline_seconds"], "selected deadline", minimum=1)
    _validate_lock(
        receipt["lock"],
        "selected lock",
        turn_lineage_sha256=lineage,
        correlation_id_sha256=correlation,
        deadline_seconds=deadline,
    )
    if receipt["pre_observation_sha256"] != artifact_sha256:
        raise IntakeContractError(
            "source_receipt_invalid", "selected pre-observation differs"
        )
    pre_counts = _mapping(receipt["pre_match_counts"], "selected pre counts")
    _expect_keys(
        pre_counts,
        {"about_job_heading", "selected_job_description_path"},
        "selected pre counts",
    )
    if any(pre_counts[key] != 1 for key in pre_counts):
        raise IntakeContractError(
            "source_receipt_invalid", "selected precondition is not exact"
        )
    selection = _mapping(receipt["selection"], "selected selection")
    selection_keys = {
        "kind",
        "verdict",
        "target_card_name_sha256",
        "detail_title_name_sha256",
        "detail_company_name_sha256",
        "target_match_count",
        "detail_title_match_count",
        "detail_company_match_count",
        "stable_cycles_observed",
        "action_name",
        "action_index",
        "action_match_count",
    }
    _expect_keys(selection, selection_keys, "selected selection")
    for key in (
        "target_card_name_sha256",
        "detail_title_name_sha256",
        "detail_company_name_sha256",
    ):
        validate_digest(selection[key], f"selection {key}")
    if (
        selection["kind"] != "private_exact_job_card_atspi_activate"
        or selection["verdict"] != "satisfied"
        or selection["target_match_count"] != 1
        or selection["detail_title_match_count"] != 1
        or selection["detail_company_match_count"] != 1
        or _exact_int(selection["stable_cycles_observed"], "selection stable cycles")
        != 2
        or selection["action_name"] != "click"
        or selection["action_index"] != 0
        or selection["action_match_count"] != 1
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "selected card action is not exact"
        )
    action = _mapping(receipt["action"], "selected action")
    _expect_keys(
        action,
        {"kind", "verdict", "records_observed", "records_written", "content_digest"},
        "selected action",
    )
    expected_written = 1 if receipt["terminal_state"] == "captured" else 0
    expected_verdict = "written" if expected_written else "already_present"
    if (
        action["kind"] != "private_sink_write_once"
        or action["verdict"] != expected_verdict
        or action["records_observed"] != 1
        or action["records_written"] != expected_written
        or action["content_digest"] != artifact_sha256
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "selected action differs from artifact"
        )
    post = _mapping(receipt["postcondition"], "selected postcondition")
    _expect_keys(
        post,
        {"kind", "verdict", "post_observation_sha256", "post_match_counts"},
        "selected postcondition",
    )
    if (
        post["kind"] != "selected_job_content_digest_unchanged"
        or post["verdict"] != "satisfied"
        or post["post_observation_sha256"] != artifact_sha256
        or dict(_mapping(post["post_match_counts"], "selected post counts"))
        != dict(pre_counts)
    ):
        raise IntakeContractError(
            "source_receipt_invalid", "selected postcondition is not exact"
        )


def _validate_search_results_url(value: object, *, require_job_id: bool) -> str | None:
    raw = _nonempty_string(value, "LinkedIn source URL", limit=4096)
    parsed = urlsplit(raw)
    normalized_path = parsed.path.rstrip("/") or "/"
    if (
        parsed.scheme != "https"
        or parsed.netloc != "www.linkedin.com"
        or normalized_path != "/jobs/search-results"
        or parsed.fragment
    ):
        raise IntakeContractError(
            "source_artifact_invalid", "LinkedIn source URL route is not exact"
        )
    if not require_job_id:
        return None
    values = [
        raw_value
        for component in parsed.query.split("&")
        for key, separator, raw_value in (component.partition("="),)
        if separator and key == "currentJobId"
    ]
    if len(values) != 1 or _JOB_ID_RE.fullmatch(values[0]) is None:
        raise IntakeContractError(
            "pair_mismatch", "selected capture has no exact current job identity"
        )
    job_id = values[0]
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


def _validate_selected_artifact(artifact: Mapping[str, Any]) -> tuple[str, str]:
    if set(artifact) != {
        "schema",
        "search_ref",
        "source_url",
        "detail_heading",
        "detail_text",
    }:
        raise IntakeContractError(
            "source_artifact_invalid", "selected artifact keys differ from contract"
        )
    if (
        artifact["schema"] != SELECTED_ARTIFACT_SCHEMA
        or artifact["detail_heading"] != "About the job"
    ):
        raise IntakeContractError(
            "source_artifact_invalid",
            "selected artifact schema or heading is unsupported",
        )
    _nonempty_string(artifact["search_ref"], "selected search reference", limit=4096)
    description = _nonempty_string(
        artifact["detail_text"], "selected job description", limit=15 * 1024 * 1024
    )
    canonical_url = _validate_search_results_url(
        artifact["source_url"], require_job_id=True
    )
    if canonical_url is None:
        raise IntakeContractError(
            "pair_mismatch", "selected capture identity is absent"
        )
    return canonical_url, description


def load_linkedin_capture(
    private_root: Path,
    transaction: Mapping[str, Any],
) -> LinkedInCapture:
    paths = {
        key: resolve_private_reference(private_root, transaction[key], key)
        for key in (
            "search_receipt_ref",
            "search_artifact_ref",
            "selected_receipt_ref",
            "selected_artifact_ref",
        )
    }
    search_artifact, search_artifact_bytes = read_private_json(
        paths["search_artifact_ref"], "search artifact"
    )
    search_receipt, search_receipt_bytes = read_private_json(
        paths["search_receipt_ref"], "search receipt"
    )
    selected_artifact, selected_artifact_bytes = read_private_json(
        paths["selected_artifact_ref"], "selected artifact"
    )
    selected_receipt, selected_receipt_bytes = read_private_json(
        paths["selected_receipt_ref"], "selected receipt"
    )
    search_artifact_sha256 = sha256_hex(search_artifact_bytes)
    selected_artifact_sha256 = sha256_hex(selected_artifact_bytes)
    cards = _validate_search_artifact(search_artifact)
    _validate_search_receipt(search_receipt, search_artifact_sha256, len(cards))
    _validate_selected_receipt(selected_receipt, selected_artifact_sha256)
    canonical_url, description = _validate_selected_artifact(selected_artifact)
    card_digest = validate_digest(transaction["card_digest"], "card digest")
    matches = [card for card in cards if card["card_digest"] == card_digest]
    if len(matches) != 1:
        raise IntakeContractError("pair_mismatch", "frozen card identity is not exact")
    card = matches[0]
    selection = _mapping(selected_receipt["selection"], "selected selection")
    identity_pairs = (
        (card["target_card_name"], selection["target_card_name_sha256"]),
        (card["detail_title_name"], selection["detail_title_name_sha256"]),
        (card["detail_company_name"], selection["detail_company_name_sha256"]),
    )
    if any(
        sha256_hex(value.encode("utf-8")) != digest for value, digest in identity_pairs
    ):
        raise IntakeContractError(
            "pair_mismatch", "selected receipt does not bind the frozen card"
        )
    search_ref = str(search_artifact["search_ref"])
    if search_ref != selected_artifact["search_ref"]:
        raise IntakeContractError("pair_mismatch", "capture search references differ")
    search_ref_sha256 = sha256_hex(search_ref.encode("utf-8"))
    if (
        search_ref_sha256 != search_receipt["search_ref_sha256"]
        or search_ref_sha256 != selected_receipt["search_ref_sha256"]
    ):
        raise IntakeContractError(
            "pair_mismatch", "capture receipt search references differ"
        )
    capture_fields = {
        "url": canonical_url,
        "source": "linkedin:ui",
        "company": card["detail_company_name"],
        "title": card["detail_title_name"],
        "location": card["location_text"],
        "workplace": None,
        "description": description,
        "posted": None,
        "posted_raw": None,
        "posted_source": None,
    }
    return LinkedInCapture(
        canonical_url=canonical_url,
        company=str(card["detail_company_name"]),
        title=str(card["detail_title_name"]),
        location=str(card["location_text"]),
        description=description,
        job_identity_sha256=sha256_hex(canonical_url.encode("utf-8")),
        capture_digest=sha256_hex(canonical_json_bytes(capture_fields)),
        card_digest=card_digest,
        search_artifact_sha256=search_artifact_sha256,
        search_receipt_sha256=sha256_hex(search_receipt_bytes),
        selected_artifact_sha256=selected_artifact_sha256,
        selected_receipt_sha256=sha256_hex(selected_receipt_bytes),
        search_hands_commit=str(search_receipt["hands_commit"]),
        selected_hands_commit=str(selected_receipt["hands_commit"]),
    )


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _validate_jobs_table(connection: sqlite3.Connection) -> None:
    table_info = connection.execute("PRAGMA table_info(jobs)").fetchall()
    columns = {str(row[1]): str(row[2]).strip().upper() for row in table_info}
    if any(
        columns.get(column) != expected_type
        for column, expected_type in _REQUIRED_JOB_COLUMN_TYPES.items()
    ):
        raise IntakeContractError(
            "database_contract_invalid", "jobs table column contract is incomplete"
        )
    primary_key_columns = [
        str(row[1])
        for row in sorted(table_info, key=lambda row: int(row[5]))
        if int(row[5]) > 0
    ]
    url_is_unique = primary_key_columns == ["url"]
    if not url_is_unique:
        for index in connection.execute("PRAGMA index_list(jobs)").fetchall():
            if int(index[2]) != 1 or (len(index) > 4 and int(index[4]) == 1):
                continue
            index_columns = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM pragma_index_info(?) ORDER BY seqno",
                    (str(index[1]),),
                )
            ]
            if index_columns == ["url"]:
                url_is_unique = True
                break
    if not url_is_unique:
        raise IntakeContractError(
            "database_contract_invalid", "jobs URL identity is not unique"
        )


def _capture_values(capture: LinkedInCapture) -> tuple[Any, ...]:
    return (
        capture.canonical_url,
        "linkedin:ui",
        capture.company,
        capture.title,
        capture.location,
        None,
        capture.description,
        None,
        None,
        None,
    )


def _row_digest(row: tuple[Any, ...]) -> str:
    return sha256_hex(
        canonical_json_bytes(dict(zip(_CAPTURE_COLUMNS, row, strict=True)))
    )


def persist_capture(database: Path, capture: LinkedInCapture) -> DatabaseWrite:
    connection = sqlite3.connect(str(database), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        _validate_jobs_table(connection)
        for table in ("applications", "apply_runs"):
            if not _table_columns(connection, table):
                raise IntakeContractError(
                    "database_contract_invalid", "application-state table is absent"
                )
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='jobs'"
        ).fetchall()
        if triggers:
            raise IntakeContractError(
                "database_contract_invalid", "jobs table has write triggers"
            )
        applications_before = int(
            connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        )
        apply_runs_before = int(
            connection.execute("SELECT COUNT(*) FROM apply_runs").fetchone()[0]
        )
        expected_values = _capture_values(capture)
        first_seen = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        connection.execute(
            "INSERT OR IGNORE INTO jobs(url,source,company,title,location,workplace,description,posted,posted_raw,"
            "posted_source,first_seen,verdict,kill_reason,detail,applied_at,score) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL)",
            (*expected_values, first_seen),
        )
        records_written = int(connection.execute("SELECT changes()").fetchone()[0])
        if records_written not in {0, 1}:
            raise IntakeContractError(
                "database_write_indeterminate", "database effect count is invalid"
            )
        matching_rows = connection.execute(
            "SELECT url,source,company,title,location,workplace,description,posted,posted_raw,posted_source,"
            "verdict,score,applied_at FROM jobs WHERE url=?",
            (capture.canonical_url,),
        ).fetchall()
        jobs_match_count = len(matching_rows)
        if jobs_match_count != 1:
            raise IntakeContractError(
                "database_write_indeterminate", "job URL identity is not exact"
            )
        after_row = matching_rows[0]
        if tuple(after_row[: len(_CAPTURE_COLUMNS)]) != expected_values:
            failure_code = (
                "existing_row_conflict"
                if records_written == 0
                else "database_write_indeterminate"
            )
            raise IntakeContractError(failure_code, "job row differs from capture")
        applications_after = int(
            connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0]
        )
        apply_runs_after = int(
            connection.execute("SELECT COUNT(*) FROM apply_runs").fetchone()[0]
        )
        if (
            applications_after != applications_before
            or apply_runs_after != apply_runs_before
        ):
            raise IntakeContractError(
                "database_write_indeterminate",
                "application state changed during intake",
            )
        verdict_is_null = after_row[-3] is None
        score_is_null = after_row[-2] is None
        applied_at_is_null = after_row[-1] is None
        if records_written == 1 and not all(
            (verdict_is_null, score_is_null, applied_at_is_null)
        ):
            raise IntakeContractError(
                "database_write_indeterminate", "new intake row is not unclassified"
            )
        connection.execute("COMMIT")
        return DatabaseWrite(
            verdict="written" if records_written else "already_present",
            records_written=records_written,
            jobs_match_count=jobs_match_count,
            row_digest=_row_digest(tuple(after_row[: len(_CAPTURE_COLUMNS)])),
            verdict_is_null=verdict_is_null,
            score_is_null=score_is_null,
            applied_at_is_null=applied_at_is_null,
            applications_before=applications_before,
            applications_after=applications_after,
            apply_runs_before=apply_runs_before,
            apply_runs_after=apply_runs_after,
        )
    except IntakeContractError:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise IntakeContractError(
            "database_write_indeterminate", "database operation failed"
        ) from exc
    finally:
        connection.close()


def finalize_success(
    *,
    receipt_path: Path,
    requester: str,
    transaction_sha256: str,
    turn_lineage_sha256: str,
    correlation_id_sha256: str,
    capture: LinkedInCapture,
    write: DatabaseWrite,
) -> dict[str, Any]:
    state = "captured_unclassified" if write.records_written else "already_present"
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "operation": OPERATION,
        "connector_version": __version__,
        "requester": requester,
        "turn_lineage_sha256": turn_lineage_sha256,
        "correlation_id_sha256": correlation_id_sha256,
        "transaction_sha256": transaction_sha256,
        "state": state,
        "ok": True,
        "failure_code": None,
        "sources": {
            "search_artifact_sha256": capture.search_artifact_sha256,
            "search_receipt_sha256": capture.search_receipt_sha256,
            "selected_artifact_sha256": capture.selected_artifact_sha256,
            "selected_receipt_sha256": capture.selected_receipt_sha256,
            "search_hands_commit": capture.search_hands_commit,
            "selected_hands_commit": capture.selected_hands_commit,
            "card_digest": capture.card_digest,
        },
        "pairing": {
            "card_match_count": 1,
            "selection_identity_match_count": 3,
            "search_reference_match_count": 4,
            "current_job_id_match_count": 1,
            "job_identity_sha256": capture.job_identity_sha256,
            "capture_digest": capture.capture_digest,
        },
        "action": {
            "kind": "sqlite_insert_once",
            "verdict": write.verdict,
            "records_observed": 1,
            "records_written": write.records_written,
            "row_digest": write.row_digest,
        },
        "postcondition": {
            "kind": "exact_capture_row_present",
            "verdict": "satisfied",
            "jobs_match_count": write.jobs_match_count,
            "verdict_is_null": write.verdict_is_null,
            "score_is_null": write.score_is_null,
            "applied_at_is_null": write.applied_at_is_null,
            "applications_before": write.applications_before,
            "applications_after": write.applications_after,
            "apply_runs_before": write.apply_runs_before,
            "apply_runs_after": write.apply_runs_after,
        },
    }
    receipt_bytes = write_new_private_json(receipt_path, receipt)
    return {
        "schema": RESULT_SCHEMA,
        "ok": True,
        "state": state,
        "failure_code": None,
        "records_observed": 1,
        "records_written": write.records_written,
        "job_identity_sha256": capture.job_identity_sha256,
        "row_digest": write.row_digest,
        "receipt_sha256": sha256_hex(receipt_bytes),
        "turn_lineage_sha256": turn_lineage_sha256,
    }
