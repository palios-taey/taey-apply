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
    _NATIVE_SEQUENCE,
    _ensure_seat_parent,
)
from .application_confirmation import EmployerConfirmation
from .application_contract import (
    ApplicationDecisionContractError,
    DECISION_REJECTION_CODES,
    DECISION_RESPONSE_EVIDENCE_SCHEMA,
    OneActionOutcome,
    OneActionRequest,
    TERMINAL_EVIDENCE_SCHEMA,
    _accepted_decision,
)
from .contract import (
    IntakeContractError,
    canonical_json_bytes,
    resolve_private_reference,
    sha256_hex,
    validate_new_receipt_path,
    validate_private_root,
    validate_public_id,
    write_new_private_json,
)


DECISION_INPUT_SCHEMA = "taey_apply_greenhouse_decision_input_v2"
DECISION_OUTPUT_SCHEMA = "taey_apply_greenhouse_action_decision_v1"
DECISION_CANDIDATE_SCHEMA = "taey_apply_greenhouse_action_candidate_v2"
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
_FACT_ACTIONS = frozenset(
    {"focus", "fill", "scroll_combo", "open_combo", "select_option", "activate_choice"}
)
_FORM_FACT_ACTIONS = _FACT_ACTIONS - {"select_option"}
_FORM_NONFACT_ACTIONS = frozenset({"open_upload", "submit"})


class ApplicationExecutorError(RuntimeError):
    def __init__(
        self,
        failure_code: str,
        message: str,
        *,
        decision_rejection_code: str | None = None,
    ) -> None:
        super().__init__(message)
        if failure_code not in _STOP_CODES:
            raise ValueError("unsupported application executor failure")
        if (
            decision_rejection_code is not None
            and decision_rejection_code not in DECISION_REJECTION_CODES
        ):
            raise ValueError("unsupported decision rejection code")
        self.failure_code = failure_code
        self.decision_rejection_code = decision_rejection_code


@dataclass(frozen=True, slots=True)
class JsonHttpResponse:
    status: int
    headers: Mapping[str, str]
    payload: Mapping[str, Any]
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class DecisionResponseCapture:
    reference: str
    artifact_sha256: str
    response_payload_sha256: str


class JsonTransport(Protocol):
    def post(
        self,
        endpoint: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
    ) -> JsonHttpResponse: ...


class DecisionResponseRecorder(Protocol):
    def record(
        self,
        *,
        application_identity_sha256: str,
        event_id: str,
        correlation_id: str,
        request_payload_sha256: str,
        response: JsonHttpResponse,
    ) -> DecisionResponseCapture: ...


class StructuredDecisionSource(Protocol):
    def decide(
        self,
        context: GreenhouseDecisionContext,
        *,
        event_id: str,
        correlation_id: str,
    ) -> Mapping[str, Any]: ...


class PrivateDecisionResponseRecorder:
    def __init__(self, *, private_root_value: str, seat_id_value: object) -> None:
        try:
            self._private_root = validate_private_root(private_root_value)
            self._seat_id = validate_public_id(seat_id_value, "seat ID")
        except IntakeContractError as exc:
            raise ApplicationExecutorError(
                "policy_or_authority_boundary",
                "decision response recorder boundary is invalid",
            ) from exc

    def record(
        self,
        *,
        application_identity_sha256: str,
        event_id: str,
        correlation_id: str,
        request_payload_sha256: str,
        response: JsonHttpResponse,
    ) -> DecisionResponseCapture:
        try:
            application_identity_sha256 = _digest(
                application_identity_sha256, "application identity"
            )
            request_payload_sha256 = _digest(
                request_payload_sha256, "decision request"
            )
            response_payload_sha256 = _digest(
                response.payload_sha256, "decision response"
            )
            if response_payload_sha256 != sha256_hex(
                canonical_json_bytes(response.payload)
            ):
                raise ApplicationExecutorError(
                    "side_effect_uncertainty", "decision response digest differs"
                )
            event_id = validate_public_id(event_id, "event ID")
            correlation_id = validate_public_id(correlation_id, "correlation ID")
            reference = (
                "application-executor-decision-responses/"
                f"{self._seat_id}/{correlation_id}.json"
            )
            parent = _ensure_seat_parent(
                self._private_root,
                "application-executor-decision-responses",
                self._seat_id,
            )
            path = resolve_private_reference(
                self._private_root,
                reference,
                "application-executor-decision-responses",
                must_exist=False,
            )
            path = validate_new_receipt_path(path, self._private_root)
            if path.parent != parent:
                raise IntakeContractError(
                    "unsafe_private_path", "decision response parent differs"
                )
            raw_bytes = write_new_private_json(
                path,
                {
                    "schema": DECISION_RESPONSE_EVIDENCE_SCHEMA,
                    "application_identity_sha256": application_identity_sha256,
                    "seat_id": self._seat_id,
                    "event_id": event_id,
                    "correlation_id": correlation_id,
                    "request_payload_sha256": request_payload_sha256,
                    "response_payload_sha256": response_payload_sha256,
                    "response_payload": dict(response.payload),
                },
            )
        except (
            ApplicationActionCompilerError,
            ApplicationExecutorError,
            IntakeContractError,
        ) as exc:
            raise ApplicationExecutorError(
                "side_effect_uncertainty",
                "decision response evidence write failed",
                decision_rejection_code="decision_response_capture_failed",
            ) from exc
        return DecisionResponseCapture(
            reference=reference,
            artifact_sha256=sha256_hex(raw_bytes),
            response_payload_sha256=response_payload_sha256,
        )


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
    surface = context.surface_capsule.get("surface")
    branches: list[dict[str, Any]] = []

    def add_branch(action: str, authority: Mapping[str, Any]) -> None:
        properties = {
            "schema": {"const": DECISION_CANDIDATE_SCHEMA},
            "action": {"const": action},
            **authority,
        }
        branches.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": list(properties),
            }
        )

    if surface == "options":
        if fact_options:
            add_branch("select_option", {"fact_key": {"enum": fact_options}})
        return {"oneOf": branches}
    if surface == "form":
        action_refs: dict[str, list[str]] = {}
        for control in context.surface_capsule.get("controls", []):
            for operation in control.get("operations", []):
                refs = action_refs.setdefault(operation, [])
                if control["ref"] not in refs:
                    refs.append(control["ref"])
        for action, refs in action_refs.items():
            if action in _FORM_FACT_ACTIONS and fact_options and refs:
                add_branch(
                    action,
                    {"ref": {"enum": refs}, "fact_key": {"enum": fact_options}},
                )
            elif action in _FORM_NONFACT_ACTIONS and refs:
                add_branch(action, {"ref": {"enum": refs}})
    elif surface == "native_dialog":
        native_step = _NATIVE_SEQUENCE.get(context.previous_action_kind or "")
        if native_step is not None:
            mapped = context.surface_capsule.get("mapped", {})
            refs = mapped.get(native_step[1], []) if isinstance(mapped, Mapping) else []
            if len(refs) == 1:
                add_branch(native_step[0], {"ref": {"const": refs[0]["ref"]}})
    if surface in {"form", "native_dialog"}:
        add_branch("halt", {"stop_code": {"enum": sorted(_STOP_CODES)}})
    return {"oneOf": branches}


def _project_decision(
    context: GreenhouseDecisionContext,
    schema: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> Mapping[str, Any]:
    matches: list[Mapping[str, Any]] = []
    for branch in schema.get("oneOf", []):
        properties = branch.get("properties")
        required = branch.get("required")
        if (
            not isinstance(properties, Mapping)
            or not isinstance(required, list)
            or frozenset(candidate) != frozenset(required)
        ):
            continue
        exact = True
        for key, constraint in properties.items():
            value = candidate.get(key)
            if (
                not isinstance(constraint, Mapping)
                or ("const" in constraint and value != constraint["const"])
                or ("enum" in constraint and value not in constraint["enum"])
            ):
                exact = False
                break
        if exact:
            matches.append(branch)
    if len(matches) != 1:
        raise ApplicationDecisionContractError(
            "Taey candidate does not match one current action branch"
        )
    action = candidate["action"]
    projected = {
        "schema": DECISION_OUTPUT_SCHEMA,
        "action": action,
        "ref": None if action in {"halt", "select_option"} else candidate.get("ref"),
        "revision": (
            None if action == "halt" else context.surface_capsule.get("revision")
        ),
        "fact_key": candidate.get("fact_key") if action in _FACT_ACTIONS else None,
        "work_evidence_keys": [],
        "expected_option_name": None,
        "stop_code": candidate.get("stop_code") if action == "halt" else None,
    }
    return _accepted_decision(projected)


class TaeyJsonSchemaDecisionClient:
    def __init__(
        self,
        *,
        endpoint_value: object,
        model_value: object,
        transport: JsonTransport,
        response_recorder: DecisionResponseRecorder,
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
        self._response_recorder = response_recorder
        self._last_response_ref: str | None = None
        self._last_response_sha256: str | None = None
        self._last_response_payload_sha256: str | None = None
        self._last_rejection_code: str | None = None

    @property
    def last_response_ref(self) -> str | None:
        return self._last_response_ref

    @property
    def last_response_sha256(self) -> str | None:
        return self._last_response_sha256

    @property
    def last_response_payload_sha256(self) -> str | None:
        return self._last_response_payload_sha256

    @property
    def last_rejection_code(self) -> str | None:
        return self._last_rejection_code

    def _reject(self, code: str, message: str) -> None:
        self._last_rejection_code = code
        raise ApplicationExecutorError(
            "unmapped_ui_or_question",
            message,
            decision_rejection_code=code,
        )

    def decide(
        self,
        context: GreenhouseDecisionContext,
        *,
        event_id: str,
        correlation_id: str,
    ) -> Mapping[str, Any]:
        self._last_response_ref = None
        self._last_response_sha256 = None
        self._last_response_payload_sha256 = None
        self._last_rejection_code = "decision_transport_failure"
        safe_input = {
            "schema": DECISION_INPUT_SCHEMA,
            "application_identity_sha256": context.application_identity_sha256,
            "current_surface": context.surface_capsule,
            "available_fact_keys": list(context.available_fact_keys),
            "previous_action_kind": context.previous_action_kind,
        }
        schema = _decision_schema(context)
        if not schema["oneOf"]:
            self._reject(
                "decision_transport_failure",
                "current surface has no exact model decision branch",
            )
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Choose exactly one next action from the current bounded "
                        "Greenhouse surface. Use only the action-scoped exact ref and "
                        "listed fact keys admitted by the response schema. For an options "
                        "surface, choose select_option and the relevant fact key so the "
                        "private compiler can resolve exactly one truthful option without "
                        "exposing its value. Follow the "
                        "native chooser sequence one action at a time. Submit only when "
                        "required_controls_complete is true. If current evidence cannot "
                        "prove one action and halt is admitted, halt with the exact "
                        "terminal code."
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
        request_payload_sha256 = sha256_hex(canonical_json_bytes(payload))
        try:
            response = self._transport.post(
                self._endpoint,
                headers={
                    "Content-Type": "application/json",
                    "X-Taey-Event-Id": event_id,
                    "X-Taey-Correlation-Id": correlation_id,
                },
                payload=payload,
            )
        except ApplicationExecutorError as exc:
            raise ApplicationExecutorError(
                exc.failure_code,
                "Taey decision transport failed",
                decision_rejection_code="decision_transport_failure",
            ) from exc
        self._last_response_payload_sha256 = response.payload_sha256
        self._last_rejection_code = "decision_response_capture_failed"
        capture = self._response_recorder.record(
            application_identity_sha256=context.application_identity_sha256,
            event_id=event_id,
            correlation_id=correlation_id,
            request_payload_sha256=request_payload_sha256,
            response=response,
        )
        self._last_response_ref = capture.reference
        self._last_response_sha256 = capture.artifact_sha256
        self._last_response_payload_sha256 = capture.response_payload_sha256
        self._last_rejection_code = None
        choices = response.payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
        message = choice.get("message") if isinstance(choice, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        tool_calls = message.get("tool_calls") if isinstance(message, Mapping) else None
        if (
            not isinstance(choice, Mapping)
            or choice.get("finish_reason") != "stop"
            or not isinstance(message, Mapping)
            or not (
                tool_calls is None
                or (isinstance(tool_calls, list) and len(tool_calls) == 0)
            )
        ):
            self._reject(
                "decision_response_envelope_malformed",
                "Taey did not return one terminal schema-constrained decision",
            )
        if not isinstance(content, str) or not content:
            self._reject(
                "decision_response_content_malformed",
                "Taey decision content is absent",
            )
        try:
            decision = _json_object(content.encode("utf-8"), "Taey schema decision")
        except ApplicationExecutorError as exc:
            self._last_rejection_code = "decision_response_content_malformed"
            raise ApplicationExecutorError(
                "unmapped_ui_or_question",
                "Taey schema decision is not one exact JSON object",
                decision_rejection_code="decision_response_content_malformed",
            ) from exc
        action_branches = [
            branch
            for branch in schema["oneOf"]
            if branch["properties"]["action"]["const"] == decision.get("action")
        ]
        if action_branches and all(
            frozenset(decision) != frozenset(branch["required"])
            for branch in action_branches
        ):
            self._reject(
                "decision_fields_malformed",
                "Taey candidate fields do not match its action branch",
            )
        try:
            return _project_decision(context, schema, decision)
        except ApplicationDecisionContractError as exc:
            self._last_rejection_code = exc.rejection_code
            raise ApplicationExecutorError(
                "unmapped_ui_or_question",
                "Taey schema decision violates its bounded authority",
                decision_rejection_code=exc.rejection_code,
            ) from exc


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
            self._private_root = validate_private_root(private_root_value)
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

    def _private_artifact(
        self,
        *,
        bucket: str,
        identity: str,
        value: Mapping[str, Any],
    ) -> tuple[str, str]:
        reference = f"{bucket}/{self._seat_id}/{identity}.json"
        try:
            parent = _ensure_seat_parent(
                self._private_root, bucket, self._seat_id
            )
            path = resolve_private_reference(
                self._private_root,
                reference,
                bucket,
                must_exist=False,
            )
            path = validate_new_receipt_path(path, self._private_root)
            if path.parent != parent:
                raise IntakeContractError(
                    "unsafe_private_path", "private artifact parent differs"
                )
            raw_bytes = write_new_private_json(path, dict(value))
        except (ApplicationActionCompilerError, IntakeContractError) as exc:
            raise ApplicationExecutorError(
                "side_effect_uncertainty", "private executor evidence write failed"
            ) from exc
        return reference, sha256_hex(raw_bytes)

    def _decision_response_evidence(
        self, *, attempted: bool
    ) -> tuple[str | None, str | None, str | None, str | None]:
        if not attempted:
            return None, None, None, None
        reference = getattr(self._decision_source, "last_response_ref", None)
        artifact_sha256 = getattr(
            self._decision_source, "last_response_sha256", None
        )
        payload_sha256 = getattr(
            self._decision_source, "last_response_payload_sha256", None
        )
        rejection_code = getattr(
            self._decision_source, "last_rejection_code", None
        )
        if rejection_code is not None and rejection_code not in DECISION_REJECTION_CODES:
            raise ApplicationExecutorError(
                "side_effect_uncertainty", "decision rejection evidence is invalid"
            )
        if rejection_code == "decision_transport_failure":
            if any(
                value is not None
                for value in (reference, artifact_sha256, payload_sha256)
            ):
                raise ApplicationExecutorError(
                    "side_effect_uncertainty",
                    "decision transport evidence is inconsistent",
                )
            return None, None, None, rejection_code
        if rejection_code == "decision_response_capture_failed":
            if reference is not None or artifact_sha256 is not None:
                raise ApplicationExecutorError(
                    "side_effect_uncertainty",
                    "decision capture failure evidence is inconsistent",
                )
            return (
                None,
                None,
                _digest(payload_sha256, "Taey decision response"),
                rejection_code,
            )
        if (
            not isinstance(reference, str)
            or not reference
            or artifact_sha256 is None
            or payload_sha256 is None
        ):
            raise ApplicationExecutorError(
                "side_effect_uncertainty", "decision response evidence is absent"
            )
        return (
            reference,
            _digest(artifact_sha256, "Taey decision response artifact"),
            _digest(payload_sha256, "Taey decision response"),
            rejection_code,
        )

    def _persist_accepted_decision(
        self,
        decision: Mapping[str, Any] | None,
        correlation_id: str,
    ) -> tuple[str | None, str | None, Mapping[str, Any] | None]:
        if decision is None:
            return None, None, None
        exact_decision = _accepted_decision(decision)
        reference, decision_sha256 = self._private_artifact(
            bucket="application-executor-decisions",
            identity=correlation_id,
            value=exact_decision,
        )
        return reference, decision_sha256, exact_decision

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
        stage: str,
        reason_code: str,
        accepted_decision_ref: str | None = None,
        accepted_decision_sha256: str | None = None,
        decision_response_ref: str | None = None,
        decision_response_sha256: str | None = None,
        decision_response_payload_sha256: str | None = None,
        decision_rejection_code: str | None = None,
        capsule_sha256: str | None = None,
        presence_response_payload_sha256: str | None = None,
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
        evidence = {
            "schema": TERMINAL_EVIDENCE_SCHEMA,
            "application_identity_sha256": (
                request.envelope.application_identity_sha256
            ),
            "envelope_sha256": request.envelope_sha256,
            "sequence_number": request.sequence_number,
            "previous_receipt_sha256": request.previous_receipt_sha256,
            "action_id": action_id,
            "state": state,
            "failure_code": failure_code,
            "stage": stage,
            "reason_code": reason_code,
            "accepted_decision_ref": accepted_decision_ref,
            "accepted_decision_sha256": accepted_decision_sha256,
            "decision_response_ref": decision_response_ref,
            "decision_response_sha256": decision_response_sha256,
            "decision_response_payload_sha256": (
                decision_response_payload_sha256
            ),
            "decision_rejection_code": decision_rejection_code,
            "capsule_sha256": capsule_sha256,
            "presence_response_payload_sha256": (
                presence_response_payload_sha256
            ),
            "mutation_count": 0,
            "next_mutation_authorized": False,
        }
        evidence_ref, evidence_sha256 = self._private_artifact(
            bucket="application-executor-outcomes",
            identity=action_id,
            value=evidence,
        )
        return OneActionOutcome(
            application_identity_sha256=(request.envelope.application_identity_sha256),
            action_id=action_id,
            previous_receipt_sha256=request.previous_receipt_sha256,
            receipt_sha256=evidence_sha256,
            state=state,
            mutation_count=0,
            postcondition_sha256=None,
            next_mutation_authorized=False,
            stop_code=failure_code,
            terminal_evidence_ref=evidence_ref,
            terminal_evidence_sha256=evidence_sha256,
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
                stage="request_lineage",
                reason_code="request_lineage_mismatch",
            )
        event_id, correlation_id = self._lineage(request.sequence_number)
        decision: Mapping[str, Any] | None = None
        decision_attempted = False
        capsule: Mapping[str, Any] | None = None
        capsule_sha256: str | None = None
        try:
            if request.sequence_number > 1:
                if self._capsule is None or self._previous_action_kind is None:
                    raise ApplicationExecutorError(
                        "side_effect_uncertainty", "current decision evidence is absent"
                    )
                capsule = self._capsule
                capsule_sha256 = sha256_hex(canonical_json_bytes(capsule))
                decision_context = self._compiler.decision_context(
                    request,
                    surface_capsule=capsule,
                )
                decision_attempted = True
                decision = self._decision_source.decide(
                    decision_context,
                    event_id=event_id,
                    correlation_id=correlation_id,
                )
            compiled = self._compiler.compile(
                request,
                event_id_value=event_id,
                correlation_id_value=correlation_id,
                surface_capsule=capsule,
                decision=decision,
            )
        except ApplicationActionCompilerError as exc:
            decision_ref, decision_sha256, exact_decision = (
                self._persist_accepted_decision(decision, correlation_id)
            )
            (
                response_ref,
                response_sha256,
                response_payload_sha256,
                rejection_code,
            ) = self._decision_response_evidence(attempted=decision_attempted)
            if rejection_code is not None:
                raise ApplicationExecutorError(
                    "side_effect_uncertainty",
                    "accepted decision carries rejection evidence",
                )
            explicit_halt = (
                exact_decision is not None
                and exact_decision["action"] == "halt"
                and exact_decision["stop_code"] == exc.failure_code
            )
            return self._terminal_outcome(
                request,
                failure_code=exc.failure_code,
                action_id=None,
                stage="compile",
                reason_code=(
                    "taey_explicit_halt" if explicit_halt else "compiler_refused"
                ),
                accepted_decision_ref=decision_ref,
                accepted_decision_sha256=decision_sha256,
                decision_response_ref=response_ref,
                decision_response_sha256=response_sha256,
                decision_response_payload_sha256=response_payload_sha256,
                capsule_sha256=capsule_sha256,
            )
        except ApplicationExecutorError as exc:
            (
                response_ref,
                response_sha256,
                response_payload_sha256,
                rejection_code,
            ) = self._decision_response_evidence(attempted=decision_attempted)
            if exc.decision_rejection_code != rejection_code:
                raise ApplicationExecutorError(
                    "side_effect_uncertainty",
                    "decision rejection lineage differs",
                ) from exc
            return self._terminal_outcome(
                request,
                failure_code=exc.failure_code,
                action_id=None,
                stage="decision",
                reason_code="decision_source_refused",
                decision_response_ref=response_ref,
                decision_response_sha256=response_sha256,
                decision_response_payload_sha256=response_payload_sha256,
                decision_rejection_code=rejection_code,
                capsule_sha256=capsule_sha256,
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
                        stage="presence",
                        reason_code="presence_observation_refused",
                        presence_response_payload_sha256=response.payload_sha256,
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
    "PrivateDecisionResponseRecorder",
    "SingleRequestJsonTransport",
    "StructuredDecisionSource",
    "TaeyJsonSchemaDecisionClient",
]
