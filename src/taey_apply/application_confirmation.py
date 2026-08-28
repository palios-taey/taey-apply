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
    observation_revisions: tuple[str, ...]


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ApplicationConfirmationError(f"{context} must be one SHA-256 digest")
    return value


def validate_employer_confirmation(
    confirmation: EmployerConfirmation,
    *,
    expected_provider: str,
    expected_application_identity_sha256: str,
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
    revisions = confirmation.observation_revisions
    if (
        not isinstance(revisions, tuple)
        or len(revisions) < 2
        or len(set(revisions)) != len(revisions)
        or any(_DIGEST_RE.fullmatch(value) is None for value in revisions)
    ):
        raise ApplicationConfirmationError(
            "confirmation requires independent stable observations"
        )
    return confirmation


def employer_confirmation_sha256(confirmation: EmployerConfirmation) -> str:
    raw_bytes = json.dumps(
        {
            "anchor_sha256": confirmation.anchor_sha256,
            "application_identity_sha256": confirmation.application_identity_sha256,
            "observation_revisions": list(confirmation.observation_revisions),
            "provider": confirmation.provider,
            "route_id": confirmation.route_id,
            "route_sha256": confirmation.route_sha256,
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
