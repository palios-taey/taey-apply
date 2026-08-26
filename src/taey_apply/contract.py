from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any


PRIVATE_INPUT_SCHEMA = "taey_apply_linkedin_intake_private_input_v1"
RECEIPT_SCHEMA = "taey_apply_linkedin_intake_receipt_v1"
RESULT_SCHEMA = "taey_apply_linkedin_intake_result_v1"
OPERATION = "ingest_linkedin_captured_job"

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_GIT_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PROCESS_GENERATION_RE = re.compile(r"[0-9a-f]{32}")
_MAX_JSON_BYTES = 16 * 1024 * 1024


class IntakeContractError(RuntimeError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_hex(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def validate_digest(value: object, context: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise IntakeContractError(
            "private_input_invalid", f"{context} must be a SHA-256 digest"
        )
    return value


def validate_git_commit(value: object, context: str) -> str:
    if not isinstance(value, str) or _GIT_COMMIT_RE.fullmatch(value) is None:
        raise IntakeContractError(
            "source_receipt_invalid", f"{context} must be a Git commit"
        )
    return value


def validate_public_id(value: object, context: str) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise IntakeContractError("private_input_invalid", f"{context} is invalid")
    return value


def validate_process_generation(value: object) -> str:
    if not isinstance(value, str) or _PROCESS_GENERATION_RE.fullmatch(value) is None:
        raise IntakeContractError(
            "private_input_invalid", "process generation is invalid"
        )
    return value


def turn_lineage_sha256(
    requester: str,
    turn_id: str,
    correlation_id: str,
    process_generation: str,
) -> str:
    return sha256_hex(
        canonical_json_bytes(
            {
                "correlation_id": correlation_id,
                "process_generation": process_generation,
                "requester": requester,
                "turn_id": turn_id,
            }
        )
    )


def _strict_json(raw_bytes: bytes, context: str) -> dict[str, Any]:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise IntakeContractError(
                    "private_input_invalid",
                    f"{context} contains a duplicate key",
                )
            value[key] = item
        return value

    try:
        decoded = json.loads(
            raw_bytes.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda token: (_ for _ in ()).throw(
                IntakeContractError(
                    "private_input_invalid",
                    f"{context} contains a non-JSON constant",
                )
            ),
        )
    except IntakeContractError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntakeContractError(
            "private_input_invalid",
            f"{context} must be strict UTF-8 JSON",
        ) from exc
    if not isinstance(decoded, dict):
        raise IntakeContractError(
            "private_input_invalid", f"{context} must be an object"
        )
    if canonical_json_bytes(decoded) != raw_bytes:
        raise IntakeContractError(
            "private_input_invalid", f"{context} is not canonical JSON"
        )
    return decoded


def _assert_no_symlink_components(path: Path, context: str) -> None:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise IntakeContractError(
                "unsafe_private_path", f"{context} contains a symlink"
            )


def validate_private_root(value: str | os.PathLike[str]) -> Path:
    root = Path(value)
    if not root.is_absolute() or os.path.normpath(str(root)) != str(root):
        raise IntakeContractError(
            "unsafe_private_path", "private root must be canonical and absolute"
        )
    _assert_no_symlink_components(root, "private root")
    try:
        metadata = os.lstat(root)
    except OSError as exc:
        raise IntakeContractError(
            "unsafe_private_path", "private root is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise IntakeContractError(
            "unsafe_private_path",
            "private root must be an owner-controlled 0700 directory",
        )
    return root.resolve(strict=True)


def _validate_relative_reference(value: object, context: str) -> Path:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or _CONTROL_RE.search(value)
        or os.path.isabs(value)
        or os.path.normpath(value) != value
    ):
        raise IntakeContractError("private_input_invalid", f"{context} is invalid")
    relative = Path(value)
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise IntakeContractError("private_input_invalid", f"{context} is invalid")
    return relative


def resolve_private_reference(
    root: Path,
    value: object,
    context: str,
    *,
    must_exist: bool = True,
) -> Path:
    relative = _validate_relative_reference(value, context)
    candidate = root / relative
    _assert_no_symlink_components(candidate, context)
    try:
        resolved = candidate.resolve(strict=must_exist)
    except OSError as exc:
        raise IntakeContractError(
            "unsafe_private_path", f"{context} is unavailable"
        ) from exc
    if resolved == root or root not in resolved.parents:
        raise IntakeContractError(
            "unsafe_private_path", f"{context} escapes the private root"
        )
    return resolved


def _read_owned_file(path: Path, expected_mode: int, context: str) -> bytes:
    _assert_no_symlink_components(path, context)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise IntakeContractError(
            "unsafe_private_path", f"{context} cannot be opened"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != expected_mode
            or metadata.st_uid != os.geteuid()
            or metadata.st_size <= 0
            or metadata.st_size > _MAX_JSON_BYTES
        ):
            raise IntakeContractError(
                "unsafe_private_path", f"{context} is not an exact private file"
            )
        remaining = metadata.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise IntakeContractError(
                    "source_artifact_invalid", f"{context} changed while read"
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise IntakeContractError(
                "source_artifact_invalid", f"{context} changed while read"
            )
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def read_private_json(path: Path, context: str) -> tuple[dict[str, Any], bytes]:
    raw_bytes = _read_owned_file(path, 0o400, context)
    return _strict_json(raw_bytes, context), raw_bytes


def read_private_input(
    path_value: str | os.PathLike[str],
    private_root: Path,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    path = Path(path_value)
    if not path.is_absolute():
        raise IntakeContractError(
            "private_input_invalid", "transaction path must be absolute"
        )
    _assert_no_symlink_components(path, "transaction file")
    resolved = path.resolve(strict=True)
    if private_root not in resolved.parents:
        raise IntakeContractError(
            "unsafe_private_path", "transaction file is outside the private root"
        )
    value, raw_bytes = read_private_json(resolved, "transaction file")
    actual_sha256 = sha256_hex(raw_bytes)
    if actual_sha256 != validate_digest(expected_sha256, "expected transaction digest"):
        raise IntakeContractError(
            "transaction_digest_mismatch", "transaction digest differs from claim"
        )
    expected_keys = {
        "schema",
        "operation",
        "search_receipt_ref",
        "search_artifact_ref",
        "selected_receipt_ref",
        "selected_artifact_ref",
        "card_digest",
    }
    if set(value) != expected_keys:
        raise IntakeContractError(
            "private_input_invalid", "transaction fields are incomplete or unknown"
        )
    if value["schema"] != PRIVATE_INPUT_SCHEMA or value["operation"] != OPERATION:
        raise IntakeContractError(
            "private_input_invalid", "transaction schema or operation is unsupported"
        )
    for key in (
        "search_receipt_ref",
        "search_artifact_ref",
        "selected_receipt_ref",
        "selected_artifact_ref",
    ):
        _validate_relative_reference(value[key], key)
    validate_digest(value["card_digest"], "card digest")
    return value, actual_sha256


def validate_database_path(value: str | os.PathLike[str]) -> Path:
    path = Path(value)
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise IntakeContractError(
            "database_contract_invalid", "database path must be canonical and absolute"
        )
    _assert_no_symlink_components(path, "database")
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise IntakeContractError(
            "database_contract_invalid", "database is unavailable"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_uid != os.geteuid()
    ):
        raise IntakeContractError(
            "database_contract_invalid",
            "database must be an owner-controlled 0600 regular file",
        )
    return path.resolve(strict=True)


def validate_new_receipt_path(
    path_value: str | os.PathLike[str],
    private_root: Path,
) -> Path:
    path = Path(path_value)
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise IntakeContractError(
            "unsafe_private_path", "receipt path must be canonical and absolute"
        )
    _assert_no_symlink_components(path, "receipt path")
    resolved = path.resolve(strict=False)
    if private_root not in resolved.parents or resolved.exists():
        raise IntakeContractError(
            "unsafe_private_path", "receipt path must be new beneath the private root"
        )
    parent = resolved.parent
    try:
        metadata = os.lstat(parent)
    except OSError as exc:
        raise IntakeContractError(
            "unsafe_private_path", "receipt parent is unavailable"
        ) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise IntakeContractError(
            "unsafe_private_path", "receipt parent must be owner-controlled 0700"
        )
    return resolved


def write_new_private_json(path: Path, value: dict[str, Any]) -> bytes:
    raw_bytes = canonical_json_bytes(value)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(path, flags, 0o400)
    except OSError as exc:
        raise IntakeContractError(
            "receipt_write_failed", "receipt could not be created"
        ) from exc
    try:
        offset = 0
        while offset < len(raw_bytes):
            offset += os.write(descriptor, raw_bytes[offset:])
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
    finally:
        os.close(descriptor)
    readback = _read_owned_file(path, 0o400, "receipt")
    if readback != raw_bytes:
        raise IntakeContractError("receipt_write_failed", "receipt readback differs")
    return raw_bytes
