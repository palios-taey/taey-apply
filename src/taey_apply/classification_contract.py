from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
import stat
from typing import Mapping

from .contract import (
    IntakeContractError,
    canonical_json_bytes,
    read_private_json,
    resolve_private_reference,
    sha256_hex,
    validate_digest,
)


CLAIM_SCHEMA = "taey_apply_linkedin_classification_claim_v1"
ATTEMPT_SCHEMA = "taey_apply_linkedin_classification_attempt_v1"
RECEIPT_SCHEMA = "taey_apply_linkedin_classification_receipt_v1"
RESULT_SCHEMA = "taey_apply_linkedin_classification_result_v1"
OPERATION = "classify_frozen_linkedin_intake"
TERMINAL_VERDICTS = frozenset({"PASS", "KILLED"})

CLAIM_KEYS = frozenset(
    {
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
)


class ClassificationContractError(RuntimeError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class ClassificationClaim:
    intake_transaction_ref: str
    intake_transaction_sha256: str
    intake_receipt_ref: str
    intake_receipt_sha256: str
    prewrite_row_sha256: str
    stable_row_sha256: str
    policy_input_sha256: str
    classifier_sha256: str
    verdict: str


def _translate_intake_error(exc: IntakeContractError) -> ClassificationContractError:
    return ClassificationContractError("CLAIM_INVALID", "claim contract is invalid")


def _validate_claim(value: object) -> ClassificationClaim:
    if not isinstance(value, Mapping) or set(value) != CLAIM_KEYS:
        raise ClassificationContractError(
            "CLAIM_INVALID", "claim fields are incomplete or unknown"
        )
    if value["schema"] != CLAIM_SCHEMA or value["operation"] != OPERATION:
        raise ClassificationContractError(
            "CLAIM_INVALID", "claim schema or operation is unsupported"
        )
    try:
        intake_transaction_sha256 = validate_digest(
            value["intake_transaction_sha256"], "intake transaction digest"
        )
        intake_receipt_sha256 = validate_digest(
            value["intake_receipt_sha256"], "intake receipt digest"
        )
        prewrite_row_sha256 = validate_digest(
            value["prewrite_row_sha256"], "prewrite row digest"
        )
        stable_row_sha256 = validate_digest(
            value["stable_row_sha256"], "stable row digest"
        )
        policy_input_sha256 = validate_digest(
            value["policy_input_sha256"], "policy input digest"
        )
        classifier_sha256 = validate_digest(
            value["classifier_sha256"], "classifier digest"
        )
    except IntakeContractError as exc:
        raise _translate_intake_error(exc) from exc
    verdict = value["verdict"]
    if verdict not in TERMINAL_VERDICTS:
        raise ClassificationContractError(
            "CLAIM_INVALID", "claim verdict is unsupported"
        )
    for key in ("intake_transaction_ref", "intake_receipt_ref"):
        reference = value[key]
        if not isinstance(reference, str):
            raise ClassificationContractError(
                "CLAIM_INVALID", "claim reference is invalid"
            )
    return ClassificationClaim(
        intake_transaction_ref=str(value["intake_transaction_ref"]),
        intake_transaction_sha256=intake_transaction_sha256,
        intake_receipt_ref=str(value["intake_receipt_ref"]),
        intake_receipt_sha256=intake_receipt_sha256,
        prewrite_row_sha256=prewrite_row_sha256,
        stable_row_sha256=stable_row_sha256,
        policy_input_sha256=policy_input_sha256,
        classifier_sha256=classifier_sha256,
        verdict=str(verdict),
    )


def read_classification_claim(
    private_root: Path,
    path_value: str | os.PathLike[str],
    expected_sha256: str,
) -> tuple[ClassificationClaim, str]:
    path = Path(path_value)
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise ClassificationContractError(
            "CLAIM_INVALID", "claim path must be canonical and absolute"
        )
    try:
        relative = path.relative_to(private_root)
        resolved = resolve_private_reference(
            private_root, relative.as_posix(), "classification claim"
        )
        value, raw_bytes = read_private_json(resolved, "classification claim")
        actual_sha256 = sha256_hex(raw_bytes)
        if actual_sha256 != validate_digest(expected_sha256, "expected claim digest"):
            raise ClassificationContractError(
                "CLAIM_DIGEST_MISMATCH", "claim digest differs"
            )
    except ValueError as exc:
        raise ClassificationContractError(
            "CLAIM_INVALID", "claim file is outside the private root"
        ) from exc
    except IntakeContractError as exc:
        raise _translate_intake_error(exc) from exc
    return _validate_claim(value), actual_sha256


def _validate_attempt_directory(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ClassificationContractError(
            "CLAIM_RESERVATION_FAILED", "attempt directory is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise ClassificationContractError(
            "CLAIM_RESERVATION_FAILED",
            "attempt directory must be owner-controlled 0700",
        )


def reserve_classification_attempt(
    private_root: Path,
    transaction_sha256: str,
) -> str:
    try:
        directory = resolve_private_reference(
            private_root,
            "classification-attempts",
            "classification attempt directory",
        )
    except IntakeContractError as exc:
        raise ClassificationContractError(
            "CLAIM_RESERVATION_FAILED", "attempt directory is unavailable"
        ) from exc
    _validate_attempt_directory(directory)
    path = directory / f"{transaction_sha256}.json"
    raw_bytes = canonical_json_bytes(
        {
            "schema": ATTEMPT_SCHEMA,
            "operation": OPERATION,
            "state": "claimed",
            "transaction_sha256": transaction_sha256,
        }
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o400)
        created = True
        offset = 0
        while offset < len(raw_bytes):
            offset += os.write(descriptor, raw_bytes[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
    except FileExistsError as exc:
        raise ClassificationContractError(
            "REPLAY_REJECTED", "classification claim was already attempted"
        ) from exc
    except OSError as exc:
        code = (
            "SIDE_EFFECT_UNCERTAIN"
            if created or exc.errno not in {errno.EACCES, errno.ENOENT, errno.ENOTDIR}
            else "CLAIM_RESERVATION_FAILED"
        )
        raise ClassificationContractError(code, "attempt reservation was not proven") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        value, readback = read_private_json(path, "classification attempt")
    except IntakeContractError as exc:
        raise ClassificationContractError(
            "SIDE_EFFECT_UNCERTAIN", "attempt reservation readback failed"
        ) from exc
    if readback != raw_bytes or value["transaction_sha256"] != transaction_sha256:
        raise ClassificationContractError(
            "SIDE_EFFECT_UNCERTAIN", "attempt reservation readback differs"
        )
    return sha256_hex(raw_bytes)
