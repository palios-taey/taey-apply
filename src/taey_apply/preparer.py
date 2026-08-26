from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any

from .contract import (
    IntakeContractError,
    canonical_json_bytes,
    read_private_input,
    resolve_private_reference,
    sha256_hex,
    validate_private_input_value,
    validate_private_root,
    validate_public_id,
)
from .linkedin_intake import load_linkedin_capture


PREPARATION_SCHEMA = "taey_apply_linkedin_intake_preparation_v1"
PREPARATION_REFUSAL_SCHEMA = "taey_apply_linkedin_intake_preparation_refusal_v1"
_MAX_DRAFT_BYTES = 16 * 1024 * 1024
_IDENTITY_BUCKETS = ("receipts", "claims", "transactions")


def _strict_draft_object(raw_bytes: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise IntakeContractError(
                    "private_input_invalid", "draft contains a duplicate key"
                )
            value[key] = item
        return value

    try:
        value = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                IntakeContractError(
                    "private_input_invalid",
                    "draft contains a non-JSON constant",
                )
            ),
        )
    except IntakeContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntakeContractError(
            "private_input_invalid", "draft must be strict UTF-8 JSON"
        ) from exc
    return validate_private_input_value(value)


def _read_private_draft(
    path_value: str | os.PathLike[str], private_root: Path
) -> tuple[dict[str, Any], bytes]:
    path = Path(path_value)
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise IntakeContractError(
            "unsafe_private_path", "draft path must be canonical and absolute"
        )
    try:
        relative = path.relative_to(private_root)
    except ValueError as exc:
        raise IntakeContractError(
            "unsafe_private_path", "draft file is outside the private root"
        ) from exc
    resolved = resolve_private_reference(
        private_root, relative.as_posix(), "draft file"
    )
    try:
        descriptor = os.open(
            resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
    except OSError as exc:
        raise IntakeContractError(
            "unsafe_private_path", "draft file cannot be opened"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_uid != os.geteuid()
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_DRAFT_BYTES
        ):
            raise IntakeContractError(
                "unsafe_private_path",
                "draft must be an owner-controlled 0400 regular file",
            )
        remaining = metadata.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise IntakeContractError(
                    "private_input_invalid", "draft changed while read"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise IntakeContractError(
                "private_input_invalid", "draft changed while read"
            )
    finally:
        os.close(descriptor)
    raw_bytes = b"".join(chunks)
    return _strict_draft_object(raw_bytes), raw_bytes


def _directory_metadata(path: Path, context: str) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise IntakeContractError(
            "unsafe_private_path", f"{context} is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise IntakeContractError(
            "unsafe_private_path",
            f"{context} must be an owner-controlled 0700 directory",
        )
    return metadata


def _fsync_directory(path: Path, context: str) -> None:
    try:
        descriptor = os.open(
            path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise IntakeContractError(
            "preparation_write_indeterminate", f"{context} could not be synced"
        ) from exc


def _ensure_bucket(private_root: Path, name: str) -> Path:
    bucket = private_root / name
    created = False
    try:
        os.mkdir(bucket, 0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise IntakeContractError(
            "preparation_write_indeterminate", f"{name} bucket could not be created"
        ) from exc
    _directory_metadata(bucket, f"{name} bucket")
    if created:
        _fsync_directory(private_root, "private root")
    return bucket


def _path_exists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise IntakeContractError(
            "preparation_write_indeterminate", "identity state cannot be inspected"
        ) from exc
    return True


def _create_identity_parent(bucket: Path, seat_id: str, context: str) -> Path:
    parent = bucket / seat_id
    try:
        os.mkdir(parent, 0o700)
    except FileExistsError as exc:
        raise IntakeContractError(
            "identity_spent", "transaction identity already exists"
        ) from exc
    except OSError as exc:
        raise IntakeContractError(
            "preparation_write_indeterminate", f"{context} parent could not be created"
        ) from exc
    _directory_metadata(parent, f"{context} parent")
    _fsync_directory(bucket, f"{context} bucket")
    return parent


def _write_frozen_bytes(path: Path, raw_bytes: bytes, context: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise IntakeContractError(
            "identity_spent", "transaction identity already exists"
        ) from exc
    except OSError as exc:
        raise IntakeContractError(
            "preparation_write_indeterminate", f"{context} could not be created"
        ) from exc
    try:
        with os.fdopen(descriptor, "wb", buffering=0, closefd=True) as handle:
            offset = 0
            while offset < len(raw_bytes):
                written = handle.write(raw_bytes[offset:])
                if written is None or written <= 0:
                    raise OSError("transaction write did not advance")
                offset += written
            os.fchmod(handle.fileno(), 0o400)
            os.fsync(handle.fileno())
    except OSError as exc:
        raise IntakeContractError(
            "preparation_write_indeterminate",
            f"{context} finalization is indeterminate; identity is spent",
        ) from exc
    _fsync_directory(path.parent, f"{context} parent")


def _write_refusal_marker(
    path: Path,
    *,
    seat_id: str,
    correlation_id: str,
    failure_code: str,
) -> None:
    marker = canonical_json_bytes(
        {
            "correlation_id": correlation_id,
            "failure_code": failure_code,
            "ok": False,
            "schema": PREPARATION_REFUSAL_SCHEMA,
            "seat_id": seat_id,
            "state": "preparation_refused",
        }
    )
    _write_frozen_bytes(path, marker, "preparation refusal marker")


def prepare_linkedin_intake(
    *,
    private_root_value: str | os.PathLike[str],
    draft_path_value: str | os.PathLike[str],
    seat_id_value: object,
    correlation_id_value: object,
) -> dict[str, object]:
    private_root = validate_private_root(private_root_value)
    seat_id = validate_public_id(seat_id_value, "seat ID")
    correlation_id = validate_public_id(correlation_id_value, "correlation ID")

    bucket_paths = {name: private_root / name for name in _IDENTITY_BUCKETS}
    identity_parents = {
        name: bucket / seat_id for name, bucket in bucket_paths.items()
    }
    identity_targets = {
        name: parent / f"{correlation_id}.json"
        for name, parent in identity_parents.items()
    }
    if any(_path_exists(path) for path in identity_parents.values()) or any(
        _path_exists(path) for path in identity_targets.values()
    ):
        raise IntakeContractError(
            "identity_spent", "transaction identity already exists"
        )

    buckets = {
        name: _ensure_bucket(private_root, name) for name in _IDENTITY_BUCKETS
    }
    if any(_path_exists(bucket / seat_id) for bucket in buckets.values()):
        raise IntakeContractError(
            "identity_spent", "transaction identity already exists"
        )
    receipt_parent = _create_identity_parent(buckets["receipts"], seat_id, "receipts")
    receipt_path = receipt_parent / f"{correlation_id}.json"
    try:
        claim_parent = _create_identity_parent(buckets["claims"], seat_id, "claims")
        transaction_parent = _create_identity_parent(
            buckets["transactions"], seat_id, "transactions"
        )
        parents = {
            "receipts": receipt_parent,
            "claims": claim_parent,
            "transactions": transaction_parent,
        }
        transaction_path = transaction_parent / f"{correlation_id}.json"
        claim_path = claim_parent / f"{correlation_id}.json"
        transaction, draft_bytes = _read_private_draft(
            draft_path_value, private_root
        )
        capture_before = load_linkedin_capture(private_root, transaction)
        canonical_bytes = canonical_json_bytes(transaction)
        _write_frozen_bytes(transaction_path, canonical_bytes, "transaction")

        transaction_sha256 = sha256_hex(canonical_bytes)
        prepared, readback_sha256 = read_private_input(
            transaction_path, private_root, transaction_sha256
        )
        capture_after = load_linkedin_capture(private_root, prepared)
        if (
            readback_sha256 != transaction_sha256
            or capture_after != capture_before
            or _path_exists(claim_path)
            or _path_exists(receipt_path)
        ):
            raise IntakeContractError(
                "preparation_write_indeterminate",
                "prepared identity failed exact readback; identity is spent",
            )
        parent_modes = {
            name: f"{stat.S_IMODE(_directory_metadata(path, f'{name} parent').st_mode):04o}"
            for name, path in parents.items()
        }
        return {
            "schema": PREPARATION_SCHEMA,
            "ok": True,
            "state": "prepared_unclaimed",
            "seat_id": seat_id,
            "correlation_id": correlation_id,
            "draft_sha256": sha256_hex(draft_bytes),
            "transaction_sha256": transaction_sha256,
            "transaction_bytes": len(canonical_bytes),
            "transaction_mode": "0400",
            "canonical_no_trailing_newline": not canonical_bytes.endswith(b"\n"),
            "canonicalization_changed": draft_bytes != canonical_bytes,
            "parent_modes": parent_modes,
            "source_file_count": 4,
            "card_match_count": 1,
            "job_identity_sha256": capture_after.job_identity_sha256,
            "capture_digest": capture_after.capture_digest,
            "claim_absent": True,
            "receipt_absent": True,
        }
    except IntakeContractError as exc:
        _write_refusal_marker(
            receipt_path,
            seat_id=seat_id,
            correlation_id=correlation_id,
            failure_code=exc.failure_code,
        )
        raise
    except OSError as exc:
        failure = IntakeContractError(
            "preparation_write_indeterminate",
            "preparation finalization is indeterminate; identity is spent",
        )
        _write_refusal_marker(
            receipt_path,
            seat_id=seat_id,
            correlation_id=correlation_id,
            failure_code=failure.failure_code,
        )
        raise failure from exc
