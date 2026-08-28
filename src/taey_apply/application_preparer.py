from __future__ import annotations

import os
from pathlib import Path
import stat

from .application_contract import (
    ApplicationContractError,
    build_application_envelope,
    load_application_envelope,
    load_application_lifecycle,
)
from .contract import (
    IntakeContractError,
    canonical_json_bytes,
    sha256_hex,
    validate_private_root,
    validate_public_id,
)
from .preparer import (
    _create_identity_parent,
    _directory_metadata,
    _ensure_bucket,
    _path_exists,
    _write_frozen_bytes,
)


PREPARATION_RESULT_SCHEMA = "taey_apply_application_preparation_result_v1"
PREPARATION_REFUSAL_SCHEMA = "taey_apply_application_preparation_refusal_v1"
_IDENTITY_BUCKETS = (
    "application-envelopes",
    "application-results",
    "application-preparation-refusals",
)


def _derived_references(seat_id: str, correlation_id: str) -> dict[str, str]:
    filename = f"{correlation_id}.json"
    return {
        "envelope_ref": f"application-envelopes/{seat_id}/{filename}",
        "result_ref": f"application-results/{seat_id}/{filename}",
        "refusal_ref": f"application-preparation-refusals/{seat_id}/{filename}",
    }


def _write_refusal(
    path: Path,
    *,
    seat_id: str,
    correlation_id: str,
    failure_code: str,
) -> None:
    _write_frozen_bytes(
        path,
        canonical_json_bytes(
            {
                "correlation_id": correlation_id,
                "failure_code": failure_code,
                "ok": False,
                "schema": PREPARATION_REFUSAL_SCHEMA,
                "seat_id": seat_id,
                "state": "preparation_refused",
            }
        ),
        "application preparation refusal",
    )


def _preparation_failure(exc: BaseException) -> ApplicationContractError:
    if isinstance(exc, ApplicationContractError):
        return exc
    if isinstance(exc, IntakeContractError):
        code = (
            "application_preparation_indeterminate"
            if exc.failure_code == "preparation_write_indeterminate"
            else "application_contract_invalid"
        )
        return ApplicationContractError(code, "application preparation stopped")
    return ApplicationContractError(
        "application_preparation_indeterminate", "application preparation stopped"
    )


def prepare_application(
    *,
    private_root_value: str | os.PathLike[str],
    lifecycle_path_value: str | os.PathLike[str],
    expected_lifecycle_sha256: object,
    seat_id_value: object,
    correlation_id_value: object,
) -> dict[str, object]:
    try:
        private_root = validate_private_root(private_root_value)
        seat_id = validate_public_id(seat_id_value, "seat ID")
        correlation_id = validate_public_id(correlation_id_value, "correlation ID")
    except IntakeContractError as exc:
        raise _preparation_failure(exc) from exc

    references = _derived_references(seat_id, correlation_id)
    parent_paths = {name: private_root / name / seat_id for name in _IDENTITY_BUCKETS}
    target_paths = {
        key: private_root / reference for key, reference in references.items()
    }
    if any(_path_exists(path) for path in parent_paths.values()) or any(
        _path_exists(path) for path in target_paths.values()
    ):
        raise ApplicationContractError(
            "identity_spent", "application preparation identity already exists"
        )

    try:
        buckets = {
            name: _ensure_bucket(private_root, name) for name in _IDENTITY_BUCKETS
        }
        _ensure_bucket(private_root, "application-attempts")
        refusal_parent = _create_identity_parent(
            buckets["application-preparation-refusals"],
            seat_id,
            "application preparation refusals",
        )
        refusal_path = refusal_parent / f"{correlation_id}.json"
        result_parent = _create_identity_parent(
            buckets["application-results"], seat_id, "application results"
        )
        envelope_parent = _create_identity_parent(
            buckets["application-envelopes"], seat_id, "application envelopes"
        )
        envelope_path = envelope_parent / f"{correlation_id}.json"
        result_path = result_parent / f"{correlation_id}.json"

        lifecycle, lifecycle_sha256 = load_application_lifecycle(
            private_root,
            lifecycle_path_value,
            expected_lifecycle_sha256,
        )
        if any(lifecycle[key] != value for key, value in references.items()):
            raise ApplicationContractError(
                "application_contract_invalid",
                "application lifecycle identity references differ",
            )
        envelope = build_application_envelope(private_root, lifecycle, lifecycle_sha256)
        envelope_bytes = canonical_json_bytes(dict(envelope))
        _write_frozen_bytes(envelope_path, envelope_bytes, "application envelope")
        envelope_sha256 = sha256_hex(envelope_bytes)
        readback, readback_sha256 = load_application_envelope(
            private_root, envelope_path, envelope_sha256
        )
        if (
            readback_sha256 != envelope_sha256
            or readback.application_identity_sha256
            != lifecycle["application_identity_sha256"]
            or _path_exists(result_path)
            or _path_exists(refusal_path)
        ):
            raise ApplicationContractError(
                "application_preparation_indeterminate",
                "application envelope readback was not proven",
            )
        parent_modes = {
            name: f"{stat.S_IMODE(_directory_metadata(path, name).st_mode):04o}"
            for name, path in {
                "envelope": envelope_parent,
                "result": result_parent,
                "refusal": refusal_parent,
            }.items()
        }
        return {
            "schema": PREPARATION_RESULT_SCHEMA,
            "ok": True,
            "state": "prepared_unclaimed",
            "seat_id": seat_id,
            "correlation_id": correlation_id,
            "lifecycle_sha256": lifecycle_sha256,
            "envelope_sha256": envelope_sha256,
            "application_identity_sha256": readback.application_identity_sha256,
            "evidence_gate_count": len(readback.evidence),
            "maximum_one_action_calls": readback.maximum_one_action_calls,
            "envelope_mode": "0400",
            "parent_modes": parent_modes,
            "result_absent": True,
            "refusal_absent": True,
        }
    except (ApplicationContractError, IntakeContractError, OSError) as exc:
        failure = _preparation_failure(exc)
        if "refusal_path" in locals() and not _path_exists(refusal_path):
            _write_refusal(
                refusal_path,
                seat_id=seat_id,
                correlation_id=correlation_id,
                failure_code=failure.failure_code,
            )
        raise failure from exc


__all__ = [
    "PREPARATION_REFUSAL_SCHEMA",
    "PREPARATION_RESULT_SCHEMA",
    "prepare_application",
]
