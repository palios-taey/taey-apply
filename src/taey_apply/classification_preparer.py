from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import sqlite3
import stat
from types import ModuleType
from typing import Any, Mapping, Sequence

from .classification_contract import (
    CLAIM_SCHEMA,
    OPERATION,
    ClassificationClaim,
    ClassificationContractError,
    _validate_claim,
    read_classification_claim,
)
from .contract import (
    IntakeContractError,
    _read_owned_file,
    canonical_json_bytes,
    read_private_json,
    resolve_private_reference,
    sha256_hex,
    validate_database_path,
    validate_digest,
    validate_new_receipt_path,
    validate_private_root,
)
from .linkedin_classification import (
    _digestable_sqlite_value,
    _jobs_columns,
    load_qualified_intake,
    row_sha256,
    stable_row_sha256,
)
from .linkedin_intake import _validate_jobs_table


MANIFEST_SCHEMA = "taey_apply_linkedin_classification_preparation_manifest_v1"
MANIFEST_OPERATION = "prepare_frozen_linkedin_classification_claim"
POLICY_SCHEMA = "taey_private_classification_policy_v1"
PRIORITY_BOARDS_SCHEMA = "taey_private_classification_priority_boards_v1"
POLICY_INPUT_SCHEMA = "taey_private_classification_policy_input_v1"
RESULT_SCHEMA = "taey_apply_linkedin_classification_preparation_result_v1"
REFUSAL_SCHEMA = "taey_apply_linkedin_classification_preparation_refusal_v1"
TERMINAL_VERDICTS = frozenset({"PASS", "KILLED"})

_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "operation",
        "intake_transaction_ref",
        "intake_transaction_sha256",
        "intake_receipt_ref",
        "intake_receipt_sha256",
        "policy_artifact_ref",
        "policy_artifact_sha256",
        "classifier_ref",
        "classifier_sha256",
        "priority_boards_artifact_ref",
        "priority_boards_artifact_sha256",
        "claim_ref",
        "refusal_ref",
    }
)
_DIGEST_KEYS = (
    "intake_transaction_sha256",
    "intake_receipt_sha256",
    "policy_artifact_sha256",
    "classifier_sha256",
    "priority_boards_artifact_sha256",
)
_NULL_COLUMNS = ("verdict", "kill_reason", "detail", "score", "applied_at")
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


class ClassificationPreparationError(RuntimeError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class PreparationIdentity:
    private_root: Path
    manifest: Mapping[str, Any]
    manifest_sha256: str
    claim_path: Path
    refusal_path: Path


@dataclass(frozen=True, slots=True)
class ReadOnlySnapshot:
    columns: tuple[str, ...]
    row: tuple[object, ...]
    counts: tuple[int, int, int]
    total_changes: int


def _failure(
    exc: BaseException,
    code: str = "PREPARATION_REFUSED",
) -> ClassificationPreparationError:
    if isinstance(exc, ClassificationPreparationError):
        return exc
    if isinstance(exc, ClassificationContractError):
        return ClassificationPreparationError(exc.failure_code, "preparation stopped")
    if isinstance(exc, IntakeContractError):
        return ClassificationPreparationError(code, "preparation contract is invalid")
    return ClassificationPreparationError(code, "preparation stopped")


def _path_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ClassificationPreparationError(
            "PREPARATION_WRITE_INDETERMINATE", "identity state is unavailable"
        ) from exc
    return True


def _validate_directory(path: Path, context: str) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ClassificationPreparationError(
            "RUNTIME_CONTRACT_INVALID", f"{context} is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise ClassificationPreparationError(
            "RUNTIME_CONTRACT_INVALID",
            f"{context} must be owner-controlled 0700",
        )


def _fsync_directory(path: Path, context: str) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        os.fsync(descriptor)
    except OSError as exc:
        raise ClassificationPreparationError(
            "PREPARATION_WRITE_INDETERMINATE", f"{context} could not be synced"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_frozen_json(
    path: Path,
    value: Mapping[str, Any],
    context: str,
) -> tuple[bytes, str]:
    raw_bytes = canonical_json_bytes(dict(value))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o400)
        created = True
        offset = 0
        while offset < len(raw_bytes):
            written = os.write(descriptor, raw_bytes[offset:])
            if written <= 0:
                raise OSError("write did not advance")
            offset += written
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise ClassificationPreparationError(
            "IDENTITY_SPENT", f"{context} identity already exists"
        ) from exc
    except OSError as exc:
        code = "PREPARATION_WRITE_INDETERMINATE" if created else "PREPARATION_REFUSED"
        raise ClassificationPreparationError(code, f"{context} was not proven") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    _fsync_directory(path.parent, f"{context} parent")
    try:
        readback_value, readback_bytes = read_private_json(path, context)
    except IntakeContractError as exc:
        raise ClassificationPreparationError(
            "PREPARATION_WRITE_INDETERMINATE", f"{context} readback failed"
        ) from exc
    if readback_bytes != raw_bytes or dict(readback_value) != dict(value):
        raise ClassificationPreparationError(
            "PREPARATION_WRITE_INDETERMINATE", f"{context} readback differs"
        )
    return raw_bytes, sha256_hex(raw_bytes)


def _validated_manifest(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _MANIFEST_KEYS:
        raise ClassificationPreparationError(
            "MANIFEST_INVALID", "manifest fields are incomplete or unknown"
        )
    if value["schema"] != MANIFEST_SCHEMA or value["operation"] != MANIFEST_OPERATION:
        raise ClassificationPreparationError(
            "MANIFEST_INVALID", "manifest schema or operation is unsupported"
        )
    try:
        for key in _DIGEST_KEYS:
            validate_digest(value[key], key)
    except IntakeContractError as exc:
        raise ClassificationPreparationError(
            "MANIFEST_INVALID", "manifest digest is invalid"
        ) from exc
    return value


def _accept_identity(
    private_root_value: str | os.PathLike[str],
    manifest_path_value: str | os.PathLike[str],
    expected_manifest_sha256: object,
) -> PreparationIdentity:
    try:
        private_root = validate_private_root(private_root_value)
        expected_digest = validate_digest(
            expected_manifest_sha256, "expected manifest digest"
        )
        manifest_path = Path(manifest_path_value)
        if not manifest_path.is_absolute() or os.path.normpath(str(manifest_path)) != str(
            manifest_path
        ):
            raise IntakeContractError(
                "private_input_invalid", "manifest path must be canonical and absolute"
            )
        manifest_relative = manifest_path.relative_to(private_root)
        manifest_resolved = resolve_private_reference(
            private_root, manifest_relative.as_posix(), "classification manifest"
        )
        manifest_value, manifest_bytes = read_private_json(
            manifest_resolved, "classification manifest"
        )
    except (IntakeContractError, ValueError) as exc:
        raise ClassificationPreparationError(
            "RUNTIME_CONTRACT_INVALID", "manifest runtime contract is invalid"
        ) from exc
    manifest_sha256 = sha256_hex(manifest_bytes)
    if manifest_sha256 != expected_digest:
        raise ClassificationPreparationError(
            "MANIFEST_DIGEST_MISMATCH", "manifest digest differs"
        )
    manifest = _validated_manifest(manifest_value)
    claim_ref = manifest["claim_ref"]
    refusal_ref = manifest["refusal_ref"]
    if (
        not isinstance(claim_ref, str)
        or not claim_ref.startswith("classification/")
        or not isinstance(refusal_ref, str)
        or not refusal_ref.startswith("classification-preparation-refusals/")
    ):
        raise ClassificationPreparationError(
            "IDENTITY_INVALID", "preparation identity namespace is invalid"
        )
    try:
        claim_path = resolve_private_reference(
            private_root, claim_ref, "classification claim", must_exist=False
        )
        refusal_path = resolve_private_reference(
            private_root,
            refusal_ref,
            "classification preparation refusal",
            must_exist=False,
        )
        claim_path = validate_new_receipt_path(claim_path, private_root)
        refusal_path = validate_new_receipt_path(refusal_path, private_root)
    except IntakeContractError as exc:
        raise ClassificationPreparationError(
            "IDENTITY_INVALID", "preparation identity is invalid"
        ) from exc
    if claim_path == refusal_path:
        raise ClassificationPreparationError(
            "IDENTITY_INVALID", "claim and refusal identities must differ"
        )
    try:
        attempts_path = resolve_private_reference(
            private_root, "classification-attempts", "classification attempts"
        )
    except IntakeContractError as exc:
        raise ClassificationPreparationError(
            "RUNTIME_CONTRACT_INVALID", "classification attempts are unavailable"
        ) from exc
    _validate_directory(attempts_path, "classification attempts")
    return PreparationIdentity(
        private_root=private_root,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        claim_path=claim_path,
        refusal_path=refusal_path,
    )


def _read_bound_json(
    identity: PreparationIdentity,
    reference_key: str,
    digest_key: str,
    context: str,
) -> tuple[Mapping[str, Any], bytes]:
    try:
        path = resolve_private_reference(
            identity.private_root, identity.manifest[reference_key], context
        )
        value, raw_bytes = read_private_json(path, context)
    except IntakeContractError as exc:
        raise ClassificationPreparationError(
            "PRIVATE_ARTIFACT_INVALID", f"{context} is invalid"
        ) from exc
    if sha256_hex(raw_bytes) != identity.manifest[digest_key]:
        raise ClassificationPreparationError(
            "PRIVATE_ARTIFACT_DIGEST_MISMATCH", f"{context} digest differs"
        )
    return value, raw_bytes


def _read_classifier(identity: PreparationIdentity) -> bytes:
    try:
        path = resolve_private_reference(
            identity.private_root, identity.manifest["classifier_ref"], "classifier"
        )
        raw_bytes = _read_owned_file(path, 0o400, "classifier")
    except IntakeContractError as exc:
        raise ClassificationPreparationError(
            "PRIVATE_ARTIFACT_INVALID", "classifier is invalid"
        ) from exc
    if sha256_hex(raw_bytes) != identity.manifest["classifier_sha256"]:
        raise ClassificationPreparationError(
            "PRIVATE_ARTIFACT_DIGEST_MISMATCH", "classifier digest differs"
        )
    return raw_bytes


def _policy_revision(value: Mapping[str, Any]) -> int:
    if set(value) != {"schema", "filter_rev"} or value["schema"] != POLICY_SCHEMA:
        raise ClassificationPreparationError(
            "PRIVATE_POLICY_INVALID", "policy artifact contract differs"
        )
    revision = value["filter_rev"]
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise ClassificationPreparationError(
            "PRIVATE_POLICY_INVALID", "policy revision is invalid"
        )
    return revision


def _priority_boards(value: Mapping[str, Any]) -> list[list[str]]:
    if (
        set(value) != {"schema", "priority_boards"}
        or value["schema"] != PRIORITY_BOARDS_SCHEMA
        or not isinstance(value["priority_boards"], list)
    ):
        raise ClassificationPreparationError(
            "PRIVATE_POLICY_INVALID", "priority boards artifact contract differs"
        )
    boards: list[list[str]] = []
    for row in value["priority_boards"]:
        if (
            not isinstance(row, list)
            or len(row) != 3
            or not all(isinstance(item, str) and item for item in row)
        ):
            raise ClassificationPreparationError(
                "PRIVATE_POLICY_INVALID", "priority boards row is invalid"
            )
        boards.append(list(row))
    return boards


def _load_classifier(
    raw_bytes: bytes,
    filter_rev: int,
    priority_boards: Sequence[Sequence[str]],
) -> Any:
    module = ModuleType("taey_private_classification_policy")
    module.__file__ = "<pinned-private-classifier>"
    module.__dict__["PRIORITY_BOARDS"] = [list(row) for row in priority_boards]
    try:
        code = compile(raw_bytes, module.__file__, "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except (ImportError, OSError, RuntimeError, SyntaxError, TypeError, ValueError) as exc:
        raise ClassificationPreparationError(
            "PRIVATE_CLASSIFIER_INVALID", "classifier could not be loaded"
        ) from exc
    classify = module.__dict__.get("classify")
    if module.__dict__.get("FILTER_REV") != filter_rev or not callable(classify):
        raise ClassificationPreparationError(
            "PRIVATE_CLASSIFIER_INVALID", "classifier contract differs"
        )
    return classify


def _quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _read_snapshot(database: Path, canonical_url: str) -> ReadOnlySnapshot:
    connection = sqlite3.connect(
        f"{database.as_uri()}?mode=ro", uri=True, timeout=30, isolation_level=None
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        try:
            _validate_jobs_table(connection)
        except IntakeContractError as exc:
            raise ClassificationPreparationError(
                "DATABASE_CONTRACT_INVALID", "jobs table contract differs"
            ) from exc
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='jobs'"
        ).fetchall()
        if triggers:
            raise ClassificationPreparationError(
                "DATABASE_CONTRACT_INVALID", "jobs table has write triggers"
            )
        columns = tuple(_jobs_columns(connection))
        required = set(_CAPTURE_COLUMNS) | set(_NULL_COLUMNS)
        if not required.issubset(columns):
            raise ClassificationPreparationError(
                "DATABASE_CONTRACT_INVALID", "jobs table contract differs"
            )
        projection = ",".join(_quoted(column) for column in columns)
        rows = connection.execute(
            f"SELECT {projection} FROM jobs WHERE url=?", (canonical_url,)
        ).fetchall()
        if len(rows) != 1:
            raise ClassificationPreparationError(
                "ROW_IDENTITY_MISMATCH", "job identity is not exact"
            )
        counts = tuple(
            int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {_quoted(table)}"
                ).fetchone()[0]
            )
            for table in ("jobs", "applications", "apply_runs")
        )
        return ReadOnlySnapshot(
            columns=columns,
            row=tuple(rows[0]),
            counts=(counts[0], counts[1], counts[2]),
            total_changes=connection.total_changes,
        )
    except sqlite3.Error as exc:
        raise ClassificationPreparationError(
            "DATABASE_OPERATION_FAILED", "read-only database observation failed"
        ) from exc
    finally:
        connection.close()


def _validate_pristine_snapshot(snapshot: ReadOnlySnapshot, qualified: Any) -> dict[str, object]:
    job = dict(zip(snapshot.columns, snapshot.row, strict=True))
    expected_capture = {
        "url": qualified.capture.canonical_url,
        "source": "linkedin:ui",
        "company": qualified.capture.company,
        "title": qualified.capture.title,
        "location": qualified.capture.location,
        "workplace": None,
        "description": qualified.capture.description,
        "posted": None,
        "posted_raw": None,
        "posted_source": None,
    }
    if any(job[key] != value for key, value in expected_capture.items()):
        raise ClassificationPreparationError(
            "ROW_DIGEST_MISMATCH", "target row differs from frozen capture"
        )
    if any(job[key] is not None for key in _NULL_COLUMNS):
        raise ClassificationPreparationError(
            "PRECONDITION_MISMATCH", "target row is not pristine and unclassified"
        )
    if qualified.capture.job_identity_sha256 != sha256_hex(
        str(job["url"]).encode("utf-8")
    ):
        raise ClassificationPreparationError(
            "ROW_IDENTITY_MISMATCH", "target job identity digest differs"
        )
    if snapshot.total_changes != 0:
        raise ClassificationPreparationError(
            "SIDE_EFFECT_UNCERTAIN", "read-only database observation changed state"
        )
    return job


def _snapshot_sha256(snapshot: ReadOnlySnapshot) -> str:
    return sha256_hex(
        canonical_json_bytes(
            {
                "columns": list(snapshot.columns),
                "counts": list(snapshot.counts),
                "row_sha256": row_sha256(snapshot.columns, snapshot.row),
                "stable_row_sha256": stable_row_sha256(
                    snapshot.columns, snapshot.row
                ),
                "total_changes": snapshot.total_changes,
            }
        )
    )


def _qualified_intake(identity: PreparationIdentity) -> Any:
    manifest = identity.manifest
    placeholder = ClassificationClaim(
        intake_transaction_ref=str(manifest["intake_transaction_ref"]),
        intake_transaction_sha256=str(manifest["intake_transaction_sha256"]),
        intake_receipt_ref=str(manifest["intake_receipt_ref"]),
        intake_receipt_sha256=str(manifest["intake_receipt_sha256"]),
        prewrite_row_sha256="0" * 64,
        stable_row_sha256="0" * 64,
        policy_input_sha256="0" * 64,
        classifier_sha256="0" * 64,
        verdict="PASS",
    )
    return load_qualified_intake(identity.private_root, placeholder)


def _write_refusal(
    identity: PreparationIdentity,
    failure_code: str,
) -> None:
    marker = {
        "schema": REFUSAL_SCHEMA,
        "operation": MANIFEST_OPERATION,
        "ok": False,
        "state": "preparation_refused",
        "failure_code": failure_code,
        "manifest_sha256": identity.manifest_sha256,
        "claim_identity_sha256": sha256_hex(
            str(identity.manifest["claim_ref"]).encode("utf-8")
        ),
    }
    _write_frozen_json(identity.refusal_path, marker, "classification preparation refusal")


def _prepare(
    identity: PreparationIdentity,
    database: Path,
) -> dict[str, object]:
    manifest = identity.manifest
    policy, policy_bytes = _read_bound_json(
        identity, "policy_artifact_ref", "policy_artifact_sha256", "policy artifact"
    )
    priority_value, priority_bytes = _read_bound_json(
        identity,
        "priority_boards_artifact_ref",
        "priority_boards_artifact_sha256",
        "priority boards artifact",
    )
    classifier_bytes = _read_classifier(identity)
    filter_rev = _policy_revision(policy)
    priority_boards = _priority_boards(priority_value)
    classifier = _load_classifier(classifier_bytes, filter_rev, priority_boards)
    qualified = _qualified_intake(identity)
    before = _read_snapshot(database, qualified.capture.canonical_url)
    job = _validate_pristine_snapshot(before, qualified)
    prewrite_sha256 = row_sha256(before.columns, before.row)
    stable_sha256 = stable_row_sha256(before.columns, before.row)
    classifier_sha256 = sha256_hex(classifier_bytes)
    priority_boards_sha256 = sha256_hex(canonical_json_bytes(priority_boards))
    digestable_job = {
        key: _digestable_sqlite_value(value) for key, value in job.items()
    }
    policy_input_sha256 = sha256_hex(
        canonical_json_bytes(
            {
                "schema": POLICY_INPUT_SCHEMA,
                "classifier_sha256": classifier_sha256,
                "filter_rev": filter_rev,
                "job": digestable_job,
                "priority_boards_sha256": priority_boards_sha256,
            }
        )
    )
    decision = classifier(job)
    if (
        not isinstance(decision, tuple)
        or len(decision) != 3
        or decision[0] not in TERMINAL_VERDICTS
        or not isinstance(decision[1], str)
        or not isinstance(decision[2], str)
    ):
        raise ClassificationPreparationError(
            "PRIVATE_CLASSIFIER_INVALID", "classifier decision contract differs"
        )
    verdict = str(decision[0])
    del decision
    claim_value = {
        "schema": CLAIM_SCHEMA,
        "operation": OPERATION,
        "intake_transaction_ref": manifest["intake_transaction_ref"],
        "intake_transaction_sha256": manifest["intake_transaction_sha256"],
        "intake_receipt_ref": manifest["intake_receipt_ref"],
        "intake_receipt_sha256": manifest["intake_receipt_sha256"],
        "prewrite_row_sha256": prewrite_sha256,
        "stable_row_sha256": stable_sha256,
        "policy_input_sha256": policy_input_sha256,
        "classifier_sha256": classifier_sha256,
        "verdict": verdict,
    }
    _validate_claim(claim_value)
    claim_sha256 = sha256_hex(canonical_json_bytes(claim_value))
    attempt_path = (
        identity.private_root
        / "classification-attempts"
        / f"{claim_sha256}.json"
    )
    if _path_exists(attempt_path):
        raise ClassificationPreparationError(
            "REPLAY_REJECTED", "classification claim was already attempted"
        )
    claim_bytes, claim_sha256 = _write_frozen_json(
        identity.claim_path, claim_value, "classification claim"
    )
    read_claim, read_claim_sha256 = read_classification_claim(
        identity.private_root, identity.claim_path, claim_sha256
    )
    requalified = load_qualified_intake(identity.private_root, read_claim)
    after = _read_snapshot(database, requalified.capture.canonical_url)
    _validate_pristine_snapshot(after, requalified)
    if (
        read_claim_sha256 != claim_sha256
        or canonical_json_bytes(claim_value) != claim_bytes
        or before != after
        or _path_exists(attempt_path)
        or _path_exists(identity.refusal_path)
    ):
        raise ClassificationPreparationError(
            "SIDE_EFFECT_UNCERTAIN", "claim preparation postcondition differs"
        )
    return {
        "schema": RESULT_SCHEMA,
        "operation": MANIFEST_OPERATION,
        "ok": True,
        "state": "claim_prepared",
        "digests": {
            "manifest_sha256": identity.manifest_sha256,
            "claim_sha256": claim_sha256,
            "intake_transaction_sha256": manifest["intake_transaction_sha256"],
            "intake_receipt_sha256": manifest["intake_receipt_sha256"],
            "policy_artifact_sha256": sha256_hex(policy_bytes),
            "classifier_sha256": classifier_sha256,
            "priority_boards_artifact_sha256": sha256_hex(priority_bytes),
            "priority_boards_sha256": priority_boards_sha256,
            "policy_input_sha256": policy_input_sha256,
            "prewrite_row_sha256": prewrite_sha256,
            "stable_row_sha256": stable_sha256,
            "database_snapshot_sha256": _snapshot_sha256(before),
        },
    }


def prepare_classification_claim(
    *,
    private_root_value: str | os.PathLike[str],
    database_path_value: str | os.PathLike[str],
    manifest_path_value: str | os.PathLike[str],
    expected_manifest_sha256: object,
) -> dict[str, object]:
    identity = _accept_identity(
        private_root_value, manifest_path_value, expected_manifest_sha256
    )
    try:
        database = validate_database_path(database_path_value)
        return _prepare(identity, database)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
            raise
        failure = _failure(exc)
        if not _path_exists(identity.claim_path) and not _path_exists(
            identity.refusal_path
        ):
            try:
                _write_refusal(identity, failure.failure_code)
            except ClassificationPreparationError as marker_error:
                raise ClassificationPreparationError(
                    "PREPARATION_WRITE_INDETERMINATE",
                    "preparation refusal was not proven",
                ) from marker_error
        raise failure from exc
