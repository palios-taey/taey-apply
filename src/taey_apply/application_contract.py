from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, Mapping, Protocol

from .application_confirmation import EmployerConfirmation
from .contract import (
    IntakeContractError,
    _validate_relative_reference,
    canonical_json_bytes,
    read_private_json,
    resolve_private_reference,
    sha256_hex,
    validate_digest,
)


LIFECYCLE_SCHEMA = "taey_apply_application_lifecycle_v1"
ENVELOPE_SCHEMA = "taey_apply_application_envelope_v1"
RECEIPT_SCHEMA = "taey_apply_application_receipt_v1"
TERMINAL_EVIDENCE_SCHEMA = "taey_apply_application_executor_terminal_evidence_v1"
DECISION_RESPONSE_EVIDENCE_SCHEMA = (
    "taey_apply_application_executor_decision_response_v1"
)
OPERATION = "execute_autonomous_application"

EVIDENCE_STATES = {
    "discovery": "discovered",
    "qualification": "qualified",
    "deep_research": "deep_research_complete",
    "materials": "materials_ready",
    "truthful_applicant_data": "truthful_applicant_data_ready",
    "submission_authority": "within_policy_and_authority",
}
STOP_CODES = frozenset(
    {
        "exact_postcondition_failure",
        "unmapped_ui_or_question",
        "missing_truthful_applicant_data",
        "policy_or_authority_boundary",
        "side_effect_uncertainty",
    }
)
OUTCOME_STATES = frozenset(
    {
        "observation_proven",
        "action_proven",
        "employer_confirmation_proven",
        "terminal_halt",
        "side_effect_uncertain",
    }
)
TERMINAL_EVIDENCE_STAGES = frozenset(
    {"request_lineage", "decision", "compile", "presence"}
)
TERMINAL_REASON_CODES = frozenset(
    {
        "request_lineage_mismatch",
        "decision_source_refused",
        "compiler_refused",
        "taey_explicit_halt",
        "presence_observation_refused",
    }
)
DECISION_REJECTION_CODES = frozenset(
    {
        "decision_transport_failure",
        "decision_response_capture_failed",
        "decision_response_envelope_malformed",
        "decision_response_content_malformed",
        "decision_fields_malformed",
        "decision_cross_field_malformed",
    }
)

_MANIFEST_KEYS = frozenset(
    {
        "schema",
        "operation",
        "provider",
        "application_identity_sha256",
        "evidence",
        "application_context_ref",
        "application_context_sha256",
        "application_context_schema",
        "maximum_one_action_calls",
        "envelope_ref",
        "result_ref",
        "refusal_ref",
    }
)
_ENVELOPE_KEYS = frozenset(_MANIFEST_KEYS - {"schema", "refusal_ref"}) | {
    "schema",
    "source_manifest_sha256",
}
_BINDING_KEYS = frozenset(
    {"receipt_ref", "receipt_sha256", "receipt_schema", "expected_state"}
)
_GATE_RECEIPT_KEYS = frozenset(
    {"schema", "ok", "state", "application_identity_sha256", "evidence_sha256"}
)
_TERMINAL_EVIDENCE_KEYS = frozenset(
    {
        "schema",
        "application_identity_sha256",
        "envelope_sha256",
        "sequence_number",
        "previous_receipt_sha256",
        "action_id",
        "state",
        "failure_code",
        "stage",
        "reason_code",
        "accepted_decision_ref",
        "accepted_decision_sha256",
        "decision_response_ref",
        "decision_response_sha256",
        "decision_response_payload_sha256",
        "decision_rejection_code",
        "capsule_sha256",
        "presence_response_payload_sha256",
        "mutation_count",
        "next_mutation_authorized",
    }
)
_DECISION_RESPONSE_EVIDENCE_KEYS = frozenset(
    {
        "schema",
        "application_identity_sha256",
        "seat_id",
        "event_id",
        "correlation_id",
        "request_payload_sha256",
        "response_payload_sha256",
        "response_payload",
    }
)
_ACCEPTED_DECISION_KEYS = frozenset(
    {
        "schema",
        "action",
        "ref",
        "revision",
        "fact_key",
        "work_evidence_keys",
        "expected_option_name",
        "stop_code",
    }
)
_DECISION_ACTIONS = frozenset(
    {
        "focus",
        "fill",
        "scroll_combo",
        "open_combo",
        "select_option",
        "activate_choice",
        "open_upload",
        "chooser_location",
        "chooser_select_all",
        "chooser_type_path",
        "chooser_confirm",
        "submit",
        "halt",
    }
)
_FACT_ACTIONS = frozenset(
    {"focus", "fill", "scroll_combo", "open_combo", "select_option", "activate_choice"}
)
_PROVIDER_RE = re.compile(r"[a-z][a-z0-9_-]{0,31}")
_SCHEMA_RE = re.compile(r"[a-z][a-z0-9_]{2,127}")
_PUBLIC_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_REF_RE = re.compile(r"(?:r_[0-9a-f]{32}|nd1_[0-9a-f]{64})")
_TOKEN_RE = re.compile(r"[a-z][a-z0-9_]{0,127}")
_TERMINAL_EVIDENCE_REF_RE = re.compile(
    r"application-executor-outcomes/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json"
)
_ACCEPTED_DECISION_REF_RE = re.compile(
    r"application-executor-decisions/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/"
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json"
)
_DECISION_RESPONSE_REF_RE = re.compile(
    r"application-executor-decision-responses/"
    r"([A-Za-z0-9][A-Za-z0-9._-]{0,127})/"
    r"([A-Za-z0-9][A-Za-z0-9._-]{0,127})\.json"
)


class ApplicationContractError(RuntimeError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        self.failure_code = failure_code


class ApplicationDecisionContractError(ApplicationContractError):
    def __init__(self, message: str) -> None:
        super().__init__("side_effect_uncertainty", message)
        self.rejection_code = "decision_cross_field_malformed"


@dataclass(frozen=True, slots=True)
class EvidenceBinding:
    stage: str
    receipt_ref: str
    receipt_sha256: str
    receipt_schema: str
    expected_state: str


@dataclass(frozen=True, slots=True)
class ApplicationEnvelope:
    provider: str
    application_identity_sha256: str
    evidence: tuple[EvidenceBinding, ...]
    application_context_ref: str
    application_context_sha256: str
    application_context_schema: str
    maximum_one_action_calls: int
    envelope_ref: str
    result_ref: str
    source_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class OneActionRequest:
    envelope: ApplicationEnvelope
    envelope_sha256: str
    sequence_number: int
    previous_receipt_sha256: str | None


@dataclass(frozen=True, slots=True)
class OneActionOutcome:
    application_identity_sha256: str
    action_id: str
    previous_receipt_sha256: str | None
    receipt_sha256: str
    state: str
    mutation_count: int
    postcondition_sha256: str | None
    next_mutation_authorized: bool
    stop_code: str | None = None
    confirmation: EmployerConfirmation | None = None
    terminal_evidence_ref: str | None = None
    terminal_evidence_sha256: str | None = None


class OneActionExecutor(Protocol):
    def __call__(self, request: OneActionRequest) -> OneActionOutcome: ...


def _failure(exc: BaseException, code: str) -> ApplicationContractError:
    if isinstance(exc, ApplicationContractError):
        return exc
    if isinstance(exc, IntakeContractError):
        return ApplicationContractError(code, "application contract is invalid")
    return ApplicationContractError(code, "application contract is invalid")


def _mapping(value: object, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ApplicationContractError(
            "application_contract_invalid", f"{context} must be an object"
        )
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: frozenset[str], context: str
) -> None:
    if frozenset(value) != expected:
        raise ApplicationContractError(
            "application_contract_invalid", f"{context} fields are not exact"
        )


def _digest(value: object, context: str) -> str:
    try:
        return validate_digest(value, context)
    except IntakeContractError as exc:
        raise _failure(exc, "application_contract_invalid") from exc


def _schema(value: object, context: str) -> str:
    if not isinstance(value, str) or _SCHEMA_RE.fullmatch(value) is None:
        raise ApplicationContractError(
            "application_contract_invalid", f"{context} is invalid"
        )
    return value


def _private_reference(value: object, context: str) -> str:
    try:
        reference = _validate_relative_reference(value, context)
    except IntakeContractError as exc:
        raise _failure(exc, "application_contract_invalid") from exc
    return reference.as_posix()


def _bound_private_json(
    private_root: Path,
    reference: str,
    expected_sha256: str,
    context: str,
) -> tuple[Mapping[str, Any], str]:
    try:
        path = resolve_private_reference(private_root, reference, context)
        value, raw_bytes = read_private_json(path, context)
    except IntakeContractError as exc:
        raise _failure(exc, "application_evidence_invalid") from exc
    actual_sha256 = sha256_hex(raw_bytes)
    if actual_sha256 != expected_sha256:
        raise ApplicationContractError(
            "application_evidence_invalid", f"{context} digest differs"
        )
    return value, actual_sha256


def _evidence_bindings(value: object) -> tuple[EvidenceBinding, ...]:
    evidence = _mapping(value, "evidence")
    if frozenset(evidence) != frozenset(EVIDENCE_STATES):
        raise ApplicationContractError(
            "application_contract_invalid", "application evidence stages are not exact"
        )
    bindings: list[EvidenceBinding] = []
    for stage, expected_state in EVIDENCE_STATES.items():
        raw_binding = _mapping(evidence[stage], f"evidence.{stage}")
        _exact_keys(raw_binding, _BINDING_KEYS, f"evidence.{stage}")
        if raw_binding["expected_state"] != expected_state:
            raise ApplicationContractError(
                "application_contract_invalid",
                f"evidence.{stage} expected state differs",
            )
        bindings.append(
            EvidenceBinding(
                stage=stage,
                receipt_ref=_private_reference(
                    raw_binding["receipt_ref"], f"evidence.{stage}.receipt_ref"
                ),
                receipt_sha256=_digest(
                    raw_binding["receipt_sha256"],
                    f"evidence.{stage}.receipt_sha256",
                ),
                receipt_schema=_schema(
                    raw_binding["receipt_schema"],
                    f"evidence.{stage}.receipt_schema",
                ),
                expected_state=expected_state,
            )
        )
    return tuple(bindings)


def _validated_manifest(value: object) -> Mapping[str, Any]:
    manifest = _mapping(value, "application lifecycle")
    _exact_keys(manifest, _MANIFEST_KEYS, "application lifecycle")
    if manifest["schema"] != LIFECYCLE_SCHEMA or manifest["operation"] != OPERATION:
        raise ApplicationContractError(
            "application_contract_invalid", "application lifecycle is unsupported"
        )
    provider = manifest["provider"]
    if not isinstance(provider, str) or _PROVIDER_RE.fullmatch(provider) is None:
        raise ApplicationContractError(
            "application_contract_invalid", "provider identity is invalid"
        )
    _digest(manifest["application_identity_sha256"], "application identity")
    _evidence_bindings(manifest["evidence"])
    _private_reference(manifest["application_context_ref"], "application context")
    _digest(manifest["application_context_sha256"], "application context")
    _schema(manifest["application_context_schema"], "application context schema")
    maximum = manifest["maximum_one_action_calls"]
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or not 1 <= maximum <= 256
    ):
        raise ApplicationContractError(
            "application_contract_invalid",
            "maximum one-action calls must be between 1 and 256",
        )
    for key in ("envelope_ref", "result_ref", "refusal_ref"):
        _private_reference(manifest[key], key)
    return manifest


def load_application_lifecycle(
    private_root: Path,
    path_value: str | os.PathLike[str],
    expected_sha256: object,
) -> tuple[Mapping[str, Any], str]:
    path = Path(path_value)
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise ApplicationContractError(
            "application_contract_invalid",
            "application lifecycle path must be canonical and absolute",
        )
    try:
        relative = path.relative_to(private_root)
        resolved = resolve_private_reference(
            private_root, relative.as_posix(), "application lifecycle"
        )
        value, raw_bytes = read_private_json(resolved, "application lifecycle")
    except (IntakeContractError, ValueError) as exc:
        raise _failure(exc, "application_contract_invalid") from exc
    actual_sha256 = sha256_hex(raw_bytes)
    if actual_sha256 != _digest(expected_sha256, "expected lifecycle digest"):
        raise ApplicationContractError(
            "application_contract_invalid", "application lifecycle digest differs"
        )
    return _validated_manifest(value), actual_sha256


def build_application_envelope(
    private_root: Path,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
) -> Mapping[str, Any]:
    bindings = _evidence_bindings(manifest["evidence"])
    application_identity_sha256 = _digest(
        manifest["application_identity_sha256"], "application identity"
    )
    for binding in bindings:
        receipt, _ = _bound_private_json(
            private_root,
            binding.receipt_ref,
            binding.receipt_sha256,
            f"{binding.stage} receipt",
        )
        _exact_keys(receipt, _GATE_RECEIPT_KEYS, f"{binding.stage} receipt")
        if (
            receipt["schema"] != binding.receipt_schema
            or receipt["ok"] is not True
            or receipt["state"] != binding.expected_state
            or receipt["application_identity_sha256"] != application_identity_sha256
        ):
            raise ApplicationContractError(
                "application_evidence_invalid",
                f"{binding.stage} receipt does not satisfy its gate",
            )
        _digest(receipt["evidence_sha256"], f"{binding.stage} evidence digest")
    context, _ = _bound_private_json(
        private_root,
        str(manifest["application_context_ref"]),
        str(manifest["application_context_sha256"]),
        "application context",
    )
    if context.get("schema") != manifest["application_context_schema"]:
        raise ApplicationContractError(
            "application_evidence_invalid", "application context schema differs"
        )
    return {
        "schema": ENVELOPE_SCHEMA,
        "operation": OPERATION,
        "provider": manifest["provider"],
        "application_identity_sha256": application_identity_sha256,
        "evidence": manifest["evidence"],
        "application_context_ref": manifest["application_context_ref"],
        "application_context_sha256": manifest["application_context_sha256"],
        "application_context_schema": manifest["application_context_schema"],
        "maximum_one_action_calls": manifest["maximum_one_action_calls"],
        "envelope_ref": manifest["envelope_ref"],
        "result_ref": manifest["result_ref"],
        "source_manifest_sha256": manifest_sha256,
    }


def _envelope_from_value(value: object) -> ApplicationEnvelope:
    envelope = _mapping(value, "application envelope")
    _exact_keys(envelope, _ENVELOPE_KEYS, "application envelope")
    if envelope["schema"] != ENVELOPE_SCHEMA or envelope["operation"] != OPERATION:
        raise ApplicationContractError(
            "application_contract_invalid", "application envelope is unsupported"
        )
    provider = envelope["provider"]
    if not isinstance(provider, str) or _PROVIDER_RE.fullmatch(provider) is None:
        raise ApplicationContractError(
            "application_contract_invalid", "provider identity is invalid"
        )
    maximum = envelope["maximum_one_action_calls"]
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, int)
        or not 1 <= maximum <= 256
    ):
        raise ApplicationContractError(
            "application_contract_invalid", "one-action call budget is invalid"
        )
    return ApplicationEnvelope(
        provider=provider,
        application_identity_sha256=_digest(
            envelope["application_identity_sha256"], "application identity"
        ),
        evidence=_evidence_bindings(envelope["evidence"]),
        application_context_ref=_private_reference(
            envelope["application_context_ref"], "application context"
        ),
        application_context_sha256=_digest(
            envelope["application_context_sha256"], "application context"
        ),
        application_context_schema=_schema(
            envelope["application_context_schema"], "application context schema"
        ),
        maximum_one_action_calls=maximum,
        envelope_ref=_private_reference(envelope["envelope_ref"], "envelope reference"),
        result_ref=_private_reference(envelope["result_ref"], "result reference"),
        source_manifest_sha256=_digest(
            envelope["source_manifest_sha256"], "source manifest"
        ),
    )


def load_application_envelope(
    private_root: Path,
    path_value: str | os.PathLike[str],
    expected_sha256: object,
) -> tuple[ApplicationEnvelope, str]:
    path = Path(path_value)
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise ApplicationContractError(
            "application_contract_invalid",
            "application envelope path must be canonical and absolute",
        )
    try:
        relative = path.relative_to(private_root)
        resolved = resolve_private_reference(
            private_root, relative.as_posix(), "application envelope"
        )
        value, raw_bytes = read_private_json(resolved, "application envelope")
    except (IntakeContractError, ValueError) as exc:
        raise _failure(exc, "application_contract_invalid") from exc
    actual_sha256 = sha256_hex(raw_bytes)
    if actual_sha256 != _digest(expected_sha256, "expected envelope digest"):
        raise ApplicationContractError(
            "application_contract_invalid", "application envelope digest differs"
        )
    envelope = _envelope_from_value(value)
    if envelope.envelope_ref != relative.as_posix():
        raise ApplicationContractError(
            "application_contract_invalid", "application envelope reference differs"
        )
    return envelope, actual_sha256


def validate_application_envelope_sources(
    private_root: Path,
    envelope: ApplicationEnvelope,
) -> None:
    for binding in envelope.evidence:
        receipt, _ = _bound_private_json(
            private_root,
            binding.receipt_ref,
            binding.receipt_sha256,
            f"{binding.stage} receipt",
        )
        _exact_keys(receipt, _GATE_RECEIPT_KEYS, f"{binding.stage} receipt")
        if (
            receipt["schema"] != binding.receipt_schema
            or receipt["ok"] is not True
            or receipt["state"] != binding.expected_state
            or receipt["application_identity_sha256"]
            != envelope.application_identity_sha256
        ):
            raise ApplicationContractError(
                "application_evidence_invalid",
                f"{binding.stage} receipt no longer satisfies its gate",
            )
        _digest(receipt["evidence_sha256"], f"{binding.stage} evidence digest")
    context, _ = _bound_private_json(
        private_root,
        envelope.application_context_ref,
        envelope.application_context_sha256,
        "application context",
    )
    if context.get("schema") != envelope.application_context_schema:
        raise ApplicationContractError(
            "application_evidence_invalid", "application context schema differs"
        )


def _optional_digest(value: object, context: str) -> str | None:
    if value is None:
        return None
    return _digest(value, context)


def _accepted_decision(value: object) -> Mapping[str, Any]:
    try:
        decision = _mapping(value, "accepted decision")
        _exact_keys(decision, _ACCEPTED_DECISION_KEYS, "accepted decision")
    except ApplicationContractError as exc:
        raise ApplicationDecisionContractError(
            "accepted decision fields are malformed"
        ) from exc
    action = decision["action"]
    if (
        decision["schema"] != "taey_apply_greenhouse_action_decision_v1"
        or not isinstance(action, str)
        or action not in _DECISION_ACTIONS
        or decision["expected_option_name"] is not None
    ):
        raise ApplicationDecisionContractError("accepted decision is not bounded")
    raw_work_keys = decision["work_evidence_keys"]
    if (
        not isinstance(raw_work_keys, list)
        or len(raw_work_keys) > 256
        or any(
            not isinstance(item, str) or _TOKEN_RE.fullmatch(item) is None
            for item in raw_work_keys
        )
        or len(raw_work_keys) != len(set(raw_work_keys))
    ):
        raise ApplicationDecisionContractError(
            "accepted decision keys are not bounded"
        )
    if action == "halt":
        if (
            decision["ref"] is not None
            or decision["revision"] is not None
            or decision["fact_key"] is not None
            or not isinstance(decision["stop_code"], str)
            or decision["stop_code"] not in STOP_CODES
        ):
            raise ApplicationDecisionContractError(
                "accepted terminal decision is malformed"
            )
        return decision
    ref = decision["ref"]
    if (
        decision["stop_code"] is not None
        or (
            action == "select_option"
            and ref is not None
        )
        or (
            action != "select_option"
            and (not isinstance(ref, str) or _REF_RE.fullmatch(ref) is None)
        )
    ):
        raise ApplicationDecisionContractError(
            "accepted decision authority is malformed"
        )
    try:
        _digest(decision["revision"], "accepted decision revision")
    except ApplicationContractError as exc:
        raise ApplicationDecisionContractError(
            "accepted decision revision is malformed"
        ) from exc
    fact_key = decision["fact_key"]
    if (
        action in _FACT_ACTIONS
        and (not isinstance(fact_key, str) or _TOKEN_RE.fullmatch(fact_key) is None)
    ) or (action not in _FACT_ACTIONS and fact_key is not None):
        raise ApplicationDecisionContractError(
            "accepted decision fact key is malformed"
        )
    return decision


def _decision_response_evidence(
    private_root: Path,
    *,
    reference: object,
    expected_sha256: object,
    expected_application_identity_sha256: str,
    expected_sequence_number: int,
    expected_payload_sha256: object,
) -> Mapping[str, Any]:
    if not isinstance(reference, str):
        raise ApplicationContractError(
            "side_effect_uncertainty", "decision response reference is invalid"
        )
    match = _DECISION_RESPONSE_REF_RE.fullmatch(reference)
    if match is None:
        raise ApplicationContractError(
            "side_effect_uncertainty", "decision response reference is invalid"
        )
    artifact_sha256 = _digest(expected_sha256, "decision response artifact")
    evidence, _ = _bound_private_json(
        private_root,
        reference,
        artifact_sha256,
        "decision response evidence",
    )
    _exact_keys(
        evidence,
        _DECISION_RESPONSE_EVIDENCE_KEYS,
        "decision response evidence",
    )
    response_payload = _mapping(
        evidence["response_payload"], "decision response payload"
    )
    response_payload_sha256 = _digest(
        evidence["response_payload_sha256"], "decision response payload"
    )
    if (
        evidence["schema"] != DECISION_RESPONSE_EVIDENCE_SCHEMA
        or evidence["application_identity_sha256"]
        != expected_application_identity_sha256
        or evidence["seat_id"] != match.group(1)
        or evidence["correlation_id"] != match.group(2)
        or not isinstance(evidence["event_id"], str)
        or _PUBLIC_ID_RE.fullmatch(evidence["event_id"]) is None
        or not evidence["event_id"].endswith(f".s{expected_sequence_number}")
        or not evidence["correlation_id"].endswith(
            f".s{expected_sequence_number}"
        )
        or response_payload_sha256 != expected_payload_sha256
        or response_payload_sha256
        != sha256_hex(canonical_json_bytes(response_payload))
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "decision response evidence differs"
        )
    _digest(evidence["request_payload_sha256"], "decision request payload")
    return evidence


def validate_terminal_outcome_evidence(
    private_root: Path,
    outcome: OneActionOutcome,
    request: OneActionRequest,
) -> Mapping[str, Any]:
    if (
        outcome.state not in {"terminal_halt", "side_effect_uncertain"}
        or outcome.terminal_evidence_ref is None
        or outcome.terminal_evidence_sha256 is None
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "terminal evidence binding is absent"
        )
    evidence, _ = _bound_private_json(
        private_root,
        outcome.terminal_evidence_ref,
        outcome.terminal_evidence_sha256,
        "terminal executor evidence",
    )
    _exact_keys(evidence, _TERMINAL_EVIDENCE_KEYS, "terminal executor evidence")
    if (
        evidence["schema"] != TERMINAL_EVIDENCE_SCHEMA
        or evidence["application_identity_sha256"]
        != request.envelope.application_identity_sha256
        or evidence["envelope_sha256"] != request.envelope_sha256
        or evidence["sequence_number"] != request.sequence_number
        or evidence["previous_receipt_sha256"]
        != request.previous_receipt_sha256
        or evidence["action_id"] != outcome.action_id
        or evidence["state"] != outcome.state
        or evidence["failure_code"] != outcome.stop_code
        or not isinstance(evidence["stage"], str)
        or evidence["stage"] not in TERMINAL_EVIDENCE_STAGES
        or not isinstance(evidence["reason_code"], str)
        or evidence["reason_code"] not in TERMINAL_REASON_CODES
        or isinstance(evidence["mutation_count"], bool)
        or not isinstance(evidence["mutation_count"], int)
        or evidence["mutation_count"] != outcome.mutation_count
        or evidence["mutation_count"] != 0
        or evidence["next_mutation_authorized"] is not False
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "terminal executor evidence differs"
        )
    stage = evidence["stage"]
    reason_code = evidence["reason_code"]
    expected_reasons = {
        "request_lineage": {"request_lineage_mismatch"},
        "decision": {"decision_source_refused"},
        "compile": {"compiler_refused", "taey_explicit_halt"},
        "presence": {"presence_observation_refused"},
    }
    if reason_code not in expected_reasons[stage]:
        raise ApplicationContractError(
            "side_effect_uncertainty", "terminal executor reason is out of stage"
        )
    decision_ref = evidence["accepted_decision_ref"]
    decision_sha256 = evidence["accepted_decision_sha256"]
    if (decision_ref is None) is not (decision_sha256 is None):
        raise ApplicationContractError(
            "side_effect_uncertainty", "accepted decision binding is incomplete"
        )
    accepted_decision: Mapping[str, Any] | None = None
    if decision_ref is not None:
        if (
            not isinstance(decision_ref, str)
            or _ACCEPTED_DECISION_REF_RE.fullmatch(decision_ref) is None
        ):
            raise ApplicationContractError(
                "side_effect_uncertainty", "accepted decision reference is invalid"
            )
        decision_sha256 = _digest(decision_sha256, "accepted decision")
        accepted_decision, _ = _bound_private_json(
            private_root,
            decision_ref,
            decision_sha256,
            "accepted decision",
        )
        accepted_decision = _accepted_decision(accepted_decision)
    response_ref = evidence["decision_response_ref"]
    response_sha256 = evidence["decision_response_sha256"]
    response_payload_sha256 = evidence["decision_response_payload_sha256"]
    rejection_code = evidence["decision_rejection_code"]
    if rejection_code is not None and rejection_code not in DECISION_REJECTION_CODES:
        raise ApplicationContractError(
            "side_effect_uncertainty", "decision rejection code is invalid"
        )
    if (response_ref is None) is not (response_sha256 is None):
        raise ApplicationContractError(
            "side_effect_uncertainty", "decision response binding is incomplete"
        )
    response_evidence: Mapping[str, Any] | None = None
    if response_ref is not None:
        response_payload_sha256 = _digest(
            response_payload_sha256, "decision response payload"
        )
        response_evidence = _decision_response_evidence(
            private_root,
            reference=response_ref,
            expected_sha256=response_sha256,
            expected_application_identity_sha256=(
                request.envelope.application_identity_sha256
            ),
            expected_sequence_number=request.sequence_number,
            expected_payload_sha256=response_payload_sha256,
        )
    elif response_payload_sha256 is not None:
        _digest(response_payload_sha256, "decision response payload")
        if rejection_code != "decision_response_capture_failed":
            raise ApplicationContractError(
                "side_effect_uncertainty",
                "unbound decision response payload is not a capture failure",
            )
    for key in (
        "capsule_sha256",
        "presence_response_payload_sha256",
    ):
        _optional_digest(evidence[key], key)
    if stage == "request_lineage" and any(
        evidence[key] is not None
        for key in (
            "accepted_decision_ref",
            "accepted_decision_sha256",
            "decision_response_ref",
            "decision_response_sha256",
            "decision_response_payload_sha256",
            "decision_rejection_code",
            "capsule_sha256",
            "presence_response_payload_sha256",
        )
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "lineage evidence exceeds its stage"
        )
    if stage != "presence" and evidence["presence_response_payload_sha256"] is not None:
        raise ApplicationContractError(
            "side_effect_uncertainty", "Presence evidence exceeds its stage"
        )
    if stage == "presence" and evidence["presence_response_payload_sha256"] is None:
        raise ApplicationContractError(
            "side_effect_uncertainty", "Presence response digest is absent"
        )
    if reason_code == "taey_explicit_halt" and (
        accepted_decision is None
        or accepted_decision["action"] != "halt"
        or accepted_decision["stop_code"] != outcome.stop_code
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "explicit Taey halt is not reconstructed"
        )
    if accepted_decision is not None and stage not in {"compile", "presence"}:
        raise ApplicationContractError(
            "side_effect_uncertainty", "accepted decision exceeds its stage"
        )
    if stage == "decision":
        if accepted_decision is not None or rejection_code is None:
            raise ApplicationContractError(
                "side_effect_uncertainty", "decision refusal evidence is incomplete"
            )
        if rejection_code == "decision_transport_failure" and any(
            value is not None
            for value in (response_ref, response_sha256, response_payload_sha256)
        ):
            raise ApplicationContractError(
                "side_effect_uncertainty", "transport refusal exceeds its evidence"
            )
        if rejection_code == "decision_response_capture_failed" and (
            response_ref is not None
            or response_sha256 is not None
            or response_payload_sha256 is None
        ):
            raise ApplicationContractError(
                "side_effect_uncertainty", "capture refusal evidence differs"
            )
        if rejection_code not in {
            "decision_transport_failure",
            "decision_response_capture_failed",
        } and response_evidence is None:
            raise ApplicationContractError(
                "side_effect_uncertainty", "malformed decision response is absent"
            )
    elif rejection_code is not None:
        raise ApplicationContractError(
            "side_effect_uncertainty", "decision rejection exceeds its stage"
        )
    if accepted_decision is not None and response_evidence is None:
        raise ApplicationContractError(
            "side_effect_uncertainty", "accepted decision response is absent"
        )
    return evidence


def validate_one_action_outcome(
    outcome: object,
    request: OneActionRequest,
) -> OneActionOutcome:
    if not isinstance(outcome, OneActionOutcome):
        raise ApplicationContractError(
            "side_effect_uncertainty", "one-action executor returned an unknown value"
        )
    if (
        outcome.application_identity_sha256
        != request.envelope.application_identity_sha256
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "one-action application identity differs"
        )
    if _PUBLIC_ID_RE.fullmatch(outcome.action_id) is None:
        raise ApplicationContractError(
            "side_effect_uncertainty", "one-action identity is invalid"
        )
    _digest(outcome.receipt_sha256, "one-action receipt")
    if outcome.previous_receipt_sha256 != request.previous_receipt_sha256:
        raise ApplicationContractError(
            "side_effect_uncertainty", "one-action receipt chain differs"
        )
    if outcome.state not in OUTCOME_STATES:
        raise ApplicationContractError(
            "side_effect_uncertainty", "one-action state is unsupported"
        )
    if outcome.mutation_count not in {0, 1}:
        raise ApplicationContractError(
            "side_effect_uncertainty", "one-action mutation count is invalid"
        )
    proven = outcome.state in {
        "observation_proven",
        "action_proven",
        "employer_confirmation_proven",
    }
    if proven:
        if (
            outcome.stop_code is not None
            or outcome.postcondition_sha256 is None
            or outcome.terminal_evidence_ref is not None
            or outcome.terminal_evidence_sha256 is not None
        ):
            raise ApplicationContractError(
                "side_effect_uncertainty", "proved outcome lacks exact postcondition"
            )
        _digest(outcome.postcondition_sha256, "one-action postcondition")
    if outcome.state == "observation_proven" and (
        outcome.mutation_count != 0 or not outcome.next_mutation_authorized
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "observation outcome is inconsistent"
        )
    if outcome.state == "action_proven" and (
        outcome.mutation_count != 1 or not outcome.next_mutation_authorized
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "action outcome is inconsistent"
        )
    if outcome.state == "employer_confirmation_proven" and (
        outcome.mutation_count != 1
        or outcome.next_mutation_authorized
        or outcome.confirmation is None
    ):
        raise ApplicationContractError(
            "side_effect_uncertainty", "confirmation outcome is inconsistent"
        )
    if outcome.state in {"terminal_halt", "side_effect_uncertain"}:
        if (
            outcome.next_mutation_authorized
            or outcome.stop_code not in STOP_CODES
            or outcome.postcondition_sha256 is not None
            or outcome.confirmation is not None
            or not isinstance(outcome.terminal_evidence_ref, str)
            or _TERMINAL_EVIDENCE_REF_RE.fullmatch(outcome.terminal_evidence_ref)
            is None
        ):
            raise ApplicationContractError(
                "side_effect_uncertainty", "terminal outcome is inconsistent"
            )
        if (
            outcome.state == "side_effect_uncertain"
            and outcome.stop_code != "side_effect_uncertainty"
        ):
            raise ApplicationContractError(
                "side_effect_uncertainty", "uncertain outcome code differs"
            )
        if (
            outcome.state == "terminal_halt"
            and outcome.stop_code == "side_effect_uncertainty"
        ):
            raise ApplicationContractError(
                "side_effect_uncertainty", "terminal halt code is inconsistent"
            )
        evidence_sha256 = _digest(
            outcome.terminal_evidence_sha256, "terminal executor evidence"
        )
        if outcome.receipt_sha256 != evidence_sha256:
            raise ApplicationContractError(
                "side_effect_uncertainty", "terminal receipt binding differs"
            )
    return outcome


__all__ = [
    "ApplicationContractError",
    "ApplicationDecisionContractError",
    "ApplicationEnvelope",
    "ENVELOPE_SCHEMA",
    "EVIDENCE_STATES",
    "EvidenceBinding",
    "LIFECYCLE_SCHEMA",
    "OneActionExecutor",
    "OneActionOutcome",
    "OneActionRequest",
    "OPERATION",
    "OUTCOME_STATES",
    "DECISION_REJECTION_CODES",
    "DECISION_RESPONSE_EVIDENCE_SCHEMA",
    "RECEIPT_SCHEMA",
    "STOP_CODES",
    "TERMINAL_EVIDENCE_SCHEMA",
    "TERMINAL_EVIDENCE_STAGES",
    "TERMINAL_REASON_CODES",
    "build_application_envelope",
    "load_application_envelope",
    "load_application_lifecycle",
    "validate_application_envelope_sources",
    "validate_one_action_outcome",
    "validate_terminal_outcome_evidence",
]
