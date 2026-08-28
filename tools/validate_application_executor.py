#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict
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
)
from taey_apply.application_executor import (  # noqa: E402
    ApplicationExecutorError,
    GreenhousePresenceOneActionExecutor,
    JsonHttpResponse,
    TaeyJsonSchemaDecisionClient,
)
from taey_apply.application_materializer import materialize_application_context  # noqa: E402
from taey_apply.application_preparer import prepare_application  # noqa: E402
from taey_apply.contract import (  # noqa: E402
    IntakeContractError,
    canonical_json_bytes,
    write_new_private_json,
)
from validate_application_action_compiler import (  # noqa: E402
    form_capsule,
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
    def __init__(self, content: str, *, tool_calls: object = None) -> None:
        self.content = content
        self.tool_calls = tool_calls
        self.calls: list[dict[str, Any]] = []

    def post(
        self, endpoint: str, *, headers: Mapping[str, str], payload: Mapping[str, Any]
    ) -> JsonHttpResponse:
        self.calls.append(
            {"endpoint": endpoint, "headers": dict(headers), "payload": dict(payload)}
        )
        body = {
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
        return JsonHttpResponse(200, {}, body, digest(canonical_json_bytes(body)))


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


def schema_case(identity: str, capsule: Mapping[str, Any]) -> None:
    exact = decision("fill", ref(1), str(capsule["revision"]), "full_name")
    transport = DecisionTransport(canonical_json_bytes(exact).decode())
    client = TaeyJsonSchemaDecisionClient(
        endpoint_value=DECISION_ENDPOINT,
        model_value="taey-production",
        transport=transport,
    )
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
                                "content": canonical_json_bytes(exact).decode(),
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
        canonical_json_bytes(exact).decode(), tool_calls=[]
    )
    empty_tool_calls_client = TaeyJsonSchemaDecisionClient(
        endpoint_value=DECISION_ENDPOINT,
        model_value="taey-production",
        transport=empty_tool_calls,
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
        canonical_json_bytes(exact).decode(),
        tool_calls=[{"id": "call_1", "type": "function"}],
    )
    nonempty_tool_calls_client = TaeyJsonSchemaDecisionClient(
        endpoint_value=DECISION_ENDPOINT,
        model_value="taey-production",
        transport=nonempty_tool_calls,
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
    else:
        raise RuntimeError("nonempty tool-call list was accepted")
    require(len(nonempty_tool_calls.calls) == 1, "nonempty tool-call decision retried")
    payload = transport.calls[0]["payload"]
    response_format = payload["response_format"]
    work_evidence_schema = response_format["json_schema"]["schema"]["properties"][
        "work_evidence_keys"
    ]
    require(
        response_format["type"] == "json_schema"
        and response_format["json_schema"]["strict"] is True
        and "uniqueItems" not in work_evidence_schema
        and payload["chat_template_kwargs"] == {"enable_thinking": False}
        and "tools" not in payload,
        "native schema contract drifted",
    )
    require(
        PRIVATE_SENTINEL.encode() not in canonical_json_bytes(payload),
        "private value reached schema request",
    )
    option_revision = digest("executor-options")
    options = options_capsule(identity, option_revision, ref(2), ref(3))
    option_value = decision("select_option", None, option_revision, "country")
    option_transport = DecisionTransport(canonical_json_bytes(option_value).decode())
    option_client = TaeyJsonSchemaDecisionClient(
        endpoint_value=DECISION_ENDPOINT,
        model_value="taey-production",
        transport=option_transport,
    )
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
    require(
        option_schema["properties"]["action"] == {"const": "select_option"}
        and option_schema["properties"]["ref"] == {"type": "null"}
        and option_schema["properties"]["expected_option_name"] == {"type": "null"},
        "private option-resolution schema widened",
    )
    prose = TaeyJsonSchemaDecisionClient(
        endpoint_value=DECISION_ENDPOINT,
        model_value="taey-production",
        transport=DecisionTransport("choose the first field"),
    )
    try:
        prose.decide(
            context, event_id="event-prose", correlation_id="correlation-prose"
        )
    except ApplicationExecutorError as exc:
        require(
            exc.failure_code == "unmapped_ui_or_question", "prose refusal code drifted"
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


def forensic_terminal_cases(
    root: Path, envelope: Any, envelope_sha256: str, capsule: Mapping[str, Any]
) -> None:
    decision_transport = DecisionTransport("not one schema decision")
    decision_client = TaeyJsonSchemaDecisionClient(
        endpoint_value=DECISION_ENDPOINT,
        model_value="taey-production",
        transport=decision_transport,
    )
    decision_presence = PresenceTransport(capsule)
    decision_executor = executor(
        root,
        capsule,
        decision_client,
        decision_presence,
        "executor-decision-refusal",
    )
    observed = decision_executor(
        OneActionRequest(envelope, envelope_sha256, 1, None)
    )
    terminal = decision_executor(
        OneActionRequest(envelope, envelope_sha256, 2, observed.receipt_sha256)
    )
    evidence, _ = terminal_evidence(root, terminal)
    require(
        terminal.state == "terminal_halt"
        and evidence["stage"] == "decision"
        and evidence["reason_code"] == "decision_source_refused"
        and evidence["decision_response_payload_sha256"]
        == decision_client.last_response_payload_sha256
        and evidence["accepted_decision_ref"] is None
        and evidence["capsule_sha256"] == digest(canonical_json_bytes(capsule))
        and len(decision_presence.calls) == 1,
        "decision-stage evidence did not reconstruct first error",
    )

    stale = decision("fill", ref(1), digest("stale-revision"), "full_name")
    compiler_presence = PresenceTransport(capsule)
    compiler_executor = executor(
        root,
        capsule,
        FrozenDecisionSource(stale),
        compiler_presence,
        "executor-compiler-refusal",
    )
    observed = compiler_executor(
        OneActionRequest(envelope, envelope_sha256, 1, None)
    )
    terminal = compiler_executor(
        OneActionRequest(envelope, envelope_sha256, 2, observed.receipt_sha256)
    )
    evidence, _ = terminal_evidence(root, terminal)
    decision_path = root / str(evidence["accepted_decision_ref"])
    decision_bytes = decision_path.read_bytes()
    require(
        terminal.stop_code == "exact_postcondition_failure"
        and evidence["stage"] == "compile"
        and evidence["reason_code"] == "compiler_refused"
        and digest(decision_bytes) == evidence["accepted_decision_sha256"]
        and json.loads(decision_bytes) == stale
        and stat.S_IMODE(decision_path.stat().st_mode) == 0o400
        and evidence["capsule_sha256"] == digest(canonical_json_bytes(capsule))
        and len(compiler_presence.calls) == 1
        and PRIVATE_SENTINEL.encode() not in decision_bytes,
        "compiler-stage evidence did not bind the exact accepted decision",
    )

    halt = decision("halt", None, None, None)
    halt["stop_code"] = "unmapped_ui_or_question"
    halt_presence = PresenceTransport(capsule)
    halt_executor = executor(
        root,
        capsule,
        FrozenDecisionSource(halt),
        halt_presence,
        "executor-explicit-halt",
    )
    observed = halt_executor(OneActionRequest(envelope, envelope_sha256, 1, None))
    terminal = halt_executor(
        OneActionRequest(envelope, envelope_sha256, 2, observed.receipt_sha256)
    )
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


if __name__ == "__main__":
    static_boundary()
    with tempfile.TemporaryDirectory(prefix="taey-apply-executor-") as temp:
        private_root = Path(temp)
        private_root.chmod(0o700)
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
        schema_case(identity, capsule)
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
                "terminal_evidence_artifacts": 4,
                "accepted_decision_artifacts": 2,
                "human_review_states": 0,
            },
            sort_keys=True,
        )
    )
