from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re


_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class ApplicationConfirmationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EmployerConfirmation:
    provider: str
    application_identity_sha256: str
    route_id: str
    route_sha256: str
    anchor_sha256: str
    stable_surface_revision: str
    stable_sample_count: int
    observation_samples_sha256: str
    receipt_sha256: str


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ApplicationConfirmationError(f"{context} must be one SHA-256 digest")
    return value


def validate_employer_confirmation(
    confirmation: EmployerConfirmation,
    *,
    expected_provider: str,
    expected_application_identity_sha256: str,
    expected_receipt_sha256: str,
) -> EmployerConfirmation:
    if not isinstance(confirmation, EmployerConfirmation):
        raise ApplicationConfirmationError("confirmation evidence type is invalid")
    if confirmation.provider != expected_provider:
        raise ApplicationConfirmationError("confirmation provider differs")
    _digest(
        confirmation.application_identity_sha256,
        "confirmation application identity",
    )
    if confirmation.application_identity_sha256 != expected_application_identity_sha256:
        raise ApplicationConfirmationError("confirmation application identity differs")
    if _ID_RE.fullmatch(confirmation.route_id) is None:
        raise ApplicationConfirmationError("confirmation route identity is invalid")
    _digest(confirmation.route_sha256, "confirmation route")
    _digest(confirmation.anchor_sha256, "confirmation anchor")
    _digest(confirmation.stable_surface_revision, "stable confirmation surface")
    _digest(confirmation.observation_samples_sha256, "confirmation samples")
    _digest(confirmation.receipt_sha256, "confirmation receipt")
    if (
        isinstance(confirmation.stable_sample_count, bool)
        or not isinstance(confirmation.stable_sample_count, int)
        or not 2 <= confirmation.stable_sample_count <= 64
    ):
        raise ApplicationConfirmationError(
            "confirmation requires consecutive matched stable samples"
        )
    _digest(expected_receipt_sha256, "expected confirmation receipt")
    if confirmation.receipt_sha256 != expected_receipt_sha256:
        raise ApplicationConfirmationError("confirmation receipt binding differs")
    return confirmation


def employer_confirmation_sha256(confirmation: EmployerConfirmation) -> str:
    raw_bytes = json.dumps(
        {
            "anchor_sha256": confirmation.anchor_sha256,
            "application_identity_sha256": confirmation.application_identity_sha256,
            "observation_samples_sha256": confirmation.observation_samples_sha256,
            "provider": confirmation.provider,
            "receipt_sha256": confirmation.receipt_sha256,
            "route_id": confirmation.route_id,
            "route_sha256": confirmation.route_sha256,
            "stable_sample_count": confirmation.stable_sample_count,
            "stable_surface_revision": confirmation.stable_surface_revision,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw_bytes).hexdigest()


__all__ = [
    "ApplicationConfirmationError",
    "EmployerConfirmation",
    "employer_confirmation_sha256",
    "validate_employer_confirmation",
]
