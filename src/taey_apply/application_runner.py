from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .application_confirmation import (
    ApplicationConfirmationError,
    employer_confirmation_sha256,
    validate_employer_confirmation,
)
from .application_contract import (
    ApplicationContractError,
    OneActionExecutor,
    OneActionOutcome,
    OneActionRequest,
    OPERATION,
    RECEIPT_SCHEMA,
    load_application_envelope,
    validate_application_envelope_sources,
    validate_one_action_outcome,
)
from .contract import (
    IntakeContractError,
    canonical_json_bytes,
    resolve_private_reference,
    sha256_hex,
    validate_new_receipt_path,
    validate_private_root,
    write_new_private_json,
)


ATTEMPT_SCHEMA = "taey_apply_application_attempt_v1"
RESULT_SCHEMA = "taey_apply_application_result_v1"


def _attempt_path(private_root: Path, envelope_sha256: str) -> Path:
    try:
        directory = resolve_private_reference(
            private_root, "application-attempts", "application attempts"
        )
        return validate_new_receipt_path(
            directory / f"{envelope_sha256}.json", private_root
        )
    except IntakeContractError as exc:
        raise ApplicationContractError(
            "policy_or_authority_boundary", "application attempt cannot be reserved"
        ) from exc


def _result_path(private_root: Path, result_ref: str) -> Path:
    try:
        path = resolve_private_reference(
            private_root, result_ref, "application result", must_exist=False
        )
        return validate_new_receipt_path(path, private_root)
    except IntakeContractError as exc:
        raise ApplicationContractError(
            "policy_or_authority_boundary", "application result identity is invalid"
        ) from exc


def _reserve_attempt(
    private_root: Path,
    *,
    envelope_sha256: str,
    application_identity_sha256: str,
) -> str:
    value = {
        "application_identity_sha256": application_identity_sha256,
        "envelope_sha256": envelope_sha256,
        "operation": OPERATION,
        "schema": ATTEMPT_SCHEMA,
        "state": "claimed",
    }
    try:
        raw_bytes = write_new_private_json(
            _attempt_path(private_root, envelope_sha256), value
        )
    except IntakeContractError as exc:
        raise ApplicationContractError(
            "side_effect_uncertainty", "application attempt was not proven"
        ) from exc
    return sha256_hex(raw_bytes)


def _receipt_chain_sha256(outcomes: list[OneActionOutcome]) -> str:
    return sha256_hex(
        canonical_json_bytes([outcome.receipt_sha256 for outcome in outcomes])
    )


def _finalize(
    private_root: Path,
    result_path: Path,
    *,
    ok: bool,
    state: str,
    failure_code: str | None,
    provider: str,
    application_identity_sha256: str,
    envelope_sha256: str,
    attempt_sha256: str,
    outcomes: list[OneActionOutcome],
    executor_calls: int,
    confirmation_sha256: str | None,
    ui_mutations_known: bool = True,
) -> dict[str, Any]:
    mutations = (
        sum(outcome.mutation_count for outcome in outcomes)
        if ui_mutations_known
        else None
    )
    final_executor_receipt_sha256 = outcomes[-1].receipt_sha256 if outcomes else None
    chain_sha256 = _receipt_chain_sha256(outcomes)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "operation": OPERATION,
        "ok": ok,
        "state": state,
        "failure_code": failure_code,
        "provider": provider,
        "application_identity_sha256": application_identity_sha256,
        "envelope_sha256": envelope_sha256,
        "attempt_sha256": attempt_sha256,
        "one_action_calls": executor_calls,
        "ui_mutations": mutations,
        "final_executor_receipt_sha256": final_executor_receipt_sha256,
        "receipt_chain_sha256": chain_sha256,
        "confirmation_sha256": confirmation_sha256,
        "next_mutation_authorized": False,
    }
    try:
        receipt_bytes = write_new_private_json(result_path, receipt)
    except IntakeContractError as exc:
        raise ApplicationContractError(
            "side_effect_uncertainty", "application result could not be proven"
        ) from exc
    receipt_sha256 = sha256_hex(receipt_bytes)
    return {
        "schema": RESULT_SCHEMA,
        "ok": ok,
        "state": state,
        "failure_code": failure_code,
        "provider": provider,
        "application_identity_sha256": application_identity_sha256,
        "envelope_sha256": envelope_sha256,
        "attempt_sha256": attempt_sha256,
        "one_action_calls": executor_calls,
        "ui_mutations": mutations,
        "confirmation_sha256": confirmation_sha256,
        "receipt_chain_sha256": chain_sha256,
        "receipt_sha256": receipt_sha256,
        "next_mutation_authorized": False,
    }


def _executor_failure(
    _exc: BaseException,
) -> ApplicationContractError:
    return ApplicationContractError(
        "side_effect_uncertainty", "one-action execution became uncertain"
    )


def run_application(
    *,
    private_root_value: str | os.PathLike[str],
    envelope_path_value: str | os.PathLike[str],
    expected_envelope_sha256: object,
    executor: OneActionExecutor,
) -> dict[str, Any]:
    try:
        private_root = validate_private_root(private_root_value)
    except IntakeContractError as exc:
        raise ApplicationContractError(
            "policy_or_authority_boundary", "private application root is invalid"
        ) from exc
    try:
        envelope, envelope_sha256 = load_application_envelope(
            private_root, envelope_path_value, expected_envelope_sha256
        )
    except ApplicationContractError as exc:
        raise ApplicationContractError(
            "policy_or_authority_boundary", "application envelope is invalid"
        ) from exc
    result_path = _result_path(private_root, envelope.result_ref)
    attempt_sha256 = _reserve_attempt(
        private_root,
        envelope_sha256=envelope_sha256,
        application_identity_sha256=envelope.application_identity_sha256,
    )
    outcomes: list[OneActionOutcome] = []
    try:
        validate_application_envelope_sources(private_root, envelope)
    except ApplicationContractError:
        return _finalize(
            private_root,
            result_path,
            ok=False,
            state="terminal_halt",
            failure_code="policy_or_authority_boundary",
            provider=envelope.provider,
            application_identity_sha256=envelope.application_identity_sha256,
            envelope_sha256=envelope_sha256,
            attempt_sha256=attempt_sha256,
            outcomes=outcomes,
            executor_calls=0,
            confirmation_sha256=None,
        )
    seen_action_ids: set[str] = set()
    seen_receipts: set[str] = set()
    previous_receipt_sha256: str | None = None
    executor_calls = 0

    for sequence_number in range(1, envelope.maximum_one_action_calls + 1):
        request = OneActionRequest(
            envelope=envelope,
            envelope_sha256=envelope_sha256,
            sequence_number=sequence_number,
            previous_receipt_sha256=previous_receipt_sha256,
        )
        try:
            executor_calls += 1
            raw_outcome = executor(request)
            outcome = validate_one_action_outcome(raw_outcome, request)
        except Exception as exc:
            failure = _executor_failure(exc)
            return _finalize(
                private_root,
                result_path,
                ok=False,
                state="side_effect_uncertain",
                failure_code=failure.failure_code,
                provider=envelope.provider,
                application_identity_sha256=envelope.application_identity_sha256,
                envelope_sha256=envelope_sha256,
                attempt_sha256=attempt_sha256,
                outcomes=outcomes,
                executor_calls=executor_calls,
                ui_mutations_known=False,
                confirmation_sha256=None,
            )
        if (
            outcome.action_id in seen_action_ids
            or outcome.receipt_sha256 in seen_receipts
        ):
            return _finalize(
                private_root,
                result_path,
                ok=False,
                state="side_effect_uncertain",
                failure_code="side_effect_uncertainty",
                provider=envelope.provider,
                application_identity_sha256=envelope.application_identity_sha256,
                envelope_sha256=envelope_sha256,
                attempt_sha256=attempt_sha256,
                outcomes=outcomes,
                executor_calls=executor_calls,
                ui_mutations_known=False,
                confirmation_sha256=None,
            )
        outcomes.append(outcome)
        seen_action_ids.add(outcome.action_id)
        seen_receipts.add(outcome.receipt_sha256)
        previous_receipt_sha256 = outcome.receipt_sha256

        if outcome.state in {"terminal_halt", "side_effect_uncertain"}:
            return _finalize(
                private_root,
                result_path,
                ok=False,
                state=outcome.state,
                failure_code=outcome.stop_code,
                provider=envelope.provider,
                application_identity_sha256=envelope.application_identity_sha256,
                envelope_sha256=envelope_sha256,
                attempt_sha256=attempt_sha256,
                outcomes=outcomes,
                executor_calls=executor_calls,
                confirmation_sha256=None,
            )
        if outcome.state == "employer_confirmation_proven":
            try:
                confirmation = validate_employer_confirmation(
                    outcome.confirmation,
                    expected_provider=envelope.provider,
                    expected_application_identity_sha256=(
                        envelope.application_identity_sha256
                    ),
                )
            except ApplicationConfirmationError:
                return _finalize(
                    private_root,
                    result_path,
                    ok=False,
                    state="side_effect_uncertain",
                    failure_code="side_effect_uncertainty",
                    provider=envelope.provider,
                    application_identity_sha256=envelope.application_identity_sha256,
                    envelope_sha256=envelope_sha256,
                    attempt_sha256=attempt_sha256,
                    outcomes=outcomes,
                    executor_calls=executor_calls,
                    confirmation_sha256=None,
                )
            return _finalize(
                private_root,
                result_path,
                ok=True,
                state="employer_confirmation_proven",
                failure_code=None,
                provider=envelope.provider,
                application_identity_sha256=envelope.application_identity_sha256,
                envelope_sha256=envelope_sha256,
                attempt_sha256=attempt_sha256,
                outcomes=outcomes,
                executor_calls=executor_calls,
                confirmation_sha256=employer_confirmation_sha256(confirmation),
            )

    return _finalize(
        private_root,
        result_path,
        ok=False,
        state="terminal_halt",
        failure_code="policy_or_authority_boundary",
        provider=envelope.provider,
        application_identity_sha256=envelope.application_identity_sha256,
        envelope_sha256=envelope_sha256,
        attempt_sha256=attempt_sha256,
        outcomes=outcomes,
        executor_calls=executor_calls,
        confirmation_sha256=None,
    )


__all__ = [
    "ATTEMPT_SCHEMA",
    "RESULT_SCHEMA",
    "run_application",
]
