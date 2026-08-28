from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Protocol
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .application_action_compiler import (
    ApplicationActionCompilerError,
    CompiledGreenhouseAction,
    GreenhouseActionCompiler,
    GreenhouseDecisionContext,
)
from .application_confirmation import EmployerConfirmation
from .application_contract import OneActionOutcome, OneActionRequest
from .contract import (
    IntakeContractError,
    canonical_json_bytes,
    sha256_hex,
    validate_public_id,
)


DECISION_INPUT_SCHEMA = "taey_apply_greenhouse_decision_input_v1"
DECISION_OUTPUT_SCHEMA = "taey_apply_greenhouse_action_decision_v1"
PRESENCE_TOOL_PROFILE = "greenhouse-ats-ui"
PRESENCE_ROUTE = "/v1/greenhouse-ats/one-action"
TAEY_DECISION_ROUTE = "/v1/chat/completions"

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_TURN_RE = re.compile(r"[0-9a-f]{32}")
_STOP_CODES = frozenset(
    {
        "exact_postcondition_failure",
        "unmapped_ui_or_question",
        "missing_truthful_applicant_data",
        "policy_or_authority_boundary",
        "side_effect_uncertainty",
    }
)


class ApplicationExecutorError(RuntimeError):
    def __init__(self, failure_code: str, message: str) -> None:
        super().__init__(message)
        if failure_code not in _STOP_CODES:
            raise ValueError("unsupported application executor failure")
        self.failure_code = failure_code


@dataclass(frozen=True, slots=True)
class JsonHttpResponse:
    status: int
    headers: Mapping[str, str]
    payload: Mapping[str, Any]
    payload_sha256: str


class JsonTransport(Protocol):
    def post(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> JsonHttpResponse: ...


class StructuredDecisionSource(Protocol):
    def decide(
        self,
        context: GreenhouseDecisionContext,
        *,
        event_id: str,
        correlation_id: str,
    ) -> Mapping[str, Any]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _json_object(raw: bytes, context: str) -> Mapping[str, Any]:
    def exact(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        if len(pairs) != len({key for key, _ in pairs}):
            raise ValueError(f"{context} contains duplicate fields")
        return dict(pairs)

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=exact)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ApplicationExecutorError(
            "side_effect_uncertainty", f"{context} is not exact JSON"
        ) from exc
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ApplicationExecutorError(
            "side_effect_uncertainty", f"{context} is not one object"
        )
    return value


def _endpoint(value: object, *, route: str, context: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise ApplicationExecutorError(
            "policy_or_authority_boundary", f"{context} is invalid"
        )
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != route
    ):
        raise ApplicationExecutorError(
            "policy_or_authority_boundary",
            f"{context} must use the exact reviewed route",
        )
    return value


class SingleRequestJsonTransport:
    def __init__(
        self,
        *,
        timeout_seconds: int,
        maximum_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, int)
            or not 1 <= timeout_seconds <= 900
            or isinstance(maximum_response_bytes, bool)
            or not isinstance(maximum_response_bytes, int)
            or not 1024 <= maximum_response_bytes <= 16 * 1024 * 1024
        ):
            raise ApplicationExecutorError(
                "policy_or_authority_boundary", "HTTP transport bounds are invalid"
            )
        self._timeout_seconds = timeout_seconds
        self._maximum_response_bytes = maximum_response_bytes
        self._opener = urllib.request.build_opener(_NoRedirect())

    def post(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> JsonHttpResponse:
        body = canonical_json_bytes(payload)
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers=dict(headers),
        )
        try:
            with self._opener.open(request, timeout=self._timeout_seconds) as response:
                raw = response.read(self._maximum_response_bytes + 1)
                status = int(response.status)
                response_headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
        except (urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            raise ApplicationExecutorError(
                "side_effect_uncertainty",
                "one HTTP request did not return an exact response",
            ) from exc
        if status != 200 or len(raw) > self._maximum_response_bytes:
            raise ApplicationExecutorError(
                "side_effect_uncertainty",
                "one HTTP response exceeded its exact contract",
            )
        exact_payload = _json_object(raw, "HTTP response")
        return JsonHttpResponse(
            status=status,
            headers=response_headers,
            payload=exact_payload,
            payload_sha256=sha256_hex(canonical_json_bytes(exact_payload)),
        )


def _decision_schema(context: GreenhouseDecisionContext) -> dict[str, Any]:
    fact_options = list(context.available_fact_keys)
    evidence_options = list(context.available_work_evidence_keys)
    options_surface = context.surface_capsule.get("surface") == "options"
    work_items: dict[str, Any] = {
        "type": "string",
        "pattern": "^[a-z][a-z0-9_]{0,127}$",
    }
    if evidence_options:
        work_items = {"enum": evidence_options}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema": {"const": DECISION_OUTPUT_SCHEMA},
            "action": (
                {"const": "select_option"}
                if options_surface
                else {
                    "enum": [
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
                    ]
                }
            ),
            "ref": (
                {"type": "null"}
                if options_surface
                else {
                    "oneOf": [
                        {
                            "type": "string",
                            "pattern": "^(?:r_[0-9a-f]{32}|nd1_[0-9a-f]{64})$",
                        },
                        {"type": "null"},
                    ]
                }
            ),
            "revision": {
                "oneOf": [
                    {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    {"type": "null"},
                ]
            },
            "fact_key": {
                "oneOf": [
                    {"enum": fact_options},
                    {"type": "null"},
                ]
            },
            "work_evidence_keys": {
                "type": "array",
                "items": work_items,
                "maxItems": len(evidence_options),
            },
            "expected_option_name": (
                {"type": "null"}
                if options_surface
                else {
                    "oneOf": [
                        {"type": "string", "minLength": 1, "maxLength": 4096},
                        {"type": "null"},
                    ]
                }
            ),
            "stop_code": {
                "oneOf": [
                    {"enum": sorted(_STOP_CODES)},
                    {"type": "null"},
                ]
            },
        },
        "required": [
            "schema",
            "action",
            "ref",
            "revision",
            "fact_key",
            "work_evidence_keys",
            "expected_option_name",
            "stop_code",
        ],
    }


class TaeyJsonSchemaDecisionClient:
    def __init__(
        self,
        *,
        endpoint_value: object,
        model_value: object,
        transport: JsonTransport,
    ) -> None:
        self._endpoint = _endpoint(
            endpoint_value,
            route=TAEY_DECISION_ROUTE,
            context="Taey decision endpoint",
        )
        if (
            not isinstance(model_value, str)
            or not model_value
            or model_value != model_value.strip()
            or len(model_value) > 256
        ):
            raise ApplicationExecutorError(
                "policy_or_authority_boundary", "Taey decision model is invalid"
            )
        self._model = model_value
        self._transport = transport

    def decide(
        self,
        context: GreenhouseDecisionContext,
        *,
        event_id: str,
        correlation_id: str,
    ) -> Mapping[str, Any]:
        safe_input = {
            "schema": DECISION_INPUT_SCHEMA,
            "application_identity_sha256": context.application_identity_sha256,
            "current_surface": context.surface_capsule,
            "available_fact_keys": list(context.available_fact_keys),
            "available_work_evidence_keys": list(context.available_work_evidence_keys),
            "previous_action_kind": context.previous_action_kind,
        }
        schema = _decision_schema(context)
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Choose exactly one next action from the current bounded "
                        "Greenhouse surface. Use only its exact ref and revision and "
                        "only the listed fact or work-evidence keys. For an options "
                        "surface, choose select_option and the relevant fact key; leave "
                        "ref and expected_option_name null so the private compiler can "
                        "resolve exactly one truthful option without exposing its value. "
                        "Follow the "
                        "native chooser sequence one action at a time. Submit only when "
                        "required_controls_complete is true. If current evidence cannot "
                        "prove one action, halt with the exact terminal code."
                    ),
                },
                {
                    "role": "user",
                    "content": canonical_json_bytes(safe_input).decode("utf-8"),
                },
            ],
            "chat_template_kwargs": {"enable_thinking": False},
            "temperature": 0,
            "max_tokens": 768,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "greenhouse_one_action_decision",
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        response = self._transport.post(
            self._endpoint,
            headers={
                "Content-Type": "application/json",
                "X-Taey-Event-Id": event_id,
                "X-Taey-Correlation-Id": correlation_id,
            },
            payload=payload,
        )
        choices = response.payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
        message = choice.get("message") if isinstance(choice, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if (
            not isinstance(choice, Mapping)
            or choice.get("finish_reason") != "stop"
            or not isinstance(message, Mapping)
            or message.get("tool_calls") is not None
            or not isinstance(content, str)
            or not content
        ):
            raise ApplicationExecutorError(
                "unmapped_ui_or_question",
                "Taey did not return one terminal schema-constrained decision",
            )
        try:
            decision = _json_object(content.encode("utf-8"), "Taey schema decision")
        except ApplicationExecutorError as exc:
            raise ApplicationExecutorError(
                "unmapped_ui_or_question",
                "Taey schema decision is not one exact JSON object",
            ) from exc
        if frozenset(decision) != frozenset(schema["required"]):
            raise ApplicationExecutorError(
                "unmapped_ui_or_question", "Taey schema decision fields are not exact"
            )
        return decision


def _digest(value: object, context: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ApplicationExecutorError(
            "side_effect_uncertainty", f"{context} is not an exact digest"
        )
    return value


class GreenhousePresenceOneActionExecutor:
    def __init__(
        self,
        *,
        private_root_value: str,
        seat_id_value: object,
        display_value: object,
        hands_commit_value: object,
        event_id_value: object,
        correlation_id_value: object,
        presence_endpoint_value: object,
        decision_source: StructuredDecisionSource,
        presence_transport: JsonTransport,
    ) -> None:
        try:
            self._seat_id = validate_public_id(seat_id_value, "seat ID")
            self._event_id = validate_public_id(event_id_value, "event ID")
            self._correlation_id = validate_public_id(
                correlation_id_value, "correlation ID"
            )
        except IntakeContractError as exc:
            raise ApplicationExecutorError(
                "policy_or_authority_boundary", "executor lineage is invalid"
            ) from exc
        for value in (self._event_id, self._correlation_id):
            if len(f"{value}.s9999") > 128:
                raise ApplicationExecutorError(
                    "policy_or_authority_boundary",
                    "executor lineage has no action suffix room",
                )
        self._compiler = GreenhouseActionCompiler(
            private_root_value=private_root_value,
            seat_id_value=self._seat_id,
            display_value=display_value,
            hands_commit_value=hands_commit_value,
        )
        self._presence_endpoint = _endpoint(
            presence_endpoint_value,
            route=PRESENCE_ROUTE,
            context="Presence endpoint",
        )
        self._display = str(display_value)
        self._decision_source = decision_source
        self._presence_transport = presence_transport
        self._capsule: Mapping[str, Any] | None = None
        self._previous_action_kind: str | None = None
        self._last_receipt_sha256: str | None = None
        self._expected_sequence = 1
        self._terminal = False

    def _lineage(self, sequence_number: int) -> tuple[str, str]:
        return (
            f"{self._event_id}.s{sequence_number}",
            f"{self._correlation_id}.s{sequence_number}",
        )

    def _terminal_outcome(
        self,
        request: OneActionRequest,
        *,
        failure_code: str,
        action_id: str | None,
        evidence: Mapping[str, Any],
    ) -> OneActionOutcome:
        self._terminal = True
        state = (
            "side_effect_uncertain"
            if failure_code == "side_effect_uncertainty"
            else "terminal_halt"
        )
        if action_id is None:
            action_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "https://palios-taey.org/application-executor-halt/"
                    f"{request.envelope_sha256}/{request.sequence_number}/"
                    f"{failure_code}",
                )
            )
        receipt_sha256 = sha256_hex(
            canonical_json_bytes(
                {
                    "schema": "taey_apply_application_executor_terminal_v1",
                    "application_identity_sha256": (
                        request.envelope.application_identity_sha256
                    ),
                    "envelope_sha256": request.envelope_sha256,
                    "sequence_number": request.sequence_number,
                    "previous_receipt_sha256": request.previous_receipt_sha256,
                    "action_id": action_id,
                    "failure_code": failure_code,
                    "evidence": evidence,
                }
            )
        )
        return OneActionOutcome(
            application_identity_sha256=(request.envelope.application_identity_sha256),
            action_id=action_id,
            previous_receipt_sha256=request.previous_receipt_sha256,
            receipt_sha256=receipt_sha256,
            state=state,
            mutation_count=0,
            postcondition_sha256=None,
            next_mutation_authorized=False,
            stop_code=failure_code,
        )

    def _response_headers_prove(
        self,
        response: JsonHttpResponse,
        compiled: CompiledGreenhouseAction,
    ) -> None:
        expected = {
            "x-taey-seat-id": compiled.seat_id,
            "x-taey-event-id": compiled.event_id,
            "x-taey-correlation-id": compiled.correlation_id,
            "x-taey-tool-profile": PRESENCE_TOOL_PROFILE,
        }
        if any(response.headers.get(key) != value for key, value in expected.items()):
            raise ApplicationExecutorError(
                "side_effect_uncertainty", "Presence response lineage differs"
            )
        if _TURN_RE.fullmatch(response.headers.get("x-taey-turn-id", "")) is None:
            raise ApplicationExecutorError(
                "side_effect_uncertainty", "Presence response turn identity is absent"
            )

    def _success_outcome(
        self,
        request: OneActionRequest,
        compiled: CompiledGreenhouseAction,
        response: JsonHttpResponse,
    ) -> OneActionOutcome:
        payload = response.payload
        if (
            frozenset(payload) != {"ok", "display", "action", "greenhouse_ats_sequence"}
            or payload.get("ok") is not True
            or payload.get("display") != compiled.display
            or payload.get("action") != "operate"
        ):
            raise ApplicationExecutorError(
                "side_effect_uncertainty", "Presence terminal object is not exact"
            )
        sequence = payload.get("greenhouse_ats_sequence")
        if not isinstance(sequence, Mapping):
            raise ApplicationExecutorError(
                "side_effect_uncertainty", "Presence sequence is absent"
            )
        common_keys = {
            "state",
            "postcondition_proven",
            "receipt_event_hash",
            "hands_result_sha256",
            "hands_state",
            "mutation_count",
            "hands_next_mutation_authorized",
            "next_mutation_authorized",
        }
        submit = compiled.action_kind == "submit"
        bounded_key = "employer_confirmation" if submit else "surface_capsule"
        expected_keys = common_keys | {bounded_key}
        expected_mutations = 0 if compiled.action_kind == "observe_form" else 1
        mutation_count = sequence.get("mutation_count")
        if (
            frozenset(sequence) != expected_keys
            or sequence.get("state")
            != ("terminal_employer_confirmation" if submit else "action_receipted")
            or sequence.get("postcondition_proven") is not True
            or sequence.get("hands_state")
            != ("employer_confirmation_proven" if submit else "action_ready")
            or isinstance(mutation_count, bool)
            or not isinstance(mutation_count, int)
            or mutation_count != expected_mutations
            or sequence.get("hands_next_mutation_authorized") is not (not submit)
            or sequence.get("next_mutation_authorized") is not False
        ):
            raise ApplicationExecutorError(
                "side_effect_uncertainty",
                "Presence postcondition object is inconsistent",
            )
        receipt_sha256 = _digest(sequence.get("receipt_event_hash"), "Presence receipt")
        postcondition_sha256 = _digest(
            sequence.get("hands_result_sha256"), "Presence postcondition"
        )
        confirmation = None
        if submit:
            raw_confirmation = sequence.get("employer_confirmation")
            expected_confirmation_keys = {
                "schema",
                "provider",
                "application_identity_sha256",
                "route_id",
                "route_sha256",
                "anchor_sha256",
                "stable_surface_revision",
                "stable_sample_count",
                "observation_samples_sha256",
                "receipt_sha256",
            }
            if (
                not isinstance(raw_confirmation, Mapping)
                or frozenset(raw_confirmation) != expected_confirmation_keys
                or raw_confirmation.get("schema")
                != "ats_greenhouse_employer_confirmation_v1"
                or isinstance(raw_confirmation.get("stable_sample_count"), bool)
                or not isinstance(raw_confirmation.get("stable_sample_count"), int)
            ):
                raise ApplicationExecutorError(
                    "side_effect_uncertainty", "employer confirmation is not exact"
                )
            confirmation = EmployerConfirmation(
                provider=str(raw_confirmation["provider"]),
                application_identity_sha256=str(
                    raw_confirmation["application_identity_sha256"]
                ),
                route_id=str(raw_confirmation["route_id"]),
                route_sha256=str(raw_confirmation["route_sha256"]),
                anchor_sha256=str(raw_confirmation["anchor_sha256"]),
                stable_surface_revision=str(
                    raw_confirmation["stable_surface_revision"]
                ),
                stable_sample_count=int(raw_confirmation["stable_sample_count"]),
                observation_samples_sha256=str(
                    raw_confirmation["observation_samples_sha256"]
                ),
                receipt_sha256=str(raw_confirmation["receipt_sha256"]),
            )
        else:
            capsule = sequence.get("surface_capsule")
            if not isinstance(capsule, Mapping):
                raise ApplicationExecutorError(
                    "side_effect_uncertainty", "next surface capsule is absent"
                )
            self._capsule = dict(capsule)
        state = (
            "employer_confirmation_proven"
            if submit
            else "observation_proven"
            if compiled.action_kind == "observe_form"
            else "action_proven"
        )
        outcome = OneActionOutcome(
            application_identity_sha256=(request.envelope.application_identity_sha256),
            action_id=compiled.action_id,
            previous_receipt_sha256=request.previous_receipt_sha256,
            receipt_sha256=receipt_sha256,
            state=state,
            mutation_count=expected_mutations,
            postcondition_sha256=postcondition_sha256,
            next_mutation_authorized=not submit,
            confirmation=confirmation,
        )
        self._previous_action_kind = compiled.action_kind
        self._last_receipt_sha256 = receipt_sha256
        self._expected_sequence += 1
        if submit:
            self._terminal = True
        return outcome

    def __call__(self, request: OneActionRequest) -> OneActionOutcome:
        if self._terminal:
            raise ApplicationExecutorError(
                "side_effect_uncertainty", "executor is already terminal"
            )
        if (
            not isinstance(request, OneActionRequest)
            or request.sequence_number != self._expected_sequence
            or request.previous_receipt_sha256 != self._last_receipt_sha256
        ):
            return self._terminal_outcome(
                request,
                failure_code="policy_or_authority_boundary",
                action_id=None,
                evidence={"stage": "request_lineage"},
            )
        event_id, correlation_id = self._lineage(request.sequence_number)
        try:
            decision: Mapping[str, Any] | None = None
            capsule: Mapping[str, Any] | None = None
            if request.sequence_number > 1:
                if self._capsule is None or self._previous_action_kind is None:
                    raise ApplicationExecutorError(
                        "side_effect_uncertainty", "current decision evidence is absent"
                    )
                decision_context = self._compiler.decision_context(
                    request,
                    surface_capsule=self._capsule,
                )
                decision = self._decision_source.decide(
                    decision_context,
                    event_id=event_id,
                    correlation_id=correlation_id,
                )
                capsule = self._capsule
            compiled = self._compiler.compile(
                request,
                event_id_value=event_id,
                correlation_id_value=correlation_id,
                surface_capsule=capsule,
                decision=decision,
            )
        except ApplicationActionCompilerError as exc:
            return self._terminal_outcome(
                request,
                failure_code=exc.failure_code,
                action_id=None,
                evidence={"stage": "compile"},
            )
        except ApplicationExecutorError as exc:
            return self._terminal_outcome(
                request,
                failure_code=exc.failure_code,
                action_id=None,
                evidence={"stage": "decision"},
            )
        try:
            response = self._presence_transport.post(
                self._presence_endpoint,
                headers={
                    "Content-Type": "application/json",
                    "X-Taey-Seat-Id": compiled.seat_id,
                    "X-Taey-Event-Id": compiled.event_id,
                    "X-Taey-Correlation-Id": compiled.correlation_id,
                    "X-Taey-Tool-Profile": PRESENCE_TOOL_PROFILE,
                },
                payload={"display": compiled.display},
            )
            self._response_headers_prove(response, compiled)
            if response.payload.get("ok") is not True:
                if compiled.action_kind == "observe_form":
                    return self._terminal_outcome(
                        request,
                        failure_code="exact_postcondition_failure",
                        action_id=compiled.action_id,
                        evidence={
                            "stage": "presence_observe",
                            "payload_sha256": response.payload_sha256,
                        },
                    )
                raise ApplicationExecutorError(
                    "side_effect_uncertainty",
                    "Presence did not prove whether the frozen mutation started",
                )
            return self._success_outcome(request, compiled, response)
        except ApplicationExecutorError:
            self._terminal = True
            raise


__all__ = [
    "ApplicationExecutorError",
    "GreenhousePresenceOneActionExecutor",
    "JsonHttpResponse",
    "JsonTransport",
    "SingleRequestJsonTransport",
    "StructuredDecisionSource",
    "TaeyJsonSchemaDecisionClient",
]
