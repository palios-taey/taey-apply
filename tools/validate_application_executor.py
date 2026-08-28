#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict
import argparse
import hashlib
import json
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from taey_apply.application_action_compiler import (  # noqa: E402
    GreenhouseDecisionContext,
    REQUIRED_HANDS_COMMIT,
)
from taey_apply.application_contract import (  # noqa: E402
    OneActionOutcome,
    OneActionRequest,
    load_application_envelope,
    validate_terminal_outcome_evidence,
)
from taey_apply.application_executor import (  # noqa: E402
    ApplicationExecutorError,
    GreenhousePresenceOneActionExecutor,
    JsonHttpResponse,
    PrivateDecisionResponseRecorder,
    TaeyJsonSchemaDecisionClient,
    _decision_schema,
)
from taey_apply.application_materializer import materialize_application_context  # noqa: E402
from taey_apply.application_preparer import prepare_application  # noqa: E402
from taey_apply import application_execute_cli  # noqa: E402
from taey_apply.contract import (  # noqa: E402
    IntakeContractError,
    canonical_json_bytes,
    write_new_private_json,
)
from validate_application_action_compiler import (  # noqa: E402
    form_capsule,
    native_capsule,
    native_ref,
    options_capsule,
    ref,
)
from validate_application_materializer import PRIVATE_SENTINEL, fixture  # noqa: E402


DECISION_ENDPOINT = "https://taey.invalid/v1/chat/completions"
PRESENCE_ENDPOINT = "https://presence.invalid/v1/greenhouse-ats/one-action"


def digest(value: str | bytes) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def require(value: object, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def envelope_fixture(root: Path) -> tuple[Any, str, str]:
    source, source_sha256, identity = fixture(root, "executor")
    materialized = materialize_application_context(
        private_root_value=root,
        manifest_path_value=source,
        expected_manifest_sha256=source_sha256,
        seat_id_value="executor-life",
        correlation_id_value="executor-life",
    )
    lifecycle = (
        root / "application-materializations/executor-life/executor-life.lifecycle.json"
    )
    prepared = prepare_application(
        private_root_value=root,
        lifecycle_path_value=lifecycle,
        expected_lifecycle_sha256=materialized["lifecycle_sha256"],
        seat_id_value="executor-life",
        correlation_id_value="executor-life",
    )
    envelope, envelope_sha256 = load_application_envelope(
        root,
        root / "application-envelopes/executor-life/executor-life.json",
        prepared["envelope_sha256"],
    )
    return envelope, envelope_sha256, identity


def decision(
    action: str, control_ref: str | None, revision: str | None, fact_key: str | None
) -> dict[str, Any]:
    return {
        "schema": "taey_apply_greenhouse_action_decision_v1",
        "action": action,
        "ref": control_ref,
        "revision": revision,
        "fact_key": fact_key,
        "work_evidence_keys": [],
        "expected_option_name": None,
        "stop_code": None,
    }


def candidate(
    action: str,
    *,
    control_ref: str | None = None,
    fact_key: str | None = None,
    stop_code: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": "taey_apply_greenhouse_action_candidate_v2",
        "action": action,
    }
    if control_ref is not None:
        value["ref"] = control_ref
    if fact_key is not None:
        value["fact_key"] = fact_key
    if stop_code is not None:
        value["stop_code"] = stop_code
    return value


class FrozenDecisionSource:
    def __init__(self, value: Mapping[str, Any]) -> None:
        self.value = value
        self.calls: list[dict[str, Any]] = []

    def decide(
        self, context: GreenhouseDecisionContext, *, event_id: str, correlation_id: str
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "context": asdict(context),
                "event_id": event_id,
                "correlation_id": correlation_id,
            }
        )
        return self.value


class PresenceTransport:
    def __init__(
        self,
        capsule: Mapping[str, Any],
        *,
        refusal: bool = False,
        wrong_second_header: bool = False,
    ) -> None:
        self.capsule = capsule
        self.refusal = refusal
        self.wrong_second_header = wrong_second_header
        self.calls: list[dict[str, Any]] = []

    def post(
        self, endpoint: str, *, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> JsonHttpResponse:
        self.calls.append(
            {"endpoint": endpoint, "headers": dict(headers), "payload": dict(payload)}
        )
        number = len(self.calls)
        request_headers = {key.lower(): value for key, value in headers.items()}
        response_headers = {
            "x-taey-turn-id": f"{number:032x}",
            "x-taey-seat-id": request_headers["x-taey-seat-id"],
            "x-taey-event-id": request_headers["x-taey-event-id"],
            "x-taey-correlation-id": request_headers["x-taey-correlation-id"],
            "x-taey-tool-profile": request_headers["x-taey-tool-profile"],
        }
        if self.wrong_second_header and number == 2:
            response_headers["x-taey-correlation-id"] = "wrong-lineage"
        if self.refusal:
            body: dict[str, Any] = {
                "ok": False,
                "display": payload["display"],
                "action": "observe",
                "greenhouse_ats_sequence": {
                    "state": "terminal_refusal",
                    "first_failure": {"reason": "bounded observation failed"},
                    "next_mutation_authorized": False,
                },
            }
        else:
            body = {
                "ok": True,
                "display": payload["display"],
                "action": "operate",
                "greenhouse_ats_sequence": {
                    "state": "action_receipted",
                    "postcondition_proven": True,
                    "receipt_event_hash": f"{number:064x}",
                    "hands_result_sha256": f"{number + 100:064x}",
                    "hands_state": "action_ready",
                    "mutation_count": 0 if number == 1 else 1,
                    "hands_next_mutation_authorized": True,
                    "next_mutation_authorized": False,
                    "surface_capsule": dict(self.capsule),
                },
            }
        return JsonHttpResponse(
            200, response_headers, body, digest(canonical_json_bytes(body))
        )


class DecisionTransport:
    def __init__(
        self,
        content: object,
        *,
        tool_calls: object = None,
        body: Mapping[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.body = body
        self.last_body: Mapping[str, Any] | None = None
        self.calls: list[dict[str, Any]] = []

    def post(
        self, endpoint: str, *, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> JsonHttpResponse:
        self.calls.append(
            {"endpoint": endpoint, "headers": dict(headers), "payload": dict(payload)}
        )
        body = dict(self.body) if self.body is not None else {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": self.content,
                        "tool_calls": self.tool_calls,
                    },
                }
            ]
        }
        self.last_body = body
        return JsonHttpResponse(200, {}, body, digest(canonical_json_bytes(body)))


def decision_client(
    root: Path, seat: str, transport: DecisionTransport
) -> TaeyJsonSchemaDecisionClient:
    return TaeyJsonSchemaDecisionClient(
        endpoint_value=DECISION_ENDPOINT,
        model_value="taey-production",
        transport=transport,
        response_recorder=PrivateDecisionResponseRecorder(
            private_root_value=str(root),
            seat_id_value=seat,
        ),
    )


def executor(
    root: Path,
    capsule: Mapping[str, Any],
    source: Any,
    transport: PresenceTransport,
    seat: str,
) -> GreenhousePresenceOneActionExecutor:
    return GreenhousePresenceOneActionExecutor(
        private_root_value=str(root),
        seat_id_value=seat,
        display_value=":17",
        hands_commit_value=REQUIRED_HANDS_COMMIT,
        event_id_value=f"{seat}-event",
        correlation_id_value=f"{seat}-correlation",
        presence_endpoint_value=PRESENCE_ENDPOINT,
        decision_source=source,
        presence_transport=transport,
    )


def require_candidate_refusal(
    root: Path,
    context: GreenhouseDecisionContext,
    content: str,
    seat: str,
    *,
    expected_calls: int = 1,
    expected_code: str = "decision_fields_malformed",
) -> None:
    transport = DecisionTransport(content)
    client = decision_client(root, seat, transport)
    try:
        client.decide(
            context,
            event_id=f"{seat}-event",
            correlation_id=f"{seat}-correlation",
        )
    except ApplicationExecutorError as exc:
        require(
            exc.failure_code == "unmapped_ui_or_question"
            and exc.decision_rejection_code == expected_code,
            f"{seat} refusal class drifted",
        )
    else:
        raise RuntimeError(f"{seat} candidate was accepted")
    require(
        len(transport.calls) == expected_calls, f"{seat} decision request count drifted"
    )


def terminal_evidence(
    root: Path, outcome: OneActionOutcome
) -> tuple[Mapping[str, Any], Path]:
    require(
        outcome.terminal_evidence_ref is not None
        and outcome.terminal_evidence_sha256 == outcome.receipt_sha256,
        "terminal outcome lacks exact evidence binding",
    )
    path = root / str(outcome.terminal_evidence_ref)
    raw_bytes = path.read_bytes()
    value = json.loads(raw_bytes)
    require(
        raw_bytes == canonical_json_bytes(value)
        and digest(raw_bytes) == outcome.terminal_evidence_sha256
        and stat.S_IMODE(path.stat().st_mode) == 0o400
        and PRIVATE_SENTINEL.encode() not in raw_bytes,
        "terminal executor evidence is not immutable and bounded",
    )
    return value, path


def success_case(
    root: Path,
    envelope: Any,
    envelope_sha256: str,
    identity: str,
    capsule: Mapping[str, Any],
) -> None:
    source = FrozenDecisionSource(
        decision("fill", ref(1), str(capsule["revision"]), "full_name")
    )
    presence = PresenceTransport(capsule)
    instance = executor(root, capsule, source, presence, "executor-success")
    observed = instance(OneActionRequest(envelope, envelope_sha256, 1, None))
    acted = instance(
        OneActionRequest(envelope, envelope_sha256, 2, observed.receipt_sha256)
    )
    require(
        observed.state == "observation_proven" and observed.mutation_count == 0,
        "observe outcome drifted",
    )
    require(
        acted.state == "action_proven" and acted.mutation_count == 1,
        "action outcome drifted",
    )
    require(
        len(presence.calls) == 2 and len(source.calls) == 1, "one-call cadence drifted"
    )
    for number, call in enumerate(presence.calls, start=1):
        require(
            call["endpoint"] == PRESENCE_ENDPOINT
            and call["payload"] == {"display": ":17"},
            "Presence route/body widened",
        )
        require(
            call["headers"]
            == {
                "Content-Type": "application/json",
                "X-Taey-Seat-Id": "executor-success",
                "X-Taey-Event-Id": f"executor-success-event.s{number}",
                "X-Taey-Correlation-Id": f"executor-success-correlation.s{number}",
                "X-Taey-Tool-Profile": "greenhouse-ats-ui",
            },
            "Presence identity headers drifted",
        )
    decision_bytes = canonical_json_bytes(source.calls[0])
    require(
        PRIVATE_SENTINEL.encode() not in decision_bytes
        and str(root).encode() not in decision_bytes,
        "private bytes reached Taey",
    )
    frozen = json.loads(
        (
            root / "actions/executor-success/executor-success-correlation.s2.json"
        ).read_bytes()
    )
    require(
        frozen["action"]["value"] == PRIVATE_SENTINEL,
        "private value was not copied after decision",
    )


def schema_case(root: Path, identity: str, capsule: Mapping[str, Any]) -> None:
    exact = decision("fill", ref(1), str(capsule["revision"]), "full_name")
    exact_candidate = candidate("fill", control_ref=ref(1), fact_key="full_name")
    transport = DecisionTransport(canonical_json_bytes(exact_candidate).decode())
    client = decision_client(root, "schema-exact", transport)
    context = GreenhouseDecisionContext(
        identity, capsule, ("full_name",), ("automation",), "observe_form"
    )
    require(
        client.decide(context, event_id="event", correlation_id="correlation") == exact,
        "schema decision changed",
    )
    require(
        len(transport.calls) == 1
        and client.last_response_payload_sha256
        == digest(
            canonical_json_bytes(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": canonical_json_bytes(
                                    exact_candidate
                                ).decode(),
                                "tool_calls": None,
                            },
                        }
                    ]
                }
            )
        ),
        "decision response digest was not retained",
    )
    empty_tool_calls = DecisionTransport(
        canonical_json_bytes(exact_candidate).decode(), tool_calls=[]
    )
    empty_tool_calls_client = decision_client(
        root, "schema-empty-tools", empty_tool_calls
    )
    require(
        empty_tool_calls_client.decide(
            context,
            event_id="event-empty-tools",
            correlation_id="correlation-empty-tools",
        )
        == exact,
        "empty tool-call list was not accepted",
    )
    require(len(empty_tool_calls.calls) == 1, "empty tool-call decision retried")
    nonempty_tool_calls = DecisionTransport(
        canonical_json_bytes(exact_candidate).decode(),
        tool_calls=[{"id": "call_1", "type": "function"}],
    )
    nonempty_tool_calls_client = decision_client(
        root, "schema-nonempty-tools", nonempty_tool_calls
    )
    try:
        nonempty_tool_calls_client.decide(
            context,
            event_id="event-nonempty-tools",
            correlation_id="correlation-nonempty-tools",
        )
    except ApplicationExecutorError as exc:
        require(
            exc.failure_code == "unmapped_ui_or_question",
            "nonempty tool-call refusal code drifted",
        )
        require(
            exc.decision_rejection_code
            == "decision_response_envelope_malformed",
            "nonempty tool-call rejection class drifted",
        )
    else:
        raise RuntimeError("nonempty tool-call list was accepted")
    require(len(nonempty_tool_calls.calls) == 1, "nonempty tool-call decision retried")
    payload = transport.calls[0]["payload"]
    response_format = payload["response_format"]
    candidate_schema = response_format["json_schema"]["schema"]
    fill_branch = next(
        item
        for item in candidate_schema["oneOf"]
        if item["properties"]["action"] == {"const": "fill"}
    )
    halt_branch = next(
        item
        for item in candidate_schema["oneOf"]
        if item["properties"]["action"] == {"const": "halt"}
    )
    require(
        response_format["type"] == "json_schema"
        and response_format["json_schema"]["strict"] is True
        and fill_branch["required"] == ["schema", "action", "ref", "fact_key"]
        and fill_branch["properties"]["ref"] == {"enum": [ref(1)]}
        and fill_branch["properties"]["fact_key"] == {"enum": ["full_name"]}
        and halt_branch["required"] == ["schema", "action", "stop_code"]
        and all(
            field not in canonical_json_bytes(candidate_schema).decode()
            for field in (
                "revision",
                "expected_option_name",
                "work_evidence_keys",
            )
        )
        and payload["chat_template_kwargs"] == {"enable_thinking": False}
        and "tools" not in payload,
        "candidate schema contract drifted",
    )
    require(
        PRIVATE_SENTINEL.encode() not in canonical_json_bytes(payload),
        "private value reached schema request",
    )
    option_revision = digest("executor-options")
    options = options_capsule(identity, option_revision, ref(2), ref(3))
    option_value = decision("select_option", None, option_revision, "country")
    option_candidate = candidate("select_option", fact_key="country")
    option_transport = DecisionTransport(
        canonical_json_bytes(option_candidate).decode()
    )
    option_client = decision_client(root, "schema-option", option_transport)
    option_context = GreenhouseDecisionContext(
        identity,
        options,
        ("country",),
        (),
        "open_combo",
    )
    require(
        option_client.decide(
            option_context,
            event_id="event-options",
            correlation_id="correlation-options",
        )
        == option_value,
        "private option-resolution decision changed",
    )
    option_schema = option_transport.calls[0]["payload"]["response_format"][
        "json_schema"
    ]["schema"]
    option_branch = option_schema["oneOf"][0]
    require(
        len(option_schema["oneOf"]) == 1
        and option_branch["required"] == ["schema", "action", "fact_key"]
        and option_branch["properties"]["action"] == {"const": "select_option"}
        and option_branch["properties"]["fact_key"] == {"enum": ["country"]}
        and "halt" not in canonical_json_bytes(option_schema).decode(),
        "private option-resolution schema widened",
    )
    repeated_operations = form_capsule(
        identity,
        digest("executor-repeated-operations"),
        [
            {
                "ref": ref(4),
                "name": "First mapped control",
                "role": "entry",
                "operations": ["focus", "fill"],
            },
            {
                "ref": ref(5),
                "name": "Second mapped control",
                "role": "combo box",
                "operations": ["focus", "open_combo"],
            },
        ],
        required_complete=False,
    )
    repeated_schema = _decision_schema(
        GreenhouseDecisionContext(
            identity,
            repeated_operations,
            ("full_name",),
            (),
            "observe_form",
        )
    )
    require(
        [item["properties"]["action"]["const"] for item in repeated_schema["oneOf"]]
        == ["focus", "fill", "open_combo", "halt"]
        and repeated_schema["oneOf"][0]["properties"]["ref"]
        == {"enum": [ref(4), ref(5)]}
        and repeated_schema["oneOf"][1]["properties"]["ref"] == {"enum": [ref(4)]}
        and repeated_schema["oneOf"][2]["properties"]["ref"] == {"enum": [ref(5)]},
        "form operations were not exact, unique, and first-seen",
    )
    native_steps = (
        ("open_upload", "chooser_location", "chooser_widget"),
        ("chooser_location", "chooser_select_all", "location_entry"),
        ("chooser_select_all", "chooser_type_path", "location_entry"),
        ("chooser_type_path", "chooser_confirm", "open_button"),
    )
    for number, (previous, expected, mapped_key) in enumerate(native_steps, start=1):
        native_schema = _decision_schema(
            GreenhouseDecisionContext(
                identity,
                native_capsule(
                    identity,
                    digest(f"executor-native-{number}"),
                    mapped_key,
                    native_ref(number),
                ),
                (),
                (),
                previous,
            )
        )
        require(
            [item["properties"]["action"]["const"] for item in native_schema["oneOf"]]
            == [expected, "halt"]
            and native_schema["oneOf"][0]["properties"]["ref"]
            == {"const": native_ref(number)},
            "native action schema exceeded the evidenced sequence",
        )
    halt_candidate = candidate("halt", stop_code="missing_truthful_applicant_data")
    halt_expected = {
        "schema": "taey_apply_greenhouse_action_decision_v1",
        "action": "halt",
        "ref": None,
        "revision": None,
        "fact_key": None,
        "work_evidence_keys": [],
        "expected_option_name": None,
        "stop_code": "missing_truthful_applicant_data",
    }
    halt_client = decision_client(
        root,
        "schema-halt-positive",
        DecisionTransport(canonical_json_bytes(halt_candidate).decode()),
    )
    require(
        halt_client.decide(
            context,
            event_id="event-halt-positive",
            correlation_id="correlation-halt-positive",
        )
        == halt_expected,
        "halt projection drifted",
    )
    upload_surface = form_capsule(
        identity,
        digest("executor-upload-candidate"),
        [
            {
                "ref": ref(6),
                "name": "Resume",
                "role": "push button",
                "operations": ["open_upload"],
                "artifact_slot": "resume",
            }
        ],
        required_complete=False,
    )
    upload_context = GreenhouseDecisionContext(
        identity, upload_surface, (), (), "observe_form"
    )
    upload_candidate = candidate("open_upload", control_ref=ref(6))
    upload_expected = decision(
        "open_upload", ref(6), str(upload_surface["revision"]), None
    )
    upload_client = decision_client(
        root,
        "schema-upload-positive",
        DecisionTransport(canonical_json_bytes(upload_candidate).decode()),
    )
    require(
        upload_client.decide(
            upload_context,
            event_id="event-upload-positive",
            correlation_id="correlation-upload-positive",
        )
        == upload_expected,
        "nonfact projection drifted",
    )
    native_surface = native_capsule(
        identity,
        digest("executor-native-positive"),
        "chooser_widget",
        native_ref(6),
    )
    native_context = GreenhouseDecisionContext(
        identity, native_surface, (), (), "open_upload"
    )
    native_candidate = candidate("chooser_location", control_ref=native_ref(6))
    native_expected = decision(
        "chooser_location", native_ref(6), str(native_surface["revision"]), None
    )
    native_client = decision_client(
        root,
        "schema-native-positive",
        DecisionTransport(canonical_json_bytes(native_candidate).decode()),
    )
    require(
        native_client.decide(
            native_context,
            event_id="event-native-positive",
            correlation_id="correlation-native-positive",
        )
        == native_expected,
        "native projection drifted",
    )

    invalid_candidates: list[tuple[str, GreenhouseDecisionContext, dict[str, Any]]] = []
    for suffix, field, value in (
        ("halt-ref", "ref", ref(1)),
        ("halt-revision", "revision", str(capsule["revision"])),
        ("halt-fact", "fact_key", "full_name"),
    ):
        invalid = dict(halt_candidate)
        invalid[field] = value
        invalid_candidates.append((suffix, context, invalid))
    invalid_candidates.extend(
        [
            (
                "halt-no-stop",
                context,
                candidate("halt"),
            ),
            (
                "nonhalt-stop",
                context,
                {**exact_candidate, "stop_code": "unmapped_ui_or_question"},
            ),
            (
                "nonhalt-revision",
                context,
                {**exact_candidate, "revision": str(capsule["revision"])},
            ),
            (
                "option-ref",
                option_context,
                {**option_candidate, "ref": ref(2)},
            ),
            (
                "option-name",
                option_context,
                {**option_candidate, "expected_option_name": "private"},
            ),
            (
                "fact-missing",
                context,
                candidate("fill", control_ref=ref(1)),
            ),
            (
                "nonfact-fact",
                upload_context,
                {**upload_candidate, "fact_key": "full_name"},
            ),
            (
                "model-option-name",
                context,
                {**exact_candidate, "expected_option_name": "private"},
            ),
            (
                "work-evidence-duplicate",
                context,
                {**exact_candidate, "work_evidence_keys": ["automation", "automation"]},
            ),
            (
                "work-evidence-unknown",
                context,
                {**exact_candidate, "work_evidence_keys": ["unknown"]},
            ),
            (
                "form-action-absent",
                context,
                candidate("submit", control_ref=ref(1)),
            ),
            (
                "native-action-wrong",
                native_context,
                candidate("chooser_confirm", control_ref=native_ref(6)),
            ),
            (
                "native-ref-wrong",
                native_context,
                candidate("chooser_location", control_ref=native_ref(7)),
            ),
            (
                "extra-field",
                context,
                {**exact_candidate, "extra": "field"},
            ),
        ]
    )
    for suffix, candidate_context, invalid in invalid_candidates:
        require_candidate_refusal(
            root,
            candidate_context,
            canonical_json_bytes(invalid).decode(),
            f"schema-negative-{suffix}",
            expected_code=(
                "decision_cross_field_malformed"
                if suffix
                in {"form-action-absent", "native-action-wrong", "native-ref-wrong"}
                else "decision_fields_malformed"
            ),
        )
    duplicate_json = (
        '{"schema":"taey_apply_greenhouse_action_candidate_v2",'
        '"schema":"taey_apply_greenhouse_action_candidate_v2",'
        f'"action":"fill","ref":"{ref(1)}","fact_key":"full_name"}}'
    )
    require_candidate_refusal(
        root,
        context,
        duplicate_json,
        "schema-negative-duplicate-json",
        expected_code="decision_response_content_malformed",
    )
    empty_option_context = GreenhouseDecisionContext(
        identity, options, (), (), "open_combo"
    )
    require_candidate_refusal(
        root,
        empty_option_context,
        canonical_json_bytes(option_candidate).decode(),
        "schema-negative-empty-options",
        expected_calls=0,
        expected_code="decision_transport_failure",
    )
    unknown_operation_surface = form_capsule(
        identity,
        digest("executor-unknown-operation"),
        [
            {
                "ref": ref(7),
                "name": "Unknown",
                "role": "push button",
                "operations": ["coordinate_click"],
            }
        ],
        required_complete=False,
    )
    unknown_schema = _decision_schema(
        GreenhouseDecisionContext(
            identity, unknown_operation_surface, ("full_name",), (), "observe_form"
        )
    )
    require(
        [branch["properties"]["action"]["const"] for branch in unknown_schema["oneOf"]]
        == ["halt"],
        "unknown form operation became model authority",
    )
    ambiguous_native = dict(native_surface)
    ambiguous_native["mapped"] = {
        "chooser_widget": [
            *native_surface["mapped"]["chooser_widget"],
            {
                **native_surface["mapped"]["chooser_widget"][0],
                "ref": native_ref(7),
            },
        ]
    }
    ambiguous_schema = _decision_schema(
        GreenhouseDecisionContext(identity, ambiguous_native, (), (), "open_upload")
    )
    require(
        [
            branch["properties"]["action"]["const"]
            for branch in ambiguous_schema["oneOf"]
        ]
        == ["halt"],
        "ambiguous native ref became model authority",
    )
    prose = decision_client(
        root,
        "schema-prose",
        DecisionTransport("choose the first field"),
    )
    try:
        prose.decide(
            context, event_id="event-prose", correlation_id="correlation-prose"
        )
    except ApplicationExecutorError as exc:
        require(
            exc.failure_code == "unmapped_ui_or_question", "prose refusal code drifted"
        )
        require(
            exc.decision_rejection_code
            == "decision_response_content_malformed",
            "prose rejection class drifted",
        )
    else:
        raise RuntimeError("model prose was accepted")


def first_error_cases(
    root: Path, envelope: Any, envelope_sha256: str, capsule: Mapping[str, Any]
) -> None:
    source = FrozenDecisionSource(
        decision("fill", ref(1), str(capsule["revision"]), "full_name")
    )
    refused_transport = PresenceTransport(capsule, refusal=True)
    refused = executor(root, capsule, source, refused_transport, "executor-refused")
    request = OneActionRequest(envelope, envelope_sha256, 1, None)
    terminal = refused(request)
    require(
        terminal.state == "terminal_halt"
        and terminal.stop_code == "exact_postcondition_failure",
        "observe refusal did not terminalize",
    )
    evidence, evidence_path = terminal_evidence(root, terminal)
    require(
        evidence["stage"] == "presence"
        and evidence["reason_code"] == "presence_observation_refused"
        and isinstance(evidence["presence_response_payload_sha256"], str)
        and len(evidence["presence_response_payload_sha256"]) == 64,
        "Presence refusal evidence drifted",
    )
    try:
        write_new_private_json(evidence_path, dict(evidence))
    except IntakeContractError:
        pass
    else:
        raise RuntimeError("terminal evidence identity was overwritten")
    try:
        refused(request)
    except ApplicationExecutorError:
        pass
    else:
        raise RuntimeError("terminal executor accepted a later call")
    require(len(refused_transport.calls) == 1, "terminal executor retried")

    wrong_transport = PresenceTransport(capsule, wrong_second_header=True)
    uncertain = executor(root, capsule, source, wrong_transport, "executor-uncertain")
    observed = uncertain(OneActionRequest(envelope, envelope_sha256, 1, None))
    try:
        uncertain(
            OneActionRequest(envelope, envelope_sha256, 2, observed.receipt_sha256)
        )
    except ApplicationExecutorError as exc:
        require(
            exc.failure_code == "side_effect_uncertainty",
            "uncertain receipt code drifted",
        )
    else:
        raise RuntimeError("wrong Presence lineage was accepted")
    require(len(wrong_transport.calls) == 2, "uncertain executor retried")


def assert_decision_rejection(
    root: Path,
    envelope: Any,
    envelope_sha256: str,
    capsule: Mapping[str, Any],
    *,
    seat: str,
    transport: DecisionTransport,
    expected_rejection_code: str,
) -> None:
    client = decision_client(root, seat, transport)
    presence = PresenceTransport(capsule)
    instance = executor(root, capsule, client, presence, seat)
    observed = instance(OneActionRequest(envelope, envelope_sha256, 1, None))
    terminal_request = OneActionRequest(
        envelope, envelope_sha256, 2, observed.receipt_sha256
    )
    terminal = instance(terminal_request)
    validate_terminal_outcome_evidence(root, terminal, terminal_request)
    evidence, _ = terminal_evidence(root, terminal)
    response_path = root / str(evidence["decision_response_ref"])
    response_bytes = response_path.read_bytes()
    response_evidence = json.loads(response_bytes)
    require(
        terminal.state == "terminal_halt"
        and evidence["stage"] == "decision"
        and evidence["reason_code"] == "decision_source_refused"
        and evidence["decision_rejection_code"] == expected_rejection_code
        and evidence["decision_response_ref"] == client.last_response_ref
        and evidence["decision_response_sha256"] == client.last_response_sha256
        and evidence["decision_response_payload_sha256"]
        == client.last_response_payload_sha256
        and evidence["accepted_decision_ref"] is None
        and evidence["capsule_sha256"] == digest(canonical_json_bytes(capsule))
        and response_bytes == canonical_json_bytes(response_evidence)
        and digest(response_bytes) == evidence["decision_response_sha256"]
        and stat.S_IMODE(response_path.stat().st_mode) == 0o400
        and response_evidence["schema"]
        == "taey_apply_application_executor_decision_response_v1"
        and response_evidence["application_identity_sha256"]
        == envelope.application_identity_sha256
        and response_evidence["seat_id"] == seat
        and response_evidence["event_id"] == f"{seat}-event.s2"
        and response_evidence["correlation_id"] == f"{seat}-correlation.s2"
        and response_evidence["request_payload_sha256"]
        == digest(canonical_json_bytes(transport.calls[0]["payload"]))
        and response_evidence["response_payload_sha256"]
        == digest(canonical_json_bytes(transport.last_body))
        and response_evidence["response_payload"] == transport.last_body
        and len(presence.calls) == 1
        and PRIVATE_SENTINEL.encode() not in response_bytes
        and str(root).encode() not in response_bytes,
        f"{expected_rejection_code} did not preserve exact first-error evidence",
    )
    try:
        write_new_private_json(response_path, response_evidence)
    except IntakeContractError:
        pass
    else:
        raise RuntimeError("decision response identity was overwritten")


def forensic_terminal_cases(
    root: Path, envelope: Any, envelope_sha256: str, capsule: Mapping[str, Any]
) -> None:
    incomplete = candidate("fill", control_ref=ref(1))
    cross_field = candidate("fill", control_ref=ref(1), fact_key="unknown")
    rejection_cases = (
        (
            "executor-envelope-refusal",
            DecisionTransport(None, body={"choices": []}),
            "decision_response_envelope_malformed",
        ),
        (
            "executor-content-refusal",
            DecisionTransport("not one schema decision"),
            "decision_response_content_malformed",
        ),
        (
            "executor-fields-refusal",
            DecisionTransport(canonical_json_bytes(incomplete).decode()),
            "decision_fields_malformed",
        ),
        (
            "executor-cross-field-refusal",
            DecisionTransport(canonical_json_bytes(cross_field).decode()),
            "decision_cross_field_malformed",
        ),
    )
    for seat, transport, rejection_code in rejection_cases:
        assert_decision_rejection(
            root,
            envelope,
            envelope_sha256,
            capsule,
            seat=seat,
            transport=transport,
            expected_rejection_code=rejection_code,
        )

    choice_capsule = form_capsule(
        envelope.application_identity_sha256,
        digest("compiler-choice-refusal"),
        [
            {
                "ref": ref(1),
                "name": "Consent",
                "role": "check box",
                "operations": ["activate_choice"],
            }
        ],
        required_complete=False,
    )
    choice_candidate = candidate(
        "activate_choice", control_ref=ref(1), fact_key="full_name"
    )
    projected_choice = decision(
        "activate_choice", ref(1), str(choice_capsule["revision"]), "full_name"
    )
    choice_transport = DecisionTransport(
        canonical_json_bytes(choice_candidate).decode()
    )
    compiler_presence = PresenceTransport(choice_capsule)
    compiler_executor = executor(
        root,
        choice_capsule,
        decision_client(root, "executor-compiler-refusal", choice_transport),
        compiler_presence,
        "executor-compiler-refusal",
    )
    observed = compiler_executor(
        OneActionRequest(envelope, envelope_sha256, 1, None)
    )
    compiler_request = OneActionRequest(
        envelope, envelope_sha256, 2, observed.receipt_sha256
    )
    terminal = compiler_executor(compiler_request)
    validate_terminal_outcome_evidence(root, terminal, compiler_request)
    evidence, _ = terminal_evidence(root, terminal)
    decision_path = root / str(evidence["accepted_decision_ref"])
    decision_bytes = decision_path.read_bytes()
    require(
        terminal.stop_code == "missing_truthful_applicant_data"
        and evidence["stage"] == "compile"
        and evidence["reason_code"] == "compiler_refused"
        and digest(decision_bytes) == evidence["accepted_decision_sha256"]
        and json.loads(decision_bytes) == projected_choice
        and stat.S_IMODE(decision_path.stat().st_mode) == 0o400
        and isinstance(evidence["decision_response_ref"], str)
        and evidence["decision_response_sha256"] is not None
        and evidence["decision_rejection_code"] is None
        and evidence["capsule_sha256"] == digest(canonical_json_bytes(choice_capsule))
        and len(compiler_presence.calls) == 1
        and PRIVATE_SENTINEL.encode() not in decision_bytes,
        "compiler-stage evidence did not bind the exact accepted decision",
    )

    halt = decision("halt", None, None, None)
    halt["stop_code"] = "unmapped_ui_or_question"
    halt_candidate = candidate("halt", stop_code="unmapped_ui_or_question")
    halt_transport = DecisionTransport(canonical_json_bytes(halt_candidate).decode())
    halt_presence = PresenceTransport(capsule)
    halt_executor = executor(
        root,
        capsule,
        decision_client(root, "executor-explicit-halt", halt_transport),
        halt_presence,
        "executor-explicit-halt",
    )
    observed = halt_executor(OneActionRequest(envelope, envelope_sha256, 1, None))
    halt_request = OneActionRequest(
        envelope, envelope_sha256, 2, observed.receipt_sha256
    )
    terminal = halt_executor(halt_request)
    validate_terminal_outcome_evidence(root, terminal, halt_request)
    evidence, _ = terminal_evidence(root, terminal)
    halt_decision_path = root / str(evidence["accepted_decision_ref"])
    require(
        terminal.stop_code == "unmapped_ui_or_question"
        and evidence["stage"] == "compile"
        and evidence["reason_code"] == "taey_explicit_halt"
        and json.loads(halt_decision_path.read_bytes()) == halt
        and digest(halt_decision_path.read_bytes())
        == evidence["accepted_decision_sha256"]
        and stat.S_IMODE(halt_decision_path.stat().st_mode) == 0o400
        and isinstance(evidence["decision_response_ref"], str)
        and evidence["decision_response_sha256"] is not None
        and evidence["decision_rejection_code"] is None
        and len(halt_presence.calls) == 1,
        "explicit Taey halt was conflated with compiler refusal",
    )
    try:
        write_new_private_json(halt_decision_path, halt)
    except IntakeContractError:
        pass
    else:
        raise RuntimeError("accepted decision identity was overwritten")


def static_boundary() -> None:
    source = (ROOT / "src/taey_apply/application_executor.py").read_text()
    forbidden = (
        "human_review_required",
        "approval_required",
        "human_approval",
        "review_queue",
        "taeys_hands",
        "subprocess",
        "requests",
        "httpx",
    )
    leaked = [token for token in forbidden if token in source]
    require(not leaked, f"executor boundary leaked: {leaked}")
    require(
        source.count("self._presence_transport.post(") == 1,
        "Presence transport site duplicated",
    )


def endpoint_pair_boundary(root: Path) -> None:
    transports: list[int] = []
    application_calls: list[Mapping[str, Any]] = []

    class TrackingTransport:
        def __init__(self, *, timeout_seconds: int) -> None:
            transports.append(timeout_seconds)

    def frozen_args(decision_endpoint: str, presence_endpoint: str) -> argparse.Namespace:
        return argparse.Namespace(
            private_root=str(root),
            envelope_file=str(root / "unused-envelope.json"),
            envelope_sha256="0" * 64,
            seat_id="executor-endpoint-pair",
            display=":17",
            hands_commit=REQUIRED_HANDS_COMMIT,
            event_id="executor-endpoint-pair-event",
            correlation_id="executor-endpoint-pair-correlation",
            taey_decision_endpoint=decision_endpoint,
            taey_model="taey-production",
            presence_endpoint=presence_endpoint,
            decision_timeout_seconds=181,
            presence_timeout_seconds=301,
        )

    def accept_application(**values: Any) -> dict[str, bool]:
        application_calls.append(values)
        return {"ok": True}

    original_transport = application_execute_cli.SingleRequestJsonTransport
    original_runner = application_execute_cli.run_application
    application_execute_cli.SingleRequestJsonTransport = TrackingTransport
    application_execute_cli.run_application = accept_application
    try:
        try:
            application_execute_cli.run(
                frozen_args(
                    "https://shared.invalid/v1/chat/completions",
                    "https://shared.invalid/v1/greenhouse-ats/one-action",
                )
            )
        except ApplicationExecutorError as exc:
            require(
                exc.failure_code == "policy_or_authority_boundary",
                "same-origin refusal code drifted",
            )
        else:
            raise RuntimeError("same-origin decision and Presence pair was accepted")
        require(
            transports == [] and application_calls == [],
            "same-origin pair reached transport construction",
        )
        require(
            application_execute_cli.run(
                frozen_args(DECISION_ENDPOINT, PRESENCE_ENDPOINT)
            )
            == {"ok": True}
            and transports == [181, 301]
            and len(application_calls) == 1,
            "distinct decision and Presence pair did not construct",
        )
    finally:
        application_execute_cli.SingleRequestJsonTransport = original_transport
        application_execute_cli.run_application = original_runner


if __name__ == "__main__":
    static_boundary()
    with tempfile.TemporaryDirectory(prefix="taey-apply-executor-") as temp:
        private_root = Path(temp)
        private_root.chmod(0o700)
        endpoint_pair_boundary(private_root)
        envelope, envelope_sha256, identity = envelope_fixture(private_root)
        capsule = form_capsule(
            identity,
            digest("executor-surface"),
            [
                {
                    "ref": ref(1),
                    "name": "Full name",
                    "role": "entry",
                    "operations": ["fill"],
                    "is_empty": True,
                }
            ],
            required_complete=False,
        )
        success_case(private_root, envelope, envelope_sha256, identity, capsule)
        schema_case(private_root, identity, capsule)
        first_error_cases(private_root, envelope, envelope_sha256, capsule)
        forensic_terminal_cases(
            private_root, envelope, envelope_sha256, capsule
        )
    print(
        json.dumps(
            {
                "schema": "taey_apply_application_executor_validation_v1",
                "status": "PASS",
                "production_mutations": 0,
                "network_calls": 0,
                "schema_constrained_decisions": 2,
                "private_exact_option_resolutions": 1,
                "empty_tool_call_lists_accepted": 1,
                "nonempty_tool_call_lists_accepted": 0,
                "model_prose_accepted": 0,
                "private_values_in_decision_context": 0,
                "private_paths_in_decision_context": 0,
                "presence_body_fields": ["display"],
                "presence_calls_after_first_error": 0,
                "same_origin_endpoint_pairs_accepted": 0,
                "distinct_endpoint_pairs_constructed": 1,
                "terminal_evidence_artifacts": 7,
                "accepted_decision_artifacts": 2,
                "decision_response_artifacts": 11,
                "malformed_response_classes_proven": 4,
                "human_review_states": 0,
            },
            sort_keys=True,
        )
    )
